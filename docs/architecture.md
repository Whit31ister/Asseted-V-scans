# Architecture and data model

## Overview

Asset Monitor is a Python 3.10+ application with two entry points:

| Entry point | Purpose |
| --- | --- |
| `main.py` | Command-line scans and scheduled execution |
| `gui.py` | Local FastAPI dashboard and HTTP API |

Both entry points use the shared scan pipeline in `core/scanner.py`. The CLI
prints results to standard output. The web interface runs scans as background
jobs and streams progress to a browser over Server-Sent Events (SSE).

```text
CLI (main.py) ───────────────┐
                             ├── core.scanner.run_scan()
Web API (web/app.py) ────────┤          │
  └── JobManager (web/jobs.py)          ├── core.discovery
                                        ├── core.resolver
                                        ├── core.prober
                                        ├── database.state.AssetStore
                                        └── utils.webhook
```

## Components

| Component | Responsibility |
| --- | --- |
| `core/scanner.py` | Input normalization, pipeline orchestration, progress events, state updates, notification delivery, and scan-run completion |
| `core/discovery.py` | Queries passive sources, retries each source independently, parses provider responses, and merges results |
| `core/resolver.py` | Resolves `A` and `AAAA` records with a concurrency limit and reports per-host progress |
| `core/prober.py` | Requests active hosts over HTTPS and HTTP, extracts metadata, and isolates per-host failures |
| `database/state.py` | Creates and reads SQLite tables, records asset changes, and stores scan-run history |
| `utils/webhook.py` | Formats and sends Discord and Telegram notifications |
| `web/app.py` | Defines FastAPI routes, validates requests, serves static assets, and produces SSE responses |
| `web/jobs.py` | Owns the active job, event buffer, cancellation, log capture, and retention |
| `web/static/` | Implements the dashboard without a build step |

## Scan pipeline

```text
Target domain
    │
    ▼
Input normalization and validation
    │
    ▼
Scan-run record created with status "running"
    │
    ▼
Passive discovery
    │ four sources, each attempted twice
    ▼
DNS resolution
    │ A and AAAA, concurrency-limited
    ▼
Optional HTTP/HTTPS probing
    │ status, final URL, title, Server header
    ▼
SQLite change detection and update
    │
    ├── CLI output or JSON
    └── Optional Discord/Telegram notification
    │
    ▼
Scan-run record completed, failed, or cancelled
```

### Input validation

`core.scanner.normalize_target()` performs the following steps:

1. Trims whitespace, converts the value to lowercase, and removes a trailing
   dot.
2. Converts internationalized domain names to ASCII using IDNA.
3. Validates the result as a DNS name with at least one dot and labels of 1 to 63
   characters.
4. Rejects IP addresses and values that do not match the domain pattern.

`ScanOptions` also rejects a non-positive concurrency value, a non-positive
timeout, and an empty database path.

### Discovery

`core.discovery.discover_subdomains()` queries four independent sources:
`crt.sh`, Cert Spotter, AlienVault OTX, and HackerTarget. Each source is
attempted twice. Successful responses are normalized, filtered to the target
domain, merged, deduplicated, and sorted.

A source failure is isolated from the other sources. The scan fails only when all
four sources fail. A successful empty response from one source does not suppress
results from another source.

### Resolution

`core.resolver.resolve_subdomains()` checks both `A` and `AAAA` records. A DNS
error for one record family does not discard a valid answer from the other. The
`--threads` value is an asyncio semaphore limit, not a thread-pool size. Only
hostnames with at least one address are returned as assets.

Asset objects have the following shape:

```json
{
  "hostname": "api.example.com",
  "addresses": ["203.0.113.10", "2001:db8::10"]
}
```

### Probing

`core.prober.probe_assets()` reuses one HTTP session and probes each hostname over
HTTPS and then HTTP. It follows redirects, reads at most 256 KiB per response,
extracts a normalized title, and records the response `Server` header. Probe
failures are ignored for the affected URL and do not fail the scan.

Probe results are keyed by hostname. Each value is a list of entries with `url`,
`status`, `title`, and `server`.

### State update and notifications

The pipeline resolves the known-hostname set before discovery, then calls
`AssetStore.record_scan()` after probing. The store inserts new `(target,
hostname)` rows and updates addresses and `last_seen` for existing rows. The
returned new-asset list and probe results determine whether notifications are
sent.

Notifications are sent after the state update. If a notification fails, the
asset data remains saved. The failure is recorded in the scan result, written to
the scan-run record, and reported:

- the CLI prints the error and exits with status `2`;
- the dashboard reports a warning and keeps the completed result available.

The dashboard does not persist webhook URLs or bot tokens. Scan options exist in
server memory for the lifetime of the job and are not included in API snapshots.

## CLI design

`main.py` parses arguments, creates `ScanOptions`, and runs `run_scan()` with
`asyncio.run()`. Human-readable output lists active assets and probe details.
`--json` emits the target, assets, new assets, and probes. Logging uses the
standard `logging` module.

## Web job model

`web.jobs.JobManager` allows one active scan per server process.

- `POST /api/scans` creates a job ID and starts an asyncio task. The task sets
  the job status to `running` when it begins execution.
- A second scan request returns HTTP `409` while the first job is active.
- `POST /api/scans/{id}/cancel` cancels the task and waits for it to finish. The
  pipeline records a `cancelled` scan-run status when cancellation occurs before
  completion.
- Completed, failed, and cancelled jobs remain in memory for 3,600 seconds.
- At most 50 terminal jobs are retained. Older terminal jobs are pruned first.
- Job events are stored in a bounded 2,000-event buffer.
- Scan module logs are attached to the job for the duration of the run and then
  removed. Verbose mode lowers the captured log level to `DEBUG`.

The pipeline invokes synchronous progress callbacks for discovery sources, DNS
results, probe results, phases, notifications, and logs. The job manager converts
these into numbered events that can be replayed to a reconnecting client.

## Event stream

`GET /api/scans/{id}/events` returns `text/event-stream` data. Each event has an
ID, an event name, and JSON data. The client may resume with either the
`last_event_id` query parameter or the `Last-Event-ID` header; the header takes
precedence when both are present.

The stream sends a keepalive comment every 15 seconds while a job is active and
ends after a terminal event. The browser uses `EventSource` and its automatic
reconnection. If the stream is interrupted, the dashboard polls the job snapshot
every two seconds until the stream reconnects or the job reaches a terminal
state.

Event names are:

- `status`
- `phase`
- `source`
- `progress`
- `asset`
- `probe`
- `log`
- `notification`
- `done`
- `error`
- `cancelled`

Payload details are documented in the [HTTP API reference](api.md).

## Data model

```sql
CREATE TABLE assets (
    target TEXT NOT NULL,
    hostname TEXT NOT NULL,
    addresses TEXT NOT NULL,       -- JSON array of strings
    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (target, hostname)
);

CREATE TABLE scan_runs (
    id TEXT PRIMARY KEY,           -- UUID hex string
    target TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL,          -- running, completed, failed, cancelled
    asset_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    probe_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX idx_scan_runs_target_started
    ON scan_runs (target, started_at DESC);
```

The database uses WAL mode. Read requests for history do not create a missing
database file. Existing asset rows receive updated addresses and `last_seen`;
new rows are returned to the caller. Disappeared hostnames remain in the table as
historical records.

Probe results are not stored in SQLite. The database therefore represents known
assets and scan metadata, not a complete current-state inventory.

The web process prevents overlapping web scans. Separate CLI processes do not
coordinate with the server. Avoid concurrent writers against the same database
when exact baseline ordering is required.

## Failure handling

- Passive source failures are isolated per source. The scan fails only if every
  source fails.
- DNS record-family failures are ignored when the other family returns an
  address.
- Probe failures do not fail the scan.
- Notification failures do not roll back saved assets. They are recorded and
  reported according to the interface.
- Scan failures and cancellations update `scan_runs` with a terminal status.
- Invalid API input returns HTTP `422`; a conflicting scan returns HTTP `409`;
  unknown job IDs return HTTP `404`.

## Security boundaries

The dashboard is a local, unauthenticated administrative interface. Its default
bind address is `127.0.0.1`. Binding to another interface exposes scan controls
and database history to anyone who can reach the port.

Request validation is enforced by the FastAPI layer and the shared scanner:

- Discord webhook URLs must use HTTPS and an approved Discord host and path.
- Telegram values must use the `BOT_TOKEN/CHAT_ID` format.
- Database paths must not be empty and must not refer to a directory.
- Target values must be valid domain names.
- Concurrency, timeout, result limits, and string lengths are bounded.

The frontend renders external data with DOM text nodes rather than HTML. CSV
export prefixes cells that begin with spreadsheet formula characters to reduce
CSV injection risk. Notification secrets are not stored in `localStorage`, SQLite,
logs, or API snapshots.

## Extension points

- Add passive providers in `core/discovery.py`.
- Add DNS record types in `core/resolver.py`.
- Add probe metadata in `core/prober.py`.
- Add notification providers beside `utils/webhook.py`.
- Add dashboard views in `web/static/` and corresponding routes in `web/app.py`.
- Add schema migrations before changing the SQLite structure used by existing
  databases.

## Related documentation

- [HTTP API reference](api.md)
- [Web interface guide](gui.md)
- [Operations and troubleshooting](operations.md)
