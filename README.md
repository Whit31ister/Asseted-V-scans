# Asset Monitor

An asynchronous asset discovery and monitoring engine written in Python. The tool queries public certificate transparency logs, performs concurrent DNS resolution to identify active subdomains, and alerts on newly discovered infrastructure via webhooks.

## Features

* Queries crt.sh and public certificate transparency endpoints for historical and active subdomains.
* Performs concurrent, non-blocking DNS resolution using `aiohttp` and `dnspython`.
* Maintains local state using SQLite to track infrastructure changes over time.
* Sends instant notifications for newly discovered assets via webhooks (Discord/Telegram).
* Configurable thread counts, timeout parameters, and output formats.

## Installation

### Prerequisites

* Python 3.9 or higher
* Git

### Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/Whit31ister/asset_monitor.git
   cd asset_monitor
   ```

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. Install the required dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Usage

Basic run against a target domain:

```bash
python main.py --target example.com
```

### Advanced Options

* **Specify thread count for DNS resolution:**

  ```bash
  python main.py --target example.com --threads 50
  ```

* **Set a custom database path:**

  ```bash
  python main.py --target example.com --db data/custom_assets.db
  ```

* **Enable Discord alerts:**

  ```bash
  python main.py --target example.com --webhook https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
  ```

## Automated Execution

To run the monitor continuously using a cron job, add an entry to your crontab:

```bash
0 */6 * * * /path/to/asset_monitor/venv/bin/python /path/to/asset_monitor/main.py --target example.com >> /path/to/asset_monitor/cron.log 2>&1
```

## Directory Structure

```text
asset_monitor/
├── main.py                # Main CLI entry point
├── core/                  # Scanning and DNS resolution logic
├── database/              # SQLite state management
├── utils/                 # Webhook and alert helper utilities
├── data/                  # Local storage for SQLite databases
├── requirements.txt       # Project dependencies
└── README.md              # Documentation
```

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.