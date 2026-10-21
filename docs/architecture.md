# Architecture and data model

## Scan pipeline

```text
target domain
     │
     ▼
CT discovery (crt.sh → Cert Spotter fallback)
     │ normalized, deduplicated subdomains
     ▼
Concurrent DNS resolution (A + AAAA)
     │ active assets only
     ▼
SQLite state comparison and update
     │
     ├── terminal output / JSON
     └── optional Discord-compatible webhook for new hostnames
```

`main.py` owns orchestration and CLI validation. Discovery returns hostnames;
resolution turns them into assets shaped like:

```json
{
  "hostname": "api.example.com",
  "addresses": ["203.0.113.10", "2001:db8::10"]
}
```

## Discovery and resolution

`core.discovery.discover_subdomains()` tries `crt.sh` twice, then Cert Spotter
twice if requests fail. A successful empty response is returned as empty rather
than merged with another provider. Names are lowercased, trailing dots and an
initial wildcard are removed, and only names ending in `.<target>` are kept.

`core.resolver` checks both `A` and `AAAA`. Record-level DNS errors are ignored
so one unavailable family does not discard a valid answer from the other. The
`--threads` value is an asyncio concurrency limit, not a thread-pool size.

## SQLite schema

```sql
CREATE TABLE assets (
    target TEXT NOT NULL,
    hostname TEXT NOT NULL,
    addresses TEXT NOT NULL,       -- JSON array of strings
    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (target, hostname)
);
```

Existing rows receive updated addresses and `last_seen` timestamps. New rows are
returned to the caller and can trigger a webhook. Disappeared hostnames remain
as historical records; this is not a current-state inventory.

## Extension points

Add CT providers in `core/discovery.py`, record types in `core/resolver.py`, or
notification providers beside `utils/webhook.py`. Add schema migrations before
changing the SQLite structure used by existing databases.
