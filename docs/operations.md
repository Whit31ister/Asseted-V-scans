# Operations and troubleshooting

## Safe operation

Monitor only domains in scope for you. Set concurrency and scheduling
conservatively, and respect bug bounty rules, DNS provider policies, and
webhook limits. SQLite files and logs can reveal infrastructure details, so
restrict their permissions.

## Scheduled setup

Use absolute paths and run one manual scan before enabling cron:

```cron
0 */6 * * * /srv/asset-monitor/.venv/bin/python /srv/asset-monitor/main.py --target example.com --db /srv/asset-monitor/data/example.db >> /srv/asset-monitor/logs/example.log 2>&1
```

Create the log directory first. Keep webhook URLs out of shell history and
world-readable cron files where possible.

## Troubleshooting

### `ModuleNotFoundError`

Activate the virtual environment and reinstall dependencies:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

### CT discovery fails

Run with `--verbose` and verify outbound HTTPS access to `crt.sh` and Cert
Spotter. Providers may be unavailable or rate-limited. A successful response
with no matching names is treated as an empty result.

### No active assets are found

This is expected when CT names no longer resolve. Confirm DNS from the same host
and remember that only `A` and `AAAA` answers count as active.

### Webhook notification fails

Verify the URL is a Discord-compatible webhook, that it is reachable, and that
the timeout is sufficient. The CLI reports the HTTP or network error.

### Re-running reports no new assets

That is expected baseline behavior. Use another `--db` path to isolate a test or
different target; preserve production databases as history.

## Exit behavior

* `0`: scan completed successfully.
* `2`: invalid CLI values or a handled runtime scan error.
* Other unhandled exceptions may be surfaced by Python.
