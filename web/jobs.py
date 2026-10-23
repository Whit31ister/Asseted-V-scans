"""Background scan jobs and event streaming for the web interface."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from core.scanner import ScanOptions, ScanResult, run_scan

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
MAX_EVENTS = 2000
JOB_TTL_SECONDS = 3600
MAX_FINISHED_JOBS = 50


@dataclass(slots=True)
class ScanEvent:
    id: int
    event: str
    data: dict[str, Any]
    timestamp: float


@dataclass
class ScanJob:
    id: str
    options: ScanOptions
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: ScanResult | None = None
    error: str | None = None
    events: deque[ScanEvent] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    last_event_id: int = 0
    task: asyncio.Task[None] | None = None
    _wake: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def emit(self, event: str, data: dict[str, Any]) -> None:
        self.last_event_id += 1
        self.events.append(
            ScanEvent(
                id=self.last_event_id,
                event=event,
                data=data,
                timestamp=time.time(),
            )
        )
        self.updated_at = time.time()
        self._wake.set()

    async def stream(self, cursor: int = 0):
        while True:
            self._wake.clear()
            for event in [item for item in self.events if item.id > cursor]:
                cursor = event.id
                yield event
            if self.terminal:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield None

    def snapshot(self) -> dict[str, Any]:
        result = self.result
        return {
            "id": self.id,
            "target": self.options.target,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_event_id": self.last_event_id,
            "db": str(self.options.db_path),
            "probe": self.options.probe,
            "notifications": bool(self.options.webhook or self.options.telegram),
            "assets": result.assets if result else [],
            "new_assets": result.new_assets if result else [],
            "probes": result.probes if result else {},
            "notification_errors": result.notification_errors if result else [],
            "notifications_sent": result.notifications_sent if result else 0,
        }


class _JobLogHandler(logging.Handler):
    def __init__(self, job: ScanJob) -> None:
        super().__init__()
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self.job.emit(
            "log",
            {"level": record.levelname.lower(), "message": message},
        )


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, ScanJob] = {}
        self._active_id: str | None = None
        self._lock = asyncio.Lock()

    async def start(self, options: ScanOptions) -> ScanJob:
        async with self._lock:
            self._prune()
            if self._active_id is not None:
                active = self._jobs.get(self._active_id)
                if active is not None and not active.terminal:
                    raise RuntimeError("a scan is already running")
                self._active_id = None
            job = ScanJob(id=uuid.uuid4().hex, options=options)
            self._jobs[job.id] = job
            self._active_id = job.id
            job.task = asyncio.create_task(self._run(job))
            await asyncio.sleep(0)
            return job

    async def cancel(self, job_id: str) -> ScanJob:
        job = self.get(job_id)
        if job.terminal:
            return job
        if job.task is not None:
            job.task.cancel()
            try:
                await job.task
            except asyncio.CancelledError:
                pass
        if not job.terminal:
            job.status = "cancelled"
            job.error = "scan cancelled"
            job.emit("cancelled", {"message": "Scan cancelled."})
        if self._active_id == job.id:
            self._active_id = None
        return job

    def get(self, job_id: str) -> ScanJob:
        self._prune()
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def active(self) -> ScanJob | None:
        self._prune()
        if self._active_id is None:
            return None
        job = self._jobs.get(self._active_id)
        if job is None or job.terminal:
            self._active_id = None
            return None
        return job

    async def shutdown(self) -> None:
        active = self.active()
        if active is not None and active.task is not None:
            active.task.cancel()
            try:
                await active.task
            except asyncio.CancelledError:
                pass

    async def _run(self, job: ScanJob) -> None:
        job.status = "running"
        job.emit(
            "status",
            {"status": "running", "target": job.options.target},
        )
        handler = _JobLogHandler(job)
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        handler.setLevel(logging.DEBUG if job.options.verbose else logging.INFO)
        loggers: list[tuple[logging.Logger, int]] = []
        for name in ("core", "database", "utils", "web.jobs"):
            logger = logging.getLogger(name)
            loggers.append((logger, logger.level))
            logger.addHandler(handler)
            if logger.level == logging.NOTSET or logger.level > handler.level:
                logger.setLevel(handler.level)
        try:
            result = await run_scan(job.options, emit=job.emit)
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.error = "scan cancelled"
            job.emit("cancelled", {"message": "Scan cancelled."})
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc) or type(exc).__name__
            job.emit("error", {"message": job.error})
        else:
            job.result = result
            job.status = "completed"
            job.emit(
                "done",
                {
                    "target": result.target,
                    "run_id": result.run_id,
                    "assets": len(result.assets),
                    "new_assets": len(result.new_assets),
                    "probes": sum(
                        len(entries) for entries in result.probes.values()
                    ),
                    "notification_errors": result.notification_errors,
                },
            )
        finally:
            job.updated_at = time.time()
            job.task = None
            if self._active_id == job.id:
                self._active_id = None
            for logger, level in loggers:
                logger.removeHandler(handler)
                logger.setLevel(level)

    def _prune(self) -> None:
        now = time.time()
        finished = [
            job for job in self._jobs.values() if job.terminal
        ]
        for job in finished:
            if now - job.updated_at > JOB_TTL_SECONDS:
                self._jobs.pop(job.id, None)
        finished = sorted(
            (job for job in self._jobs.values() if job.terminal),
            key=lambda job: job.updated_at,
        )
        excess = max(0, len(finished) - MAX_FINISHED_JOBS)
        for job in finished[:excess]:
            self._jobs.pop(job.id, None)
