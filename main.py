"""Command-line entry point for the asset monitor."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from core.scanner import ScanOptions, run_scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and monitor active subdomains for a domain."
    )
    parser.add_argument("--target", required=True, help="Root domain to monitor")
    parser.add_argument(
        "--threads",
        type=int,
        default=50,
        help="Maximum concurrent DNS lookups (default: 50)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/assets.db"),
        help="SQLite database path (default: data/assets.db)",
    )
    parser.add_argument(
        "--webhook",
        help="Discord webhook URL for new asset alerts",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Network timeout in seconds")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Probe active assets over HTTPS/HTTP for status, title, and server",
    )
    parser.add_argument(
        "--telegram",
        metavar="BOT_TOKEN/CHAT_ID",
        help="Send alerts through Telegram using BOT_TOKEN/CHAT_ID",
    )
    parser.add_argument("--json", action="store_true", help="Print results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser


async def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    options = ScanOptions(
        target=args.target,
        threads=args.threads,
        timeout=args.timeout,
        db_path=args.db,
        probe=args.probe,
        webhook=args.webhook,
        telegram=args.telegram,
        verbose=args.verbose,
    )
    result = await run_scan(options)

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "target": result.target,
                    "assets": result.assets,
                    "new_assets": result.new_assets,
                    "probes": result.probes,
                },
                indent=2,
            )
        )
    else:
        print(f"Target: {result.target}")
        print(f"Active assets: {len(result.assets)}")
        print(f"New assets: {len(result.new_assets)}")
        for asset in result.assets:
            print(f"  {asset['hostname']} -> {', '.join(asset['addresses'])}")
            for probe in result.probes.get(asset["hostname"], []):
                print(
                    f"    {probe['url']} [{probe['status']}]"
                    f" title={probe['title'] or '-'} server={probe['server'] or '-'}"
                )

    if result.notification_errors:
        for message in result.notification_errors:
            print(f"error: {message}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
