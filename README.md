# Asset Monitor

Asset Monitor is a Python application for passive subdomain discovery and asset
change detection. It collects names from public Certificate Transparency and
passive DNS sources, resolves active `A` and `AAAA` records, stores scan state in
SQLite, and reports hostnames that have not been observed before.

The project provides two interfaces:

- a command-line interface for scheduled or interactive scans;
- a local web dashboard for live progress, results, history, and exports.

Use this tool only against domains that you own or are explicitly authorized to
assess. Certificate Transparency data is public, but DNS queries, HTTP probing,
and monitoring activity can still be governed by program rules, provider
policies, and local law.

## Features

- Aggregates and deduplicates subdomains from `crt.sh`, Cert Spotter,
  AlienVault OTX, and HackerTarget.
- Resolves `A` and `AAAA` records concurrently and records only names with an
  active address.
- Optionally probes active hosts over HTTPS and HTTP for status codes, final
  URLs, page titles, and `Server` headers.
- Stores tracked assets, first/last seen timestamps, and scan-run history in
  SQLite.
- Sends optional Discord or Telegram notifications for new assets and probe
  results.
- Provides a local FastAPI dashboard with Server-Sent Events (SSE) progress,
  streaming logs, result filtering, scan history, and JSON/CSV export.
- Supports cancellation of the active dashboard scan.
- Does not persist Discord webhook URLs or Telegram bot tokens.

## Requirements

- Python 3.10 or later.
- Network access to the configured discovery providers, DNS resolvers, target
  hosts when probing is enabled, and notification endpoints.
- Authorization to monitor the target domain.
- A modern browser for the web dashboard.

## Installation

Create a virtual environment and install the runtime dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Install development dependencies to run the test suite:

```bash
python -m pip install -r requirements-dev.txt
```

## Quick start

### Web dashboard

Start the local dashboard:

```bash
python gui.py
```

The server binds to `127.0.0.1:8000` and opens the dashboard in the default
browser. Use `--no-browser` on headless systems or `--port` to select a different
port:

```bash
python gui.py --port 8080 --no-browser
```

The dashboard provides:

- scan configuration for target, concurrency, timeout, database path, probing,
  and notifications;
- source-level discovery results and live scan logs;
- phase and progress reporting for discovery, DNS resolution, probing, state
  updates, and notifications;
- a filterable results table with new/known status and probe details;
- JSON and CSV export for completed scans;
- scan-run history and tracked assets from the selected SQLite database;
- browser-local defaults for non-secret settings.

Only one scan can run per server process. The local server has no authentication
and must not be exposed to an untrusted network. See the
[web interface guide](docs/gui.md) for operation and configuration details.

### Command line

Run a basic scan:

```bash
python main.py --target example.com
```

Run a scan with probing and JSON output:

```bash
python main.py --target example.com --probe --json
```

Run a scan with a Discord notification:

```bash
python main.py \
  --target example.com \
  --webhook 'https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN'
```

Run a scan with a Telegram notification:

```bash
python main.py \
  --target example.com \
  --telegram 'BOT_TOKEN/CHAT_ID'
```

The first scan against a database stores all active assets as a baseline.
Subsequent scans report only hostnames that were not previously stored for the
same target. Address changes update the existing record but do not count as a new
hostname.

## CLI reference

| Option | Description |
| --- | --- |
| `--target DOMAIN` | Target root domain. Required. |
| `--threads N` | Maximum concurrent DNS lookups. Default: `50`; minimum: `1`. |
| `--timeout SECONDS` | Network timeout for discovery, DNS, probing, and notifications. Default: `10.0`. |
| `--db PATH` | SQLite database path. Default: `data/assets.db`. |
| `--probe` | Probe active hosts over HTTPS and HTTP. |
| `--webhook URL` | Send Discord notifications through a webhook. |
| `--telegram BOT_TOKEN/CHAT_ID` | Send notifications through the Telegram Bot API. |
| `--json` | Print `target`, `assets`, `new_assets`, and `probes` as JSON. |
| `-v`, `--verbose` | Enable debug logging. |

Examples:

```bash
python main.py --target example.com --threads 10 --timeout 15
python main.py --target example.com --db data/custom_assets.db
python main.py --target example.com --probe --json
```

### Exit status

| Code | Meaning |
| --- | --- |
| `0` | Scan completed successfully. |
| `2` | Invalid input, a handled runtime error, or a notification failure after results were saved. |

Unhandled exceptions may produce a non-zero exit status and a traceback.

## Scan behavior

Discovery runs four passive sources in parallel. Each source is attempted twice.
Results are normalized, filtered to the target domain, merged, and deduplicated.
A source failure does not discard results from the other sources. The scan fails
only when every source fails.

DNS resolution checks both `A` and `AAAA`. A name is stored only when at least
one address is returned. With `--probe`, each active hostname is requested over
HTTPS and then HTTP. Probe redirects are followed, response bodies are limited to
256 KiB, and titles are normalized. Individual probe failures are ignored.

Discord notifications use the webhook `content` field. Telegram notifications use
the Bot API `sendMessage` endpoint. Messages are split to respect provider limits.
A notification is sent when the scan produced new assets or at least one probe
result. The message includes new assets and, when probing is enabled, probe
metadata.

Probe results are not stored in SQLite. They are available in the current scan
result, the dashboard, exports, and notifications.

## Data storage

The SQLite database stores:

- one row per `(target, hostname)` pair;
- resolved addresses as a JSON array;
- first seen and last seen timestamps;
- one row per scan run with status, counts, timestamps, and errors.

The database uses WAL mode so the dashboard can read history while a scan is
writing. Missing databases are not created by history requests. Removing or
replacing the database resets the new-asset baseline for the affected targets.

## Automated execution

The CLI can be scheduled with cron or another process manager. Use absolute paths
and run one manual scan before enabling automation:

```cron
0 */6 * * * /srv/asset-monitor/.venv/bin/python /srv/asset-monitor/main.py --target example.com --db /srv/asset-monitor/data/example.db >> /srv/asset-monitor/logs/example.log 2>&1
```

Create the log directory first and restrict access to logs, databases, and
configuration files that contain notification credentials.

## Development

Install development dependencies and run the test suite from the repository
root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests cover hostname normalization, passive-source parsing, title extraction,
SQLite change detection, scan-run history, shared scan orchestration, and the web
API. Network calls are mocked.

## Project structure

```text
asset_monitor/
├── main.py                # CLI entry point
├── gui.py                 # Local web dashboard launcher
├── core/                  # Shared scan pipeline, discovery, DNS, probing
├── database/              # SQLite state and scan-run history
├── utils/                 # Notification helpers
├── web/                   # FastAPI application, jobs, static dashboard
├── tests/                 # Unit and API tests
├── docs/                  # Architecture, API, GUI, and operations documentation
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Test and development dependencies
└── README.md
```

## Limitations

- Passive discovery cannot find names that are absent from the queried sources.
- Public providers may rate-limit requests or return incomplete results.
- Only names with at least one `A` or `AAAA` answer are stored as active assets.
- SQLite state is historical. Hostnames that no longer resolve remain in the
  database.
- The dashboard is single-user and accepts one scan at a time.
- Completed dashboard jobs are held in memory for a limited time. Persistent
  history is available from SQLite.

## Documentation

- [Architecture and data model](docs/architecture.md)
- [Web interface guide](docs/gui.md)
- [HTTP API reference](docs/api.md)
- [Operations and troubleshooting](docs/operations.md)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
