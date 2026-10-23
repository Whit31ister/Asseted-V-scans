# Asset Monitor

Asset Monitor discovers subdomains from public Certificate Transparency and passive DNS sources, checks which names currently resolve in DNS, stores scan state in SQLite, and reports newly observed active assets.

Use it only against domains you own or are explicitly authorized to monitor. CT data is public, but DNS probing and monitoring can still be subject to program rules, provider policies, and local law.

## Features

* Aggregates and deduplicates names from `crt.sh`, Cert Spotter, AlienVault OTX, and HackerTarget.
* Normalizes and filters names so only subdomains of the target are retained.
* Resolves `A` and `AAAA` records concurrently to identify active assets.
* Optionally probes active assets over HTTPS and HTTP for status codes, page titles, and server headers.
* Maintains local SQLite state and reports hostnames not seen before.
* Sends optional Discord or Telegram notifications for new assets and probe results.

## Requirements

### Prerequisites

* Python 3.10 or higher
* Network access to the selected CT provider, DNS resolvers, and optional webhook endpoint
* Authorization to monitor the target domain

## Installation

### Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/Whit31ister/asset_monitor.git
   cd asset_monitor
   ```

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
   ```

3. Install the required dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

## Usage

Basic run against a target domain:

```bash
python main.py --target example.com
```

### Options and examples

* `--target DOMAIN` is required.
* `--threads N` limits concurrent DNS lookups (default: `50`; minimum: `1`).
* `--db PATH` selects the SQLite file (default: `data/assets.db`).
* `--timeout SECONDS` sets the CT, DNS, probe, and notification timeout (default: `10.0`).
* `--webhook URL` sends Discord notifications.
* `--probe` probes active assets over HTTPS and HTTP.
* `--telegram BOT_TOKEN/CHAT_ID` sends alerts through the Telegram Bot API.
* `--json` emits `target`, `assets`, `new_assets`, and `probes` as JSON. `probes` is empty unless `--probe` is enabled.
* `-v` / `--verbose` enables debug logging.

Examples:

  ```bash
  python main.py --target example.com --threads 10 --timeout 15
  ```

* **Set a custom database path:**

  ```bash
  python main.py --target example.com --db data/custom_assets.db
  ```

* **Enable Discord alerts:**

  ```bash
  python main.py --target example.com --webhook 'https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN'
  ```

* **Emit machine-readable output:**

  ```bash
  python main.py --target example.com --json
  ```

  * **Probe active web services:**

    ```bash
    python main.py --target example.com --probe --json
    ```

  * **Enable Telegram alerts:**

    ```bash
    python main.py --target example.com --telegram 'BOT_TOKEN/CHAT_ID'
    ```

The first scan stores every active asset as a baseline. Later scans report only
assets that have not been seen before in the configured SQLite database. Address
changes update an existing record but do not count as a new hostname. Names
found in CT logs without an `A` or `AAAA` address are not stored as active assets.
Each passive discovery source is retried independently, twice. Results from all
successful sources are merged and deduplicated; one provider being unavailable
does not discard results from the others. The scan fails only if every source
is unavailable.

With `--probe`, every active hostname is requested over HTTPS and then HTTP.
Reachable responses include the final URL, status code, page title, and `Server`
header. Probe failures are skipped per URL and do not fail the scan.

Discord alerts use the JSON webhook `content` field. Telegram alerts use the
Bot API `sendMessage` endpoint. Alerts include newly discovered assets and,
when `--probe` is enabled, HTTP status/title/server metadata. Messages are split
to fit provider limits. A failed notification request is reported as an error
rather than silently ignored.

## Automated Execution

To run the monitor continuously using a cron job, add an entry to your crontab:

```bash
0 */6 * * * /path/to/asset_monitor/.venv/bin/python /path/to/asset_monitor/main.py --target example.com --db /path/to/asset_monitor/data/assets.db >> /path/to/asset_monitor/cron.log 2>&1
```

## Directory Structure

```text
asset_monitor/
├── main.py                # Main CLI entry point
├── core/                  # Scanning and DNS resolution logic
├── database/              # SQLite state management
├── utils/                 # Webhook and alert helper utilities
├── tests/                 # Unit tests for normalization and state tracking
├── docs/                  # Architecture and operational documentation
├── requirements.txt       # Project dependencies
├── requirements-dev.txt   # Test/development dependencies
└── README.md              # Documentation
```

## Development and tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The unit tests cover hostname normalization, passive-source parsing, title
extraction, and SQLite new-asset detection. They do not call live CT, DNS, or
HTTP services.

## Limitations

* Discovery depends on public CT/passive-DNS APIs and cannot enumerate names absent from those sources.
* Public providers may rate-limit requests or return incomplete results.
* Only `A` and `AAAA` answers count as active; CNAME-only names are not included.
* SQLite state is local and historical: hostnames that disappear are retained.
* Webhook failures cause the CLI run to fail rather than being silently ignored.

## Further documentation

* [Architecture and data model](docs/architecture.md)
* [Operations and troubleshooting](docs/operations.md)

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.
