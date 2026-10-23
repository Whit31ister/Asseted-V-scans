"""Launch the local Asset Monitor web interface."""

from __future__ import annotations

import argparse
import ipaddress
import sys
import threading
import webbrowser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch the local Asset Monitor web interface."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1; keep it on loopback)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP port (default: 8000)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser automatically",
    )
    return parser


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.port <= 65535:
        print("error: --port must be between 1 and 65535", file=sys.stderr)
        return 2
    try:
        import uvicorn
    except ImportError:
        print(
            "error: uvicorn is not installed; run "
            "'python -m pip install -r requirements.txt'",
            file=sys.stderr,
        )
        return 2

    display_host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{display_host}:{args.port}/"
    if not _is_loopback(args.host):
        print(
            "warning: binding to a non-loopback address exposes an "
            "unauthenticated interface. Use only on trusted networks.",
            file=sys.stderr,
        )
    print(f"Asset Monitor running at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        timer = threading.Timer(1.0, webbrowser.open, args=[url])
        timer.daemon = True
        timer.start()
    uvicorn.run(
        "web.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
