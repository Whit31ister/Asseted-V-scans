# Operations and troubleshooting

## Scope and safe operation

Use Asset Monitor only against domains that you own or are explicitly authorized
to assess. Set concurrency and scheduling conservatively. Respect bug bounty
program rules, DNS provider policies, passive-source terms, and notification
provider limits.

The `--probe` option sends HTTP and HTTPS requests to every active hostname.
Enable it only when that traffic is within scope. Discovery and probing generate
outbound network traffic from the host running the application.

SQLite databases and logs can reveal infrastructure details. Restrict file
permissions to the account that runs the application.

## Requirements

- Python 3.10 or later.
- Runtime dependencies from `requirements.txt`.
- Network access to the four passive sources, DNS resolvers, target hosts when
  probing, and notification endpoints.
- Write access to the selected database directory and any log directory.
- A loopback interface for the web dashboard.

## Installation and upgrades

Install or update runtime dependencies:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Install development dependencies to run tests:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Before upgrading an existing deployment, back up the SQLite database and review
changes to the CLI, API, and database schema.

## Command-line operation

Basic scan:

```bash
python main.py --target example.com
```

Scheduled scan with an absolute database path and log redirection:

```cron
0 */6 * * * /srv/asset-monitor/.venv/bin/python /srv/asset-monitor/main.py --target example.com --db /srv/asset-monitor/data/example.db >> /srv/asset-monitor/logs/example.log 2>&1
```

Create the log directory before enabling the schedule. Use absolute paths and run
one manual scan before scheduling automation.

Recommended practices:

- Keep webhook URLs and bot tokens out of shell history and world-readable cron
  files. Prefer a protected environment file or secret manager when the
  scheduler supports it.
- Redirect standard output and standard error to a log file with restricted
  permissions.
- Use a separate database path for unrelated targets or test runs.
- Monitor the process exit status and notification errors.

## Web dashboard operation

Start the dashboard:

```bash
python gui.py
```

Options:

| Option | Description |
| --- | --- |
| `--host HOST` | Bind address. Default: `127.0.0.1`. |
| `--port PORT` | HTTP port. Default: `8000`. |
| `--no-browser` | Do not open a browser automatically. |

Examples:

```bash
python gui.py --port 8080
python gui.py --no-browser
```

The process runs until `Ctrl+C`. Use a process manager if the dashboard must
restart automatically.

Health check:

```bash
curl http://127.0.0.1:8000/api/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "active_job": null
}
```

Operational limits:

- One scan can run per server process.
- Completed, failed, and cancelled jobs remain in memory for 3,600 seconds.
- At most 50 terminal jobs are retained.
- Each job retains at most 2,000 events.
- Jobs and event streams are lost when the server restarts.
- Persistent scan history is stored in SQLite.

The web server has no authentication. Keep it bound to `127.0.0.1`. Binding to a
non-loopback address exposes scan controls and database history to anyone who can
reach the port. `gui.py` prints a warning but does not prevent the binding.

Scan logs are streamed to the browser and are not written to disk by the
application. Redirect the process output to a file when persistent server logs
are required.

## Database and backups

The default database is `data/assets.db`. Relative paths are resolved from the
current working directory.

The database uses SQLite WAL mode. While the application is running, the database
directory may contain `-wal` and `-shm` files. Use a SQLite-aware backup method:

```bash
sqlite3 data/assets.db ".backup 'backups/assets-2026-10-23.db'"
```

Alternatively, stop the server and all scans before copying the database file and
any WAL sidecar files. Do not copy only the main database file while a writer is
active.

The database stores assets, addresses, first/last seen timestamps, and scan-run
metadata. Probe results are not stored. Deleting the database resets the
new-asset baseline for all targets in that file.

There is no automatic retention policy for `assets` or `scan_runs`. Plan database
growth and archival according to the scan schedule and number of targets.

Restrict database permissions:

```bash
chmod 600 data/assets.db
```

## Performance and resource planning

- `--threads` limits concurrent DNS and probe operations. Increase it only after
  accounting for provider rate limits and target capacity.
- `--timeout` applies to discovery, DNS, probing, and notifications. Lower values
  reduce scan duration but increase the chance of incomplete results.
- Probing adds one HTTPS and one HTTP request per active hostname, subject to
  redirects.
- The web dashboard stores events and completed jobs in memory. Large scans
  increase per-job memory use until the job is pruned.
- Running multiple CLI writers against the same database can produce misleading
  change detection. The web server serializes web scans, but it does not
  coordinate with separate CLI processes.

## Monitoring

For scheduled CLI scans, monitor:

- exit status;
- standard error output;
- new-asset counts;
- notification failures;
- database file size.

For the dashboard, monitor:

- the health endpoint;
- server process status;
- disk space for the database and logs;
- completed job status in the history view.

## Troubleshooting

### `ModuleNotFoundError`

Activate the virtual environment and reinstall dependencies:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

For test failures, install `requirements-dev.txt` as well.

### The dashboard does not start

Confirm that `fastapi` and `uvicorn` are installed. If port `8000` is in use,
select another port:

```bash
python gui.py --port 8080
```

For a headless host, use `--no-browser` and open the printed URL from a trusted
machine only if the interface is safely reachable.

### A scan returns HTTP 409

Another scan is active in the same server process. Wait for it to finish or
cancel it before starting another scan.

### The event stream reconnects

A dropped SSE connection reconnects using the last event ID. The dashboard also
polls the job snapshot while the stream is unavailable. Completed results remain
available through the job snapshot until the retention limit is reached and
through the history view after the server restarts.

### Passive discovery fails

Run with `--verbose` and verify outbound HTTPS access to `crt.sh`, Cert Spotter,
AlienVault OTX, and HackerTarget. Providers may be unavailable or rate-limited.
Each source is attempted twice, and the scan continues when only some sources
fail. The scan fails when all four sources fail.

### No active assets are found

Passive sources may return names that no longer resolve. Confirm DNS resolution
from the same host. Only names with at least one `A` or `AAAA` answer are stored
as active assets.

### Probe results are missing

`--probe` tries HTTPS and then HTTP, follows redirects, and ignores connection,
timeout, and decoding failures for individual URLs. A host can resolve in DNS
while refusing HTTP and HTTPS, resulting in no probe entry.

### Discord or Telegram notifications fail

For Discord, verify the webhook URL and network reachability. For Telegram, use
`BOT_TOKEN/CHAT_ID`; the application constructs the Bot API `sendMessage` request.
Messages are split to fit provider limits.

Notification failures do not discard saved assets. The failure is recorded in the
scan result and `scan_runs`. The CLI exits with status `2`; the dashboard reports
a warning.

### Re-running a scan reports no new assets

This is expected baseline behavior. Use a different `--db` path for an isolated
test or a separate target. Preserve production databases as history.

### History is empty

Confirm that the database path in the dashboard Settings matches the path used by
the scan. History requests do not create missing database files.

## Exit behavior

CLI behavior:

| Code | Meaning |
| --- | --- |
| `0` | Scan completed successfully |
| `2` | Invalid input, a handled runtime error, or a notification failure after results were saved |

Unhandled exceptions may produce a different non-zero exit status and a
traceback. The web server runs until stopped; scan failures are reported in the
dashboard and recorded in `scan_runs`.

## Data handling

The SQLite database stores:

- target and hostname pairs;
- resolved addresses;
- first and last seen timestamps;
- scan-run timestamps, status, counts, and errors.

Probe metadata is used for results, exports, and notifications but is not
persisted. Notification credentials are not persisted by the dashboard.

Do not commit databases, logs, bot tokens, webhook URLs, or environment files to
the repository.

## Related documentation

- [Web interface guide](gui.md)
- [HTTP API reference](api.md)
- [Architecture and data model](architecture.md)
