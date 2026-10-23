# Web interface guide

## Overview

The web interface is a local single-page dashboard served by FastAPI. It runs the
same scan pipeline as the CLI and streams progress, logs, assets, and probe
results over Server-Sent Events (SSE).

The dashboard provides:

- scan configuration for target, concurrency, timeout, database, probing, and
  notifications;
- source-level discovery results and live logs;
- phase and progress reporting for discovery, resolution, probing, state
  updates, and notifications;
- a filterable results table with new/known status and probe details;
- JSON and CSV export for completed scans;
- scan-run history and tracked assets;
- browser-local defaults for non-secret settings.

Live logs are held in memory for the active job and are not written to disk by
the application. The dashboard does not provide authentication or multi-user
access.

## Starting the dashboard

Install the runtime dependencies first:

```bash
python -m pip install -r requirements.txt
```

Start the application from the repository root:

```bash
python gui.py
```

Defaults:

| Setting | Value |
| --- | --- |
| Bind address | `127.0.0.1` |
| Port | `8000` |
| Browser | Opened automatically |
| URL | `http://127.0.0.1:8000/` |

Command-line options:

| Option | Description |
| --- | --- |
| `--host HOST` | Bind address. Default: `127.0.0.1`. |
| `--port PORT` | HTTP port from 1 to 65535. Default: `8000`. |
| `--no-browser` | Do not open a browser automatically. |

Examples:

```bash
python gui.py --port 8080
python gui.py --host 127.0.0.1 --port 9000 --no-browser
```

The process runs until `Ctrl+C`. Scan results and history are stored in SQLite
and remain after shutdown. Jobs and event streams are held in memory and do not
survive a server restart.

If the selected port is already in use, start the server on another port.

## Scan configuration

| Field | Description |
| --- | --- |
| Target domain | Root domain to monitor. Internationalized names are converted to ASCII. IP addresses are rejected. |
| Concurrency | Maximum concurrent DNS lookups or probe operations. Default: `50`; range: 1 to 500. |
| Timeout | Timeout in seconds for discovery, DNS, probing, and notifications. Default: `10`; maximum: 300. |
| SQLite database | Database file used for asset state, history, and change detection. Default: `data/assets.db`. |
| Probe active assets | Request each active host over HTTPS and HTTP. |
| Verbose logs | Include debug-level scan logs in the live log panel. |
| Discord webhook | Optional Discord webhook URL for notifications. |
| Telegram BOT_TOKEN/CHAT_ID | Optional Telegram Bot API credentials. |

Relative database paths are resolved from the directory where `gui.py` was
started. The API creates the database and parent directory during a scan. History
requests do not create a missing database.

Discord webhook URLs and Telegram credentials are accepted only for the current
scan. The form fields are cleared when a scan starts, and the values are not
stored in SQLite, `localStorage`, logs, or API snapshots.

The first scan against a database records all active assets as a baseline.
Subsequent scans against the same target and database report only hostnames that
have not been seen before.

## Scan lifecycle

Only one scan can run per server process. A second start request returns HTTP
`409`. A running scan can be cancelled from the dashboard; cancellation is
recorded in the scan-run history.

The dashboard displays the following phases when they apply:

| Phase | Description |
| --- | --- |
| Discovery | Queries the four passive sources |
| DNS resolution | Resolves `A` and `AAAA` records for discovered names |
| Web probing | Probes active hosts over HTTPS and HTTP, if enabled |
| State update | Writes assets and change detection results to SQLite |
| Notifications | Sends configured Discord or Telegram alerts |

Probing is omitted when the option is disabled. Notifications are omitted when no
channel is configured.

If the page is reloaded while a scan is active, the dashboard requests the active
job, replays buffered events, and resumes the stream. If the page is reloaded
after a scan completes, the last result is not restored automatically; completed
results remain in the history view and in the in-memory API until the job is
pruned.

The stream uses `EventSource` and resumes with the last event ID when the
connection is re-established. If the stream remains unavailable, the dashboard
polls the job snapshot every two seconds until the stream reconnects or the job
reaches a terminal state.

## Results

The results table updates as DNS assets and probe results arrive. New hostnames
are marked separately from known hostnames. The table supports hostname filtering
and sorting by hostname or status.

Probe details include the final URL, HTTP status, page title, and `Server` header
when available. Probe failures are omitted for the affected URL.

Completed scans provide two export formats:

| Format | Contents |
| --- | --- |
| JSON | Target, run ID, active assets, new assets, probe results, and notification errors |
| CSV | Target, hostname, addresses, new/known state, URL, status, title, and server |

CSV exports use one row per probe result. Hosts without probe results produce one
row with empty probe columns. Text values that begin with spreadsheet formula
characters are prefixed with an apostrophe.

## History

The history view reads the database selected in Settings and displays:

- scan runs with start and finish timestamps, status, asset counts, new-asset
  counts, probe counts, and errors;
- tracked assets with target, addresses, and first/last seen timestamps;
- target filtering and hostname search.

History reads use SQLite WAL mode, so they can proceed while a scan is writing.
The history view does not create a missing database file.

## Settings

The following defaults are stored in the browser's `localStorage`:

- database path;
- concurrency;
- timeout;
- probing;
- verbose logging.

The settings are browser-specific and do not affect other clients or the server
globally. They do not contain notification credentials.

## API and OpenAPI documentation

The frontend uses the HTTP API documented in the [HTTP API reference](api.md).
Interactive OpenAPI documentation is available at:

```text
http://127.0.0.1:8000/api/docs
```

## Security

The dashboard has no authentication and can start outbound scans and read local
SQLite databases. Keep it bound to a loopback address.

Binding to `0.0.0.0` or another public interface exposes scan controls and
history to anyone who can reach the port. `gui.py` prints a warning for
non-loopback addresses but does not prevent the binding.

The backend validates target domains, database paths, Discord webhook URLs, and
Telegram values. Discord requests are restricted to approved Discord hosts.
Displayed external data is rendered with DOM text nodes rather than HTML.

## Troubleshooting

### The dashboard does not start

Confirm that the runtime dependencies are installed:

```bash
python -m pip install -r requirements.txt
```

Start without automatic browser launch and open the printed URL manually:

```bash
python gui.py --no-browser
```

If port `8000` is in use, select another port:

```bash
python gui.py --port 8080
```

### The browser reports Offline or Reconnecting

Confirm that the `gui.py` process is still running. The dashboard retries the SSE
connection and falls back to periodic job snapshot requests while the stream is
unavailable.

### A scan returns HTTP 409

Another scan is active in the same server process. Wait for it to finish or
cancel it before starting another scan.

### History is empty

Confirm that the database path in Settings matches the path used by the scan.
History requests do not create missing databases.

### A scan completes without new assets

This is expected after the first scan. The database already contains the active
hostnames for the target. Use a different database path for an independent
baseline.

### Notifications fail

Notification errors appear as warnings and are recorded in scan history. The scan
results remain saved. Verify the Discord webhook URL or the Telegram
`BOT_TOKEN/CHAT_ID` value and network reachability.

The CLI exits with status `2` after a notification failure. The dashboard reports
the failure without discarding the completed scan.

## Related documentation

- [HTTP API reference](api.md)
- [Architecture and data model](architecture.md)
- [Operations and troubleshooting](operations.md)
