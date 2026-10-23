import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.jobs as jobs
from core.scanner import ScanResult
from database.state import AssetStore
from web.app import _csv_text, app


def _result(options, *, probe=False) -> ScanResult:
    asset = {"hostname": "api.example.com", "addresses": ["192.0.2.10"]}
    probes = (
        {
            "api.example.com": [
                {
                    "url": "https://api.example.com/",
                    "status": 200,
                    "title": "API",
                    "server": "test",
                }
            ]
        }
        if probe
        else {}
    )
    return ScanResult(
        target=options.target,
        run_id="test-run",
        assets=[asset],
        new_assets=[asset],
        probes=probes,
    )


def wait_for_status(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/scans/{job_id}")
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] in {"completed", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"scan {job_id} did not finish")


def test_scan_api_stream_and_exports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run(options, emit=None):
        if emit:
            emit("source", {"name": "crt.sh", "ok": True, "count": 1, "error": None})
            emit(
                "asset",
                {
                    "hostname": "api.example.com",
                    "addresses": ["192.0.2.10"],
                    "is_new": True,
                },
            )
            emit(
                "probe",
                {
                    "hostname": "api.example.com",
                    "probes": [
                        {
                            "url": "https://api.example.com/",
                            "status": 200,
                            "title": "API",
                            "server": "test",
                        }
                    ],
                },
            )
        return _result(options, probe=True)

    monkeypatch.setattr(jobs, "run_scan", fake_run)

    with TestClient(app) as client:
        response = client.post(
            "/api/scans",
            json={
                "target": "example.com",
                "db": str(tmp_path / "assets.db"),
                "probe": True,
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        snapshot = wait_for_status(client, job_id)
        assert snapshot["status"] == "completed"
        assert snapshot["assets"][0]["hostname"] == "api.example.com"
        assert snapshot["new_assets"][0]["hostname"] == "api.example.com"
        assert snapshot["probes"]["api.example.com"][0]["status"] == 200
        assert snapshot["probe"] is True

        with client.stream(
            "GET", f"/api/scans/{job_id}/events"
        ) as events_response:
            assert events_response.status_code == 200
            body = "".join(events_response.iter_text())
        assert "event: done" in body
        assert "event: asset" in body
        assert "event: probe" in body

        json_export = client.get(f"/api/scans/{job_id}/export?format=json")
        assert json_export.status_code == 200
        assert json_export.json()["target"] == "example.com"
        assert "attachment" in json_export.headers["content-disposition"]

        csv_export = client.get(f"/api/scans/{job_id}/export?format=csv")
        assert csv_export.status_code == 200
        assert "api.example.com" in csv_export.text
        assert "https://api.example.com/" in csv_export.text

        assert client.get(f"/api/scans/{job_id}/export?format=xml").status_code == 422


def test_validation_conflict_and_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def slow_run(options, emit=None):
        await asyncio.sleep(60)
        return _result(options)

    monkeypatch.setattr(jobs, "run_scan", slow_run)

    with TestClient(app) as client:
        invalid_target = client.post(
            "/api/scans",
            json={"target": "not-a-domain", "db": str(tmp_path / "a.db")},
        )
        assert invalid_target.status_code == 422

        invalid_webhook = client.post(
            "/api/scans",
            json={
                "target": "example.com",
                "db": str(tmp_path / "a.db"),
                "webhook": "https://example.com/hook",
            },
        )
        assert invalid_webhook.status_code == 422

        invalid_telegram = client.post(
            "/api/scans",
            json={
                "target": "example.com",
                "db": str(tmp_path / "a.db"),
                "telegram": "not-a-token",
            },
        )
        assert invalid_telegram.status_code == 422

        invalid_threads = client.post(
            "/api/scans",
            json={
                "target": "example.com",
                "threads": 0,
                "db": str(tmp_path / "a.db"),
            },
        )
        assert invalid_threads.status_code == 422

        first = client.post(
            "/api/scans",
            json={
                "target": "example.com",
                "db": str(tmp_path / "a.db"),
            },
        )
        assert first.status_code == 202
        job_id = first.json()["job_id"]

        active = client.get("/api/scans/active").json()["job"]
        assert active is not None
        assert active["id"] == job_id

        second = client.post(
            "/api/scans",
            json={
                "target": "example.org",
                "db": str(tmp_path / "b.db"),
            },
        )
        assert second.status_code == 409

        cancelled = client.post(f"/api/scans/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert client.get("/api/scans/active").json()["job"] is None
        assert client.get("/api/scans/missing").status_code == 404


def test_history_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    with AssetStore(path) as store:
        run_id = store.start_scan_run("example.com")
        store.record_scan(
            "example.com",
            [
                {
                    "hostname": "api.example.com",
                    "addresses": ["192.0.2.10"],
                }
            ],
        )
        store.finish_scan_run(
            run_id,
            "completed",
            asset_count=1,
            new_count=1,
        )

    with TestClient(app) as client:
        targets = client.get(
            "/api/history/targets", params={"db": str(path)}
        )
        assert targets.status_code == 200
        assert targets.json()["targets"] == ["example.com"]

        runs = client.get("/api/history/runs", params={"db": str(path)})
        assert runs.status_code == 200
        assert runs.json()["runs"][0]["status"] == "completed"

        assets = client.get(
            "/api/history/assets",
            params={"db": str(path), "search": "api"},
        )
        assert assets.status_code == 200
        assert assets.json()["assets"][0]["addresses"] == ["192.0.2.10"]

        missing = client.get(
            "/api/history/targets", params={"db": str(tmp_path / "missing.db")}
        )
        assert missing.status_code == 200
        assert missing.json()["targets"] == []

        directory = client.get(
            "/api/history/targets", params={"db": str(tmp_path)}
        )
        assert directory.status_code == 422


def test_csv_formula_values_are_escaped() -> None:
    assert _csv_text("=cmd|'/c calc'!A0") == "'=cmd|'/c calc'!A0"
    assert _csv_text("+1") == "'+1"
    assert _csv_text("-1") == "'-1"
    assert _csv_text("@SUM(A1)") == "'@SUM(A1)"
    assert _csv_text("api.example.com") == "api.example.com"
    assert _csv_text(None) == ""


def test_health_and_index() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["active_job"] is None

        index = client.get("/")
        assert index.status_code == 200
        assert "Asset Monitor" in index.text

        static = client.get("/static/app.js")
        assert static.status_code == 200
