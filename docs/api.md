# HTTP API reference

## Overview

The dashboard and HTTP API are served by the FastAPI application in
`web/app.py`. The default base URL is:

```text
http://127.0.0.1:8000
```

The API has no authentication and is intended for local use. Keep the server
bound to a loopback address. Interactive OpenAPI documentation is available at
`/api/docs`.

All request and response bodies use JSON unless otherwise noted. Timestamps
stored in SQLite use UTC in `YYYY-MM-DD HH:MM:SS` format. Job timestamps use Unix
time in seconds with a fractional component.

## Endpoint summary

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Server version and active job ID |
| `POST` | `/api/scans` | Start a scan |
| `GET` | `/api/scans/active` | Return the active job or `null` |
| `GET` | `/api/scans/{job_id}` | Return a job snapshot |
| `GET` | `/api/scans/{job_id}/events` | Stream job events over SSE |
| `POST` | `/api/scans/{job_id}/cancel` | Cancel a job |
| `GET` | `/api/scans/{job_id}/export` | Export completed results |
| `GET` | `/api/history/targets` | List known targets |
| `GET` | `/api/history/runs` | List scan runs |
| `GET` | `/api/history/assets` | List tracked assets |

## Health

### `GET /api/health`

Returns server status and the active job ID, if any.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "active_job": null
}
```

| Field | Type | Description |
| --- | --- | --- |
| `status` | string | Always `ok` when the application is responding |
| `version` | string | Application version |
| `active_job` | string or null | ID of the active job |

## Start a scan

### `POST /api/scans`

Starts a scan and returns HTTP `202` with the job ID and current status.

Request fields:

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `target` | string | required | 1 to 253 characters; valid domain name, not an IP address |
| `threads` | integer | `50` | 1 to 500 |
| `timeout` | number | `10.0` | Greater than 0 and at most 300 seconds |
| `db` | string | `data/assets.db` | 1 to 4,096 characters; must not be a directory |
| `probe` | boolean | `false` | Enables HTTP/HTTPS probing |
| `verbose` | boolean | `false` | Enables debug-level job logs |
| `webhook` | string or null | `null` | Discord HTTPS webhook URL, up to 2,048 characters |
| `telegram` | string or null | `null` | `BOT_TOKEN/CHAT_ID`, up to 512 characters |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/scans \
  -H 'Content-Type: application/json' \
  -d '{
    "target": "example.com",
    "threads": 25,
    "timeout": 10,
    "db": "data/assets.db",
    "probe": true
  }'
```

Response:

```json
{
  "job_id": "9f1d4a7f8c2b4f7d9e6a3c1b5d8e2f40",
  "status": "running"
}
```

The target is normalized before the job starts. A second scan request while
another job is active returns HTTP `409`. Invalid fields return HTTP `422`.
Webhook and Telegram credentials are accepted for the lifetime of the job and are
not included in snapshots, logs, or stored files.

## Job snapshots

### `GET /api/scans/active`

Returns the active job snapshot or `null`.

```json
{
  "job": null
}
```

A job is removed from the active slot when it reaches `completed`, `failed`, or
`cancelled`.

### `GET /api/scans/{job_id}`

Returns the snapshot for a job. Returns HTTP `404` if the job is unknown or has
been pruned.

Snapshot fields:

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Job ID |
| `target` | string | Normalized target domain |
| `status` | string | `queued`, `running`, `completed`, `failed`, or `cancelled` |
| `error` | string or null | Terminal error, if any |
| `created_at` | number | Creation time as Unix seconds |
| `updated_at` | number | Last update time as Unix seconds |
| `last_event_id` | integer | Most recent event ID |
| `db` | string | Database path used by the scan |
| `probe` | boolean | Whether probing was enabled |
| `notifications` | boolean | Whether a notification channel was configured |
| `assets` | array | Active assets; empty until the scan completes |
| `new_assets` | array | Assets not previously stored for the target |
| `probes` | object | Probe results keyed by hostname |
| `notification_errors` | array of strings | Notification failures |
| `notifications_sent` | integer | Successfully delivered notifications |

Asset entries contain `hostname` and `addresses`. Probe entries contain `url`,
`status`, `title`, and `server`.

A `queued` status is normally brief. The scan task changes the status to
`running` when it starts.

## Event stream

### `GET /api/scans/{job_id}/events`

Returns a `text/event-stream` response. The stream replays events newer than the
provided cursor and then continues streaming while the job is active.

Query parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `last_event_id` | integer | `0` | Replay events with IDs greater than this value |

The optional `Last-Event-ID` request header takes precedence over the query
parameter. This is the header sent automatically by browser `EventSource`
reconnections.

Event wire format:

```text
id: 17
event: asset
data: {"hostname":"api.example.com","addresses":["192.0.2.10"],"is_new":true}

```

The server sends a keepalive comment every 15 seconds while the job is active:

```text
: keepalive
```

The stream ends after a terminal event (`done`, `error`, or `cancelled`). The
event buffer is bounded at 2,000 entries. If a cursor is older than the oldest
buffered event, replay starts from the oldest available event.

### Event payloads

| Event | Payload |
| --- | --- |
| `status` | `{"status": "running", "target": "example.com"}` |
| `source` | `{"name": "crt.sh", "ok": true, "count": 120, "error": null}` |
| `progress` | `{"phase": "resolve", "completed": 40, "total": 120}` |
| `asset` | `{"hostname": "api.example.com", "addresses": ["192.0.2.10"], "is_new": true}` |
| `probe` | `{"hostname": "api.example.com", "probes": [{"url": "https://api.example.com/", "status": 200, "title": "API", "server": "nginx"}]}` |
| `log` | `{"level": "info", "message": "crt.sh returned 120 subdomains"}` |
| `notification` | `{"provider": "Discord", "ok": false, "error": "Discord notification failed with HTTP 404"}` |
| `done` | `{"target": "example.com", "run_id": "...", "assets": 40, "new_assets": 3, "probes": 35, "notification_errors": []}` |
| `error` | `{"message": "all passive discovery sources failed"}` |
| `cancelled` | `{"message": "Scan cancelled."}` |

`phase` events use the following forms:

| Phase | Status | Payload fields |
| --- | --- | --- |
| `discovery` | `running` | `phase`, `status` |
| `discovery` | `completed` | `phase`, `status`, `count` |
| `resolve` | `running` | `phase`, `status`, `total` |
| `resolve` | `completed` | `phase`, `status`, `count` |
| `probe` | `running` | `phase`, `status`, `total` |
| `probe` | `completed` | `phase`, `status`, `count` |
| `save` | `running` | `phase`, `status` |
| `save` | `completed` | `phase`, `status`, `new` |
| `notify` | `running` | `phase`, `status` |
| `notify` | `completed` | `phase`, `status`, `sent`, `failed` |

`progress` events are emitted for `resolve` and `probe` phases with `completed`
and `total`. The `probe` phase is omitted when probing is disabled. The `notify`
phase is omitted when no notification channel is configured.

Example:

```bash
curl -N http://127.0.0.1:8000/api/scans/JOB_ID/events
```

## Cancel a scan

### `POST /api/scans/{job_id}/cancel`

Cancels an active scan and returns its final snapshot. Cancelling an already
terminal job returns the existing snapshot. Returns HTTP `404` for an unknown or
pruned job.

```bash
curl -X POST http://127.0.0.1:8000/api/scans/JOB_ID/cancel
```

A cancelled scan is recorded in `scan_runs` with status `cancelled`.

## Export results

### `GET /api/scans/{job_id}/export`

Exports results for a completed job.

| Parameter | Type | Default | Values |
| --- | --- | --- | --- |
| `format` | string | `json` | `json` or `csv` |

JSON exports contain `target`, `run_id`, `assets`, `new_assets`, `probes`, and
`notification_errors`.

CSV exports contain the columns:

```text
target, hostname, addresses, is_new, url, status, title, server
```

A host with multiple probe entries produces multiple CSV rows. A host without
probe results produces one row with empty probe columns. Text values that begin
with `=`, `+`, `-`, `@`, tab, or carriage return are prefixed with an apostrophe
to reduce spreadsheet formula injection risk.

The response includes a `Content-Disposition` attachment header. Export requests
return:

- HTTP `409` if the job has not produced results;
- HTTP `422` if `format` is invalid;
- HTTP `404` if the job is unknown or pruned.

## History

History endpoints read an existing SQLite database. They do not create a missing
database file. If the database path does not exist, the endpoint returns an empty
collection. A directory path returns HTTP `422`; an unreadable or invalid
database returns HTTP `500`.

All history endpoints accept a `db` query parameter. The default is
`data/assets.db`.

### `GET /api/history/targets`

Returns known targets from tracked assets and scan-run records.

```json
{
  "targets": ["example.com", "example.org"]
}
```

### `GET /api/history/runs`

Returns scan runs ordered by start time, newest first.

| Parameter | Type | Default | Constraints |
| --- | --- | --- | --- |
| `target` | string or null | `null` | Up to 253 characters |
| `db` | string | `data/assets.db` | Up to 4,096 characters |
| `limit` | integer | `50` | 1 to 500 |

Run objects contain `id`, `target`, `started_at`, `finished_at`, `status`,
`asset_count`, `new_count`, `probe_count`, and `error`.

```bash
curl 'http://127.0.0.1:8000/api/history/runs?db=data/assets.db&limit=20'
```

### `GET /api/history/assets`

Returns tracked assets ordered by hostname.

| Parameter | Type | Default | Constraints |
| --- | --- | --- | --- |
| `target` | string or null | `null` | Up to 253 characters |
| `search` | string or null | `null` | Hostname substring, up to 253 characters |
| `db` | string | `data/assets.db` | Up to 4,096 characters |
| `limit` | integer | `1000` | 1 to 2000 |

Asset objects contain `target`, `hostname`, `addresses`, `first_seen`, and
`last_seen`.

## Errors

FastAPI validation errors use HTTP `422` with a `detail` array. Application-level
errors use a string `detail`:

```json
{
  "detail": "a scan is already running"
}
```

| Status | Meaning |
| --- | --- |
| `404` | Job ID not found or pruned |
| `409` | A scan is already active, or results are not available for export |
| `422` | Invalid target, database path, notification value, or query parameter |
| `500` | History database could not be read |

## Retention and limits

- One scan is active per server process.
- Terminal jobs remain in memory for 3,600 seconds.
- At most 50 terminal jobs are retained.
- Each job retains at most 2,000 events.
- Job results are lost when the server restarts. Scan history remains in SQLite.
- The server does not provide authentication, authorization, or multi-user
  isolation.

## Security

The API can start outbound network scans and read local SQLite databases. Run it
only on a trusted local interface. The default bind address is `127.0.0.1`.
Binding to `0.0.0.0` or another non-loopback address exposes the API without
authentication.

Webhook URLs and Telegram tokens are accepted in request bodies and held in
server memory for the job. They are not written to SQLite, logs, browser storage,
or API responses.

## Related documentation

- [Web interface guide](gui.md)
- [Architecture and data model](architecture.md)
- [Operations and troubleshooting](operations.md)
