"""Command-line entry point for the asset monitor."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from core.discovery import discover_subdomains
from core.prober import probe_assets
from core.resolver import resolve_subdomains
from database.state import AssetStore
from utils.webhook import send_webhook


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
    if args.threads < 1:
        raise ValueError("--threads must be at least 1")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    target = args.target.strip().lower().rstrip(".")
    discovered = await discover_subdomains(target, timeout=args.timeout)
    assets = await resolve_subdomains(discovered, concurrency=args.threads, timeout=args.timeout)
    probes = (
        await probe_assets(
            [asset["hostname"] for asset in assets],
            concurrency=args.threads,
            timeout=args.timeout,
        )
        if args.probe
        else {}
    )

    with AssetStore(args.db) as store:
        new_assets = store.record_scan(target, assets)

    if args.json:
        import json

        print(
            json.dumps(
                {"target": target, "assets": assets, "new_assets": new_assets, "probes": probes},
                indent=2,
            )
        )
    else:
        print(f"Target: {target}")
        print(f"Active assets: {len(assets)}")
        print(f"New assets: {len(new_assets)}")
        for asset in assets:
            print(f"  {asset['hostname']} -> {', '.join(asset['addresses'])}")
            for probe in probes.get(asset["hostname"], []):
                print(
                    f"    {probe['url']} [{probe['status']}]"
                    f" title={probe['title'] or '-'} server={probe['server'] or '-'}"
                )

    if args.webhook and (new_assets or probes):
        await send_webhook(
            args.webhook,
            target,
            new_assets,
            probes=probes,
            timeout=args.timeout,
        )
    if args.telegram and (new_assets or probes):
        await send_webhook(
            f"https://api.telegram.org/bot{args.telegram}",
            target,
            new_assets,
            probes=probes,
            timeout=args.timeout,
        )
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
