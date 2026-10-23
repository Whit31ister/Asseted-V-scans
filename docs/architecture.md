# Architecture and data model

## Scan pipeline

```text
target domain
     │
     ▼
Passive discovery (crt.sh, Cert Spotter, OTX, HackerTarget)
     │ each source retried independently; results merged and deduplicated
     ▼
Concurrent DNS resolution (A + AAAA)
     │ active assets only
     ▼
Optional HTTPS/HTTP metadata probing
     │ status, final URL, title, Server header
     ▼
SQLite state comparison and update
     │
     ├── terminal output / JSON
     └── optional Discord or Telegram notification
```

`main.py` owns orchestration and CLI validation. Discovery returns hostnames;
resolution turns them into assets shaped like:

```json
{
  "hostname": "api.example.com",
  "addresses": ["203.0.113.10", "2001:db8::10"]
}
```

The JSON response always contains a `probes` object keyed by hostname; it is
empty unless `--probe` is enabled. Each value contains `url`, `status`, `title`,
and `server`.

## Discovery and resolution

`core.discovery.discover_subdomains()` queries four independent passive sources:
`crt.sh`, Cert Spotter, AlienVault OTX passive DNS, and HackerTarget. Each
source is attempted twice. Successful responses are merged, deduplicated, and
sorted. The scan fails only when all sources fail; a successful empty response
from one source does not suppress results from other sources.

Names are lowercased, trailing dots and an initial wildcard are removed, and
only names ending in `.<target>` are kept. Provider-specific parsers are unit
tested without network access.

`core.resolver` checks both `A` and `AAAA`. Record-level DNS errors are ignored
so one unavailable family does not discard a valid answer from the other. The
`--threads` value is an asyncio concurrency limit, not a thread-pool size.

`core.prober` reuses one HTTP session and checks HTTPS before HTTP for every
active hostname. It follows redirects, reads at most 256 KiB, extracts a
normalized title, and records the response's `Server` header. Individual probe
errors are ignored.

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
returned to the caller and can trigger notifications. Probe results are not
persisted in SQLite. Disappeared hostnames remain as historical records; this
is not a current-state inventory.

## Extension points

Add passive providers in `core/discovery.py`, record types in
`core/resolver.py`, probe metadata in `core/prober.py`, or notification
providers beside `utils/webhook.py`. Add schema migrations before changing the
SQLite structure used by existing databases.
