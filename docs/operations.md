# Operations and troubleshooting

## Safe operation

Monitor only domains in scope for you. Set concurrency and scheduling
conservatively, and respect bug bounty rules, DNS provider policies, passive
source terms, and webhook limits. `--probe` makes outbound HTTP(S) requests to
each active hostname; enable it only when that traffic is in scope. SQLite
files and logs can reveal infrastructure details, so restrict their permissions.

## Scheduled setup

Use absolute paths and run one manual scan before enabling cron:

```cron
0 */6 * * * /srv/asset-monitor/.venv/bin/python /srv/asset-monitor/main.py --target example.com --db /srv/asset-monitor/data/example.db >> /srv/asset-monitor/logs/example.log 2>&1
```

Create the log directory first. Keep webhook URLs out of shell history and
world-readable cron files where possible.

## Troubleshooting

### `ModuleNotFoundError`

Activate the virtual environment and reinstall runtime and development dependencies:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

### Passive discovery fails

Run with `--verbose` and verify outbound HTTPS access to `crt.sh`, Cert Spotter,
AlienVault OTX, and HackerTarget. Providers may be unavailable or rate-limited.
The implementation retries each source twice and continues when only some
sources fail. The scan errors only when all sources fail.

### No active assets are found

This is expected when passive-source names no longer resolve. Confirm DNS from the same host
and remember that only `A` and `AAAA` answers count as active.

### Probes return no results

`--probe` tries HTTPS and then HTTP, follows redirects, and ignores connection,
timeout, and decoding failures for individual URLs. A host can resolve in DNS
while refusing HTTP(S), returning no probe entry.

### Discord or Telegram notification fails

For Discord, verify the webhook URL and reachability. For Telegram, pass the
value as `BOT_TOKEN/CHAT_ID`; the tool constructs the Bot API `sendMessage`
request. Messages are split to fit provider limits. The CLI reports HTTP or
network errors instead of hiding them.

### Re-running reports no new assets

That is expected baseline behavior. Use another `--db` path to isolate a test or
different target; preserve production databases as history.

## Exit behavior

* `0`: scan completed successfully.
* `2`: invalid CLI values or a handled runtime scan error.
* Other unhandled exceptions may be surfaced by Python.

## Data handling

The SQLite database stores target, hostname, resolved addresses, and first/last
seen timestamps. Probe metadata is printed or sent in notifications but is not
stored. Do not commit databases, logs, bot tokens, or webhook URLs to the
repository.
