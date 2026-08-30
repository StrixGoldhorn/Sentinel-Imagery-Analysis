"""SQLite implementation of the AOI repository."""

import json
from datetime import datetime, timezone
from pathlib import Path

from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


class SQLiteAreaOfInterestRepository:
    def __init__(self, database_path: Path | str, timeout: float = 5) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()

    @staticmethod
    def _from_row(row) -> AreaOfInterest:
        has_auto = "auto_capture_enabled" in row.keys()
        auto_capture = bool(row["auto_capture_enabled"]) if has_auto else False
        return AreaOfInterest(
            id=row["id"],
            name=row["name"],
            bbox=BoundingBox.from_sequence(json.loads(row["bbox"])),
            next_scan=datetime.fromisoformat(row["next_scan"].replace("Z", "+00:00")).astimezone(timezone.utc) if row["next_scan"] else None,
            last_checked=datetime.fromisoformat(row["last_checked"].replace("Z", "+00:00")).astimezone(timezone.utc) if row["last_checked"] else None,
            auto_capture_enabled=auto_capture,
        )

    def list(self) -> list[AreaOfInterest]:
        with self._database.connection(rows=True) as connection:
            rows = connection.execute("SELECT * FROM aoi ORDER BY name").fetchall()
        return [self._from_row(row) for row in rows]

    def add(self, aoi: AreaOfInterest) -> int:
        with self._database.connection(rows=True) as connection:
            cursor = connection.execute(
                "INSERT INTO aoi (name, bbox, auto_capture_enabled) VALUES (?, ?, ?)",
                (aoi.name, json.dumps(aoi.bbox.as_list()), 1 if aoi.auto_capture_enabled else 0),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an AOI identifier")
            return int(cursor.lastrowid)

    def get(self, aoi_id: int) -> AreaOfInterest | None:
        with self._database.connection(rows=True) as connection:
            row = connection.execute("SELECT * FROM aoi WHERE id = ?", (aoi_id,)).fetchone()
        return self._from_row(row) if row else None

    def update_prediction(self, aoi_id: int, next_scan: datetime, last_checked: datetime) -> None:
        with self._database.connection(rows=True) as connection:
            cursor = connection.execute(
                "UPDATE aoi SET next_scan = ?, last_checked = ? WHERE id = ?",
                (next_scan.isoformat(), last_checked.isoformat(), aoi_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"Area of interest not found: {aoi_id}")

    def update_auto_capture(self, aoi_id: int, enabled: bool) -> None:
        with self._database.connection(rows=True) as connection:
            cursor = connection.execute(
                "UPDATE aoi SET auto_capture_enabled = ? WHERE id = ?",
                (1 if enabled else 0, aoi_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"Area of interest not found: {aoi_id}")
