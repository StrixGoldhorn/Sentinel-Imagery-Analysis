"""Command-line interface for AIS ingestion."""

import argparse
from datetime import datetime, timedelta, timezone

from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="AIS Data Scraper Pipeline")
    parser.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    parser.add_argument("--plugin", help="Run only the named plugin")
    parser.add_argument("--hours", type=int, default=24, help="Hours of history to request")
    args = parser.parse_args()

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    settings = Settings.from_environment()
    result = IngestAIS(
        DynamicAISPluginRegistry(),
        SQLiteAISRepository(settings.database_path),
    ).execute(BoundingBox.from_sequence(args.bbox), (start, end), args.plugin)

    print(f"Total records inserted: {result['total_inserted']}")
    for log in result["logs"]:
        print(f"{log['plugin']}: {log['status']} ({log['records']} records)")


if __name__ == "__main__":
    main()

