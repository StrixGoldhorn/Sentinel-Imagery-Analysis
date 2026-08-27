"""SQLite implementation of the AOI repository."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox


class SQLiteAreaOfInterestRepository:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = str(database_path)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS aoi (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    bbox TEXT NOT NULL,
                    next_scan TEXT,
                    last_checked TEXT
                )
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AreaOfInterest:
        return AreaOfInterest(
            id=row["id"],
            name=row["name"],
            bbox=BoundingBox.from_sequence(json.loads(row["bbox"])),
            next_scan=datetime.fromisoformat(row["next_scan"].replace("Z", "+00:00")) if row["next_scan"] else None,
            last_checked=datetime.fromisoformat(row["last_checked"].replace("Z", "+00:00")) if row["last_checked"] else None,
        )

    def list(self) -> list[AreaOfInterest]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM aoi ORDER BY name").fetchall()
        return [self._from_row(row) for row in rows]

    def add(self, aoi: AreaOfInterest) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO aoi (name, bbox) VALUES (?, ?)",
                (aoi.name, json.dumps(aoi.bbox.as_list())),
            )
            return int(cursor.lastrowid)

    def get(self, aoi_id: int) -> AreaOfInterest | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM aoi WHERE id = ?", (aoi_id,)).fetchone()
        return self._from_row(row) if row else None

    def update_prediction(self, aoi_id: int, next_scan: datetime, last_checked: datetime) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE aoi SET next_scan = ?, last_checked = ? WHERE id = ?",
                (next_scan.isoformat(), last_checked.isoformat(), aoi_id),
            )
