import asyncio
from pathlib import Path

import pytest

import core.scanner as scanner
from core.scanner import ScanOptions, normalize_target, run_scan
from database.state import AssetStore


def test_normalize_target_validates_and_normalizes() -> None:
    assert normalize_target(" Example.COM. ") == "example.com"
    assert normalize_target("bücher.example") == "xn--bcher-kva.example"
    for value in ("", "example", "127.0.0.1", "bad_target.example"):
        with pytest.raises(ValueError):
            normalize_target(value)


def test_run_scan_records_results_and_emits_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    notifications: list[str] = []

    async def fake_discover(target, timeout=10.0, *, on_source=None):
        if on_source:
            on_source("crt.sh", True, 2, None)
        return ["api.example.com"]

    async def fake_resolve(
        hostnames, concurrency=50, timeout=10.0, *, on_result=None
    ):
        asset = {"hostname": "api.example.com", "addresses": ["192.0.2.10"]}
        if on_result:
            on_result(1, 1, asset)
        return [asset]

    async def fake_probe(
        hostnames, concurrency=50, timeout=10.0, *, on_result=None
    ):
        results = [
            {
                "url": "https://api.example.com/",
                "status": 200,
                "title": "API",
                "server": "test",
            }
        ]
        if on_result:
            on_result(1, 1, "api.example.com", results)
        return {"api.example.com": results}

    async def fake_webhook(url, target, assets, probes=None, timeout=10.0):
        notifications.append(url)

    monkeypatch.setattr(scanner, "discover_subdomains", fake_discover)
    monkeypatch.setattr(scanner, "resolve_subdomains", fake_resolve)
    monkeypatch.setattr(scanner, "probe_assets", fake_probe)
    monkeypatch.setattr(scanner, "send_webhook", fake_webhook)

    events: list[tuple[str, dict]] = []
    options = ScanOptions(
        target="Example.COM.",
        probe=True,
        db_path=tmp_path / "assets.db",
        webhook="https://discord.com/api/webhooks/1/token",
    )
    result = asyncio.run(
        run_scan(options, emit=lambda event, data: events.append((event, data)))
    )

    assert result.target == "example.com"
    assert result.assets == [
        {"hostname": "api.example.com", "addresses": ["192.0.2.10"]}
    ]
    assert result.new_assets == result.assets
    assert result.probes["api.example.com"][0]["status"] == 200
    assert result.notifications_sent == 1
    assert result.notification_errors == []
    assert notifications == ["https://discord.com/api/webhooks/1/token"]
    assert any(event == "source" for event, _ in events)
    assert any(event == "asset" for event, _ in events)
    assert any(event == "probe" for event, _ in events)
    assert ("phase", {"phase": "save", "status": "completed", "new": 1}) in events

    with AssetStore(options.db_path) as store:
        runs = store.list_scan_runs(target="example.com")
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["asset_count"] == 1
        assert runs[0]["new_count"] == 1
        assert runs[0]["probe_count"] == 1
        assets = store.list_assets(target="example.com")
        assert assets[0]["hostname"] == "api.example.com"
        assert assets[0]["addresses"] == ["192.0.2.10"]

    second = asyncio.run(
        run_scan(
            ScanOptions(
                target="example.com",
                db_path=tmp_path / "assets.db",
            )
        )
    )
    assert second.new_assets == []


def test_notification_failure_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_discover(target, timeout=10.0, *, on_source=None):
        return ["api.example.com"]

    async def fake_resolve(
        hostnames, concurrency=50, timeout=10.0, *, on_result=None
    ):
        return [
            {"hostname": "api.example.com", "addresses": ["192.0.2.10"]}
        ]

    async def fake_webhook(url, target, assets, probes=None, timeout=10.0):
        raise RuntimeError("network down")

    monkeypatch.setattr(scanner, "discover_subdomains", fake_discover)
    monkeypatch.setattr(scanner, "resolve_subdomains", fake_resolve)
    monkeypatch.setattr(scanner, "send_webhook", fake_webhook)

    result = asyncio.run(
        run_scan(
            ScanOptions(
                target="example.com",
                db_path=tmp_path / "assets.db",
                webhook="https://discord.com/api/webhooks/1/token",
            )
        )
    )

    assert result.notifications_sent == 0
    assert result.notification_errors == [
        "Discord notification failed (RuntimeError)"
    ]
    assert len(result.new_assets) == 1
    with AssetStore(tmp_path / "assets.db") as store:
        runs = store.list_scan_runs(target="example.com")
        assert runs[0]["status"] == "completed"
        assert runs[0]["error"] == "Discord notification failed (RuntimeError)"


def test_scan_failure_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_discover(target, timeout=10.0, *, on_source=None):
        raise RuntimeError("all passive discovery sources failed")

    monkeypatch.setattr(scanner, "discover_subdomains", fake_discover)

    with pytest.raises(RuntimeError, match="all passive discovery sources failed"):
        asyncio.run(
            run_scan(
                ScanOptions(
                    target="example.com",
                    db_path=tmp_path / "assets.db",
                )
            )
        )

    with AssetStore(tmp_path / "assets.db") as store:
        runs = store.list_scan_runs(target="example.com")
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"
        assert "all passive discovery sources failed" in runs[0]["error"]
