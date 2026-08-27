"""SQLite implementation of the AIS repository port."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sentinel_analysis.domain.entities import AISRecord


class SQLiteAISRepository:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = str(database_path)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS vessels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    imo TEXT NOT NULL,
                    mmsi TEXT NOT NULL,
                    vessel_name TEXT,
                    vessel_type TEXT,
                    callsign TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (imo, mmsi)
                );
                CREATE TABLE IF NOT EXISTS vessel_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vessel_id INTEGER NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    speed REAL,
                    heading REAL,
                    timestamp DATETIME NOT NULL,
                    source_plugin TEXT NOT NULL,
                    FOREIGN KEY (vessel_id) REFERENCES vessels (id)
                );
                CREATE INDEX IF NOT EXISTS idx_vessel_locations_vessel
                    ON vessel_locations(vessel_id);
                CREATE INDEX IF NOT EXISTS idx_vessel_locations_timestamp
                    ON vessel_locations(timestamp);
                CREATE TABLE IF NOT EXISTS scraper_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    records_inserted INTEGER DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    error_message TEXT
                );
                """
            )

    def save_records(self, records: list[AISRecord], source_plugin: str) -> int:
        inserted = 0
        with self._connection() as connection:
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
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO scraper_logs
                (plugin_name, status, records_inserted, error_message)
                VALUES (?, ?, ?, ?)
                """,
                (plugin_name, status, records_inserted, error_message),
            )
