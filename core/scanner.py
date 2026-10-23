"""Shared scan orchestration for the CLI and web interface."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.discovery import discover_subdomains
from core.prober import ProbeResult, probe_assets
from core.resolver import Asset, resolve_subdomains
from database.state import AssetStore
from utils.webhook import send_webhook

LOGGER = logging.getLogger(__name__)

TARGET_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)
EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class ScanOptions:
    target: str
    threads: int = 50
    timeout: float = 10.0
    db_path: Path = Path("data/assets.db")
    probe: bool = False
    webhook: str | None = None
    telegram: str | None = None
    verbose: bool = False

    def __post_init__(self) -> None:
        self.target = normalize_target(self.target)
        if self.threads < 1:
            raise ValueError("--threads must be at least 1")
        if self.timeout <= 0:
            raise ValueError("--timeout must be greater than 0")
        raw_path = str(self.db_path)
        if not raw_path.strip():
            raise ValueError("database path must not be empty")
        self.db_path = Path(raw_path).expanduser()


@dataclass(slots=True)
class ScanResult:
    target: str
    run_id: str
    assets: list[Asset]
    new_assets: list[Asset]
    probes: dict[str, list[ProbeResult]]
    notifications_sent: int = 0
    notification_errors: list[str] = field(default_factory=list)


def normalize_target(value: str) -> str:
    target = value.strip().lower().rstrip(".")
    if not target:
        raise ValueError("invalid target domain")
    if not target.isascii():
        try:
            target = target.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("invalid target domain") from exc
    if not TARGET_RE.match(target):
        raise ValueError("invalid target domain")
    try:
        ipaddress.ip_address(target)
    except ValueError:
        return target
    raise ValueError("target must be a domain name, not an IP address")


def _emit(
    callback: EventCallback | None, event: str, data: dict[str, Any]
) -> None:
    if callback is None:
        return
    try:
        callback(event, data)
    except Exception:
        pass


def _notification_error(provider: str, exc: Exception) -> str:
    status = getattr(exc, "status", None)
    if status:
        return f"{provider} notification failed with HTTP {status}"
    if isinstance(exc, ValueError):
        return f"{provider} notification failed: {exc}"
    return f"{provider} notification failed ({type(exc).__name__})"


async def run_scan(
    options: ScanOptions, emit: EventCallback | None = None
) -> ScanResult:
    """Run a complete scan using the shared CLI/web pipeline."""
    target = options.target
    assets: list[Asset] = []
    probes: dict[str, list[ProbeResult]] = {}
    new_assets: list[Asset] = []
    notifications_sent = 0
    notification_errors: list[str] = []
    finished = False

    with AssetStore(options.db_path) as store:
        run_id = store.start_scan_run(target)
        known = store.known_hostnames(target)

    try:
        _emit(emit, "phase", {"phase": "discovery", "status": "running"})

        def on_source(name: str, ok: bool, count: int, error: str | None) -> None:
            _emit(
                emit,
                "source",
                {"name": name, "ok": ok, "count": count, "error": error},
            )

        discovered = await discover_subdomains(
            target, timeout=options.timeout, on_source=on_source
        )
        _emit(
            emit,
            "phase",
            {"phase": "discovery", "status": "completed", "count": len(discovered)},
        )

        _emit(
            emit,
            "phase",
            {"phase": "resolve", "status": "running", "total": len(discovered)},
        )

        def on_resolve(
            completed: int, total: int, asset: Asset | None
        ) -> None:
            _emit(
                emit,
                "progress",
                {
                    "phase": "resolve",
                    "completed": completed,
                    "total": total,
                },
            )
            if asset is not None:
                _emit(
                    emit,
                    "asset",
                    {
                        "hostname": asset["hostname"],
                        "addresses": asset["addresses"],
                        "is_new": asset["hostname"] not in known,
                    },
                )

        assets = await resolve_subdomains(
            discovered,
            concurrency=options.threads,
            timeout=options.timeout,
            on_result=on_resolve,
        )
        _emit(
            emit,
            "phase",
            {"phase": "resolve", "status": "completed", "count": len(assets)},
        )

        if options.probe:
            _emit(
                emit,
                "phase",
                {"phase": "probe", "status": "running", "total": len(assets)},
            )

            def on_probe(
                completed: int,
                total: int,
                hostname: str,
                results: list[ProbeResult],
            ) -> None:
                _emit(
                    emit,
                    "progress",
                    {"phase": "probe", "completed": completed, "total": total},
                )
                if results:
                    _emit(
                        emit,
                        "probe",
                        {"hostname": hostname, "probes": results},
                    )

            probes = await probe_assets(
                [asset["hostname"] for asset in assets],
                concurrency=options.threads,
                timeout=options.timeout,
                on_result=on_probe,
            )
            _emit(
                emit,
                "phase",
                {
                    "phase": "probe",
                    "status": "completed",
                    "count": sum(len(results) for results in probes.values()),
                },
            )

        _emit(emit, "phase", {"phase": "save", "status": "running"})
        with AssetStore(options.db_path) as store:
            new_assets = store.record_scan(target, assets)
        _emit(
            emit,
            "phase",
            {"phase": "save", "status": "completed", "new": len(new_assets)},
        )

        if options.webhook or options.telegram:
            _emit(emit, "phase", {"phase": "notify", "status": "running"})
            notifications: list[tuple[str, str]] = []
            if options.webhook:
                notifications.append(("Discord", options.webhook))
            if options.telegram:
                notifications.append(
                    (
                        "Telegram",
                        f"https://api.telegram.org/bot{options.telegram}",
                    )
                )
            for provider, url in notifications:
                if not new_assets and not probes:
                    continue
                try:
                    await send_webhook(
                        url,
                        target,
                        new_assets,
                        probes=probes,
                        timeout=options.timeout,
                    )
                except Exception as exc:
                    message = _notification_error(provider, exc)
                    notification_errors.append(message)
                    LOGGER.warning("%s", message)
                    _emit(
                        emit,
                        "notification",
                        {"provider": provider, "ok": False, "error": message},
                    )
                else:
                    notifications_sent += 1
                    _emit(
                        emit,
                        "notification",
                        {"provider": provider, "ok": True, "error": None},
                    )
            _emit(
                emit,
                "phase",
                {
                    "phase": "notify",
                    "status": "completed",
                    "sent": notifications_sent,
                    "failed": len(notification_errors),
                },
            )

        with AssetStore(options.db_path) as store:
            store.finish_scan_run(
                run_id,
                "completed",
                asset_count=len(assets),
                new_count=len(new_assets),
                probe_count=sum(len(results) for results in probes.values()),
                error="; ".join(notification_errors) or None,
            )
        finished = True

        return ScanResult(
            target=target,
            run_id=run_id,
            assets=assets,
            new_assets=new_assets,
            probes=probes,
            notifications_sent=notifications_sent,
            notification_errors=notification_errors,
        )
    except asyncio.CancelledError:
        if not finished:
            with AssetStore(options.db_path) as store:
                store.finish_scan_run(
                    run_id,
                    "cancelled",
                    asset_count=len(assets),
                    probe_count=sum(len(results) for results in probes.values()),
                )
        raise
    except Exception as exc:
        if not finished:
            with AssetStore(options.db_path) as store:
                store.finish_scan_run(
                    run_id,
                    "failed",
                    asset_count=len(assets),
                    probe_count=sum(len(results) for results in probes.values()),
                    error=str(exc)[:1000],
                )
        raise
