"""Compatibility facade for the clean AIS ingestion use case."""

from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository


def run_pipeline(bbox, time_range, db_path: str = "data.db", target_plugin: str | None = None):
    return IngestAIS(
        DynamicAISPluginRegistry(),
        SQLiteAISRepository(db_path),
    ).execute(BoundingBox.from_sequence(bbox), time_range, target_plugin)

