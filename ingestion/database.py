"""Compatibility facade for clean SQLite AIS persistence."""

from pathlib import Path

from sentinel_analysis.domain.entities import AISRecord, Vessel, VesselPosition
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository

DB_PATH = Path("data.db")


def _repository() -> SQLiteAISRepository:
    return SQLiteAISRepository(DB_PATH)


def init_db() -> None:
    _repository().initialize()


def insert_vessels_and_locations(vessels, locations, plugin_name: str) -> int:
    by_mmsi = {item.mmsi: item for item in vessels}
    records = []
    for location in locations:
        vessel = by_mmsi.get(location.mmsi)
        if vessel is not None:
            records.append(
                AISRecord(
                    Vessel(vessel.imo, vessel.mmsi, vessel.vessel_name, vessel.vessel_type, vessel.callsign),
                    VesselPosition(
                        location.mmsi, location.latitude, location.longitude,
                        location.timestamp, location.speed, location.heading,
                    ),
                )
            )
    return _repository().save_records(records, plugin_name)


def log_scraper_execution(plugin_name, status, records_inserted=0, error_message=None) -> None:
    _repository().log_execution(plugin_name, status, records_inserted, error_message)

