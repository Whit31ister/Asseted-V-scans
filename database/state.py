"""SQLite persistence and change detection."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from core.resolver import Asset


class AssetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> "AssetStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
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
        self.connection.commit()
        return self

    def __exit__(self, *_: object) -> None:
        if self.connection:
            self.connection.close()

    def record_scan(self, target: str, assets: Iterable[Asset]) -> list[Asset]:
        if self.connection is None:
            raise RuntimeError("AssetStore must be used as a context manager")
        assets = list(assets)
        known = {
            row[0]
            for row in self.connection.execute(
                "SELECT hostname FROM assets WHERE target = ?", (target,)
            )
        }
        new_assets = [asset for asset in assets if asset["hostname"] not in known]
        self.connection.executemany(
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
        self.connection.commit()
        return new_assets
