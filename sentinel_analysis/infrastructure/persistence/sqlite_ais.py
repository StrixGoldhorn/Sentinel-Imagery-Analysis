from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
import sqlite3

from sentinel_analysis.domain.entities import AISRecord, BoundingBox
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


class SQLiteAISRepository:
    def __init__(self, database_path: Path | str, timeout: float = 5) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()

    def save_records(self, records: Iterable[AISRecord], source_plugin: str) -> int:
        if not isinstance(source_plugin, str) or not source_plugin.strip():
            raise ValueError("AIS source plugin name is required")
        source_plugin = source_plugin.strip()
        inserted = 0
        with self._database.connection() as connection:
            for record in records:
                vessel = record.vessel
                position = record.position
                connection.execute(
                    """
                    INSERT INTO vessels (imo, mmsi, vessel_name, vessel_type, callsign)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(imo, mmsi) DO UPDATE SET
                        vessel_name=excluded.vessel_name,
                        vessel_type=excluded.vessel_type,
                        callsign=excluded.callsign
                    """,
                    (vessel.imo, vessel.mmsi, vessel.name, vessel.vessel_type, vessel.callsign),
                )
                row = connection.execute(
                    "SELECT id FROM vessels WHERE imo = ? AND mmsi = ?",
                    (vessel.imo, vessel.mmsi),
                ).fetchone()
                if row is None:
                    raise sqlite3.IntegrityError("Unable to resolve persisted vessel")
                connection.execute(
                    """
                    INSERT INTO vessel_locations
                    (vessel_id, latitude, longitude, speed, heading, timestamp, source_plugin)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row[0], position.latitude, position.longitude,
                        position.speed, position.heading,
                        position.timestamp.isoformat(), source_plugin,
                    ),
                )
                inserted += 1
        return inserted

    def log_execution(
        self,
        plugin_name: str,
        status: str,
        records_inserted: int,
        error_message: str | None = None,
    ) -> None:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("AIS plugin name is required")
        if status not in {"SUCCESS", "FAILED"}:
            raise ValueError("Invalid execution status: AIS execution status must be SUCCESS or FAILED")
        if isinstance(records_inserted, bool) or not isinstance(records_inserted, int) or records_inserted < 0:
            raise ValueError("Inserted record count must be a non-negative integer")
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO scraper_logs
                (plugin_name, status, records_inserted, error_message)
                VALUES (?, ?, ?, ?)
                """,
                (plugin_name.strip(), status, records_inserted, error_message),
            )

    def get_timeline_bounds(self) -> dict[str, object]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM vessel_locations"
            ).fetchone()
            if row and row[0] is not None and row[1] is not None:
                return {
                    "min_timestamp": row[0],
                    "max_timestamp": row[1],
                    "total_records": row[2],
                    "count": row[2],
                }
            return {
                "min_timestamp": None,
                "max_timestamp": None,
                "total_records": 0,
                "count": 0,
            }

    def get_vessel_positions(
        self,
        bbox: BoundingBox | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        limit: int = 500,
        latest_only: bool = True,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 2000))
        params: list[object] = []
        where_conditions: list[str] = []

        if bbox is not None:
            where_conditions.append("vl.longitude >= ? AND vl.latitude >= ? AND vl.longitude <= ? AND vl.latitude <= ?")
            params.extend([bbox.min_longitude, bbox.min_latitude, bbox.max_longitude, bbox.max_latitude])

        if time_range is not None:
            start, end = time_range
            if start is not None:
                where_conditions.append("vl.timestamp >= ?")
                params.append(start.isoformat())
            if end is not None:
                where_conditions.append("vl.timestamp <= ?")
                params.append(end.isoformat())

        where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""

        if latest_only:
            query = f"""
                WITH ranked AS (
                    SELECT 
                        v.id as vessel_id,
                        v.imo,
                        v.mmsi,
                        v.vessel_name,
                        v.vessel_type,
                        v.callsign,
                        vl.latitude,
                        vl.longitude,
                        vl.speed,
                        vl.heading,
                        vl.timestamp,
                        vl.source_plugin,
                        ROW_NUMBER() OVER (
                            PARTITION BY v.mmsi 
                            ORDER BY vl.timestamp DESC, vl.id DESC
                        ) as rn
                    FROM vessel_locations vl
                    JOIN vessels v ON vl.vessel_id = v.id
                    {where_clause}
                )
                SELECT 
                    vessel_id, imo, mmsi, vessel_name, vessel_type, callsign,
                    latitude, longitude, speed, heading, timestamp, source_plugin
                FROM ranked
                WHERE rn = 1
                ORDER BY timestamp DESC
                LIMIT ?
            """
        else:
            query = f"""
                SELECT 
                    v.id as vessel_id,
                    v.imo,
                    v.mmsi,
                    v.vessel_name,
                    v.vessel_type,
                    v.callsign,
                    vl.latitude,
                    vl.longitude,
                    vl.speed,
                    vl.heading,
                    vl.timestamp,
                    vl.source_plugin
                FROM vessel_locations vl
                JOIN vessels v ON vl.vessel_id = v.id
                {where_clause}
                ORDER BY vl.timestamp DESC
                LIMIT ?
            """
        params.append(limit)

        results: list[dict] = []
        with self._database.connection() as connection:
            cursor = connection.execute(query, tuple(params))
            for row in cursor.fetchall():
                results.append({
                    "vessel_id": row[0],
                    "imo": row[1],
                    "mmsi": row[2],
                    "name": row[3] or f"MMSI: {row[2]}",
                    "type": row[4] or "Unspecified",
                    "callsign": row[5],
                    "latitude": float(row[6]),
                    "longitude": float(row[7]),
                    "speed": float(row[8]) if row[8] is not None else None,
                    "heading": float(row[9]) if row[9] is not None else None,
                    "timestamp": row[10],
                    "source_plugin": row[11],
                })
        return results

    def get_scraper_logs(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        query = """
            SELECT id, plugin_name, status, records_inserted, timestamp, error_message
            FROM scraper_logs
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        """
        results: list[dict] = []
        with self._database.connection() as connection:
            cursor = connection.execute(query, (limit,))
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "plugin_name": row[1],
                    "status": row[2],
                    "records_inserted": row[3],
                    "timestamp": row[4],
                    "error_message": row[5],
                })
        return results

