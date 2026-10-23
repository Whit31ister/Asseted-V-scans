"""FastAPI application for the local asset monitor interface."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.scanner import ScanOptions, normalize_target
from database.state import AssetStore
from web.jobs import JobManager

STATIC_DIR = Path(__file__).resolve().parent / "static"
DISCORD_HOSTS = {
    "discord.com",
    "discordapp.com",
    "ptb.discord.com",
    "canary.discord.com",
}
TELEGRAM_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+/@?[A-Za-z0-9_-]+$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.jobs = JobManager()
    yield
    await app.state.jobs.shutdown()


app = FastAPI(
    title="Asset Monitor",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)


class ScanRequest(BaseModel):
    target: str = Field(min_length=1, max_length=253)
    threads: int = Field(default=50, ge=1, le=500)
    timeout: float = Field(default=10.0, gt=0, le=300)
    db: str = Field(default="data/assets.db", min_length=1, max_length=4096)
    probe: bool = False
    verbose: bool = False
    webhook: str | None = Field(default=None, max_length=2048)
    telegram: str | None = Field(default=None, max_length=512)


def _manager(request: Request) -> JobManager:
    return request.app.state.jobs


def _validate_db(value: str) -> Path:
    value = value.strip()
    if not value or "\x00" in value:
        raise HTTPException(status_code=422, detail="database path is invalid")
    path = Path(value).expanduser()
    if path.exists() and path.is_dir():
        raise HTTPException(
            status_code=422,
            detail="database path must be a file, not a directory",
        )
    return path


def _validate_webhook(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or host not in DISCORD_HOSTS
        or not parsed.path.startswith("/api/webhooks/")
        or len(parsed.path) <= len("/api/webhooks/")
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Discord webhook must be an "
                "https://discord.com/api/webhooks/... URL"
            ),
        )
    return value


def _validate_telegram(value: str) -> str:
    value = value.strip()
    if not TELEGRAM_RE.match(value):
        raise HTTPException(
            status_code=422,
            detail="Telegram value must use BOT_TOKEN/CHAT_ID",
        )
    return value


def _csv_text(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


def _read_history(
    path: Path, action: Callable[[AssetStore], Any]
) -> Any:
    if not path.exists():
        return []
    if not path.is_file():
        raise HTTPException(
            status_code=422,
            detail="database path must be a file",
        )
    try:
        with AssetStore(path) as store:
            return action(store)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"could not read database: {exc}",
        ) from exc


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    active = _manager(request).active()
    return {
        "status": "ok",
        "version": app.version,
        "active_job": active.id if active else None,
    }


@app.post("/api/scans", status_code=202)
async def create_scan(payload: ScanRequest, request: Request) -> dict[str, Any]:
    try:
        target = normalize_target(payload.target)
        webhook = _validate_webhook(payload.webhook) if payload.webhook else None
        telegram = _validate_telegram(payload.telegram) if payload.telegram else None
        options = ScanOptions(
            target=target,
            threads=payload.threads,
            timeout=payload.timeout,
            db_path=_validate_db(payload.db),
            probe=payload.probe,
            webhook=webhook,
            telegram=telegram,
            verbose=payload.verbose,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        job = await _manager(request).start(options)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job.id, "status": job.status}


@app.get("/api/scans/active")
async def active_scan(request: Request) -> dict[str, Any]:
    job = _manager(request).active()
    return {"job": job.snapshot() if job else None}


@app.get("/api/scans/{job_id}")
async def get_scan(job_id: str, request: Request) -> dict[str, Any]:
    try:
        job = _manager(request).get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="scan not found") from exc
    return job.snapshot()


@app.get("/api/scans/{job_id}/events")
async def scan_events(
    job_id: str,
    request: Request,
    last_event_id: int = Query(default=0, ge=0),
    last_event_id_header: str | None = Header(
        default=None, alias="Last-Event-ID"
    ),
) -> StreamingResponse:
    try:
        job = _manager(request).get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="scan not found") from exc
    cursor = last_event_id
    if last_event_id_header:
        try:
            cursor = max(0, int(last_event_id_header))
        except ValueError:
            pass

    async def event_stream():
        async for event in job.stream(cursor):
            if await request.is_disconnected():
                return
            if event is None:
                yield ": keepalive\n\n"
                continue
            data = json.dumps(event.data, separators=(",", ":"))
            yield f"id: {event.id}\nevent: {event.event}\ndata: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/scans/{job_id}/cancel")
async def cancel_scan(job_id: str, request: Request) -> dict[str, Any]:
    try:
        job = await _manager(request).cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="scan not found") from exc
    return job.snapshot()


@app.get("/api/scans/{job_id}/export")
async def export_scan(
    job_id: str,
    request: Request,
    format: str = Query(default="json"),
) -> Response:
    try:
        job = _manager(request).get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="scan not found") from exc
    result = job.result
    if result is None:
        raise HTTPException(
            status_code=409,
            detail="scan has not produced results",
        )
    filename_target = result.target.replace(".", "_")
    if format == "json":
        content = json.dumps(
            {
                "target": result.target,
                "run_id": result.run_id,
                "assets": result.assets,
                "new_assets": result.new_assets,
                "probes": result.probes,
                "notification_errors": result.notification_errors,
            },
            indent=2,
        )
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename_target}-scan.json"'
                )
            },
        )
    if format != "csv":
        raise HTTPException(
            status_code=422,
            detail="format must be json or csv",
        )
    new_hostnames = {
        asset["hostname"] for asset in result.new_assets
    }
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "target",
            "hostname",
            "addresses",
            "is_new",
            "url",
            "status",
            "title",
            "server",
        ]
    )
    for asset in result.assets:
        hostname = asset["hostname"]
        entries = result.probes.get(hostname) or [None]
        for entry in entries:
            writer.writerow(
                [
                    _csv_text(result.target),
                    _csv_text(hostname),
                    _csv_text(" ".join(asset["addresses"])),
                    "yes" if hostname in new_hostnames else "no",
                    _csv_text(entry["url"] if entry else ""),
                    _csv_text(entry["status"] if entry else ""),
                    _csv_text(entry["title"] if entry and entry["title"] else ""),
                    _csv_text(entry["server"] if entry and entry["server"] else ""),
                ]
            )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename_target}-scan.csv"'
            )
        },
    )


@app.get("/api/history/targets")
async def history_targets(
    request: Request,
    db: str = Query(default="data/assets.db", max_length=4096),
) -> dict[str, Any]:
    path = _validate_db(db)
    targets = await asyncio.to_thread(
        _read_history, path, lambda store: store.list_targets()
    )
    return {"targets": targets}


@app.get("/api/history/runs")
async def history_runs(
    request: Request,
    target: str | None = Query(default=None, max_length=253),
    db: str = Query(default="data/assets.db", max_length=4096),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    path = _validate_db(db)
    runs = await asyncio.to_thread(
        _read_history,
        path,
        lambda store: store.list_scan_runs(target=target, limit=limit),
    )
    return {"runs": runs}


@app.get("/api/history/assets")
async def history_assets(
    request: Request,
    target: str | None = Query(default=None, max_length=253),
    search: str | None = Query(default=None, max_length=253),
    db: str = Query(default="data/assets.db", max_length=4096),
    limit: int = Query(default=1000, ge=1, le=2000),
) -> dict[str, Any]:
    path = _validate_db(db)
    assets = await asyncio.to_thread(
        _read_history,
        path,
        lambda store: store.list_assets(
            target=target, search=search, limit=limit
        ),
    )
    return {"assets": assets}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
