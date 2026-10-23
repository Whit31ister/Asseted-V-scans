"""SQLite persistence and change detection."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable

from core.resolver import Asset


class AssetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> "AssetStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
                target TEXT NOT NULL,
                hostname TEXT NOT NULL,
                addresses TEXT NOT NULL,
                first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (target, hostname)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                id TEXT PRIMARY KEY,
                target TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                status TEXT NOT NULL,
                asset_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                probe_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scan_runs_target_started
            ON scan_runs (target, started_at DESC)
            """
        )
        self.connection.commit()
        return self

    def __exit__(self, *_: object) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("AssetStore must be used as a context manager")
        return self.connection

    def record_scan(self, target: str, assets: Iterable[Asset]) -> list[Asset]:
        connection = self._connection()
        assets = list(assets)
        known = self.known_hostnames(target)
        new_assets = [asset for asset in assets if asset["hostname"] not in known]
        connection.executemany(
            """
            INSERT INTO assets (target, hostname, addresses)
            VALUES (?, ?, ?)
            ON CONFLICT(target, hostname) DO UPDATE SET
                addresses = excluded.addresses,
                last_seen = CURRENT_TIMESTAMP
            """,
            [
                (target, asset["hostname"], json.dumps(asset["addresses"]))
                for asset in assets
            ],
        )
        connection.commit()
        return new_assets

    def known_hostnames(self, target: str) -> set[str]:
        connection = self._connection()
        return {
            row[0]
            for row in connection.execute(
                "SELECT hostname FROM assets WHERE target = ?", (target,)
            )
        }

    def start_scan_run(self, target: str, run_id: str | None = None) -> str:
        connection = self._connection()
        run_id = run_id or uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO scan_runs (id, target, started_at, status)
            VALUES (?, ?, CURRENT_TIMESTAMP, 'running')
            """,
            (run_id, target),
        )
        connection.commit()
        return run_id

    def finish_scan_run(
        self,
        run_id: str,
        status: str,
        *,
        asset_count: int = 0,
        new_count: int = 0,
        probe_count: int = 0,
        error: str | None = None,
    ) -> None:
        connection = self._connection()
        connection.execute(
            """
            UPDATE scan_runs
            SET finished_at = CURRENT_TIMESTAMP,
                status = ?,
                asset_count = ?,
                new_count = ?,
                probe_count = ?,
                error = ?
            WHERE id = ?
            """,
            (status, asset_count, new_count, probe_count, error, run_id),
        )
        connection.commit()

    def list_scan_runs(
        self, target: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        connection = self._connection()
        limit = max(1, min(limit, 500))
        if target:
            rows = connection.execute(
                """
                SELECT id, target, started_at, finished_at, status,
                       asset_count, new_count, probe_count, error
                FROM scan_runs
                WHERE target = ?
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                (target, limit),
            )
        else:
            rows = connection.execute(
                """
                SELECT id, target, started_at, finished_at, status,
                       asset_count, new_count, probe_count, error
                FROM scan_runs
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in rows]

    def list_assets(
        self,
        target: str | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        connection = self._connection()
        limit = max(1, min(limit, 2000))
        clauses: list[str] = []
        params: list[Any] = []
        if target:
            clauses.append("target = ?")
            params.append(target)
        if search:
            clauses.append("hostname LIKE ?")
            params.append(f"%{search}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = connection.execute(
            f"""
            SELECT target, hostname, addresses, first_seen, last_seen
            FROM assets
            {where}
            ORDER BY hostname
            LIMIT ?
            """,
            params,
        )
        assets: list[dict[str, Any]] = []
        for row in rows:
            try:
                addresses = json.loads(row["addresses"])
            except (TypeError, ValueError):
                addresses = []
            assets.append(
                {
                    "target": row["target"],
                    "hostname": row["hostname"],
                    "addresses": addresses if isinstance(addresses, list) else [],
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                }
            )
        return assets

    def list_targets(self) -> list[str]:
        connection = self._connection()
        rows = connection.execute(
            """
            SELECT target FROM assets
            UNION
            SELECT target FROM scan_runs
            ORDER BY target
            """
        )
        return [row[0] for row in rows]
