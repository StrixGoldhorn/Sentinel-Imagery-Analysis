"""Command-line interface for AIS ingestion."""

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import TextIO

from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository
from sentinel_analysis.interfaces.cli.common import CLICommand


class IngestCommand(CLICommand):
    def __init__(
        self,
        use_case: IngestAIS | None = None,
        settings_loader: Callable[[], Settings] = Settings.from_environment,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._use_case = use_case
        self._settings_loader = settings_loader
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Ingest normalized AIS records")
        parser.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
        parser.add_argument("--plugin", help="Run only the named plugin")
        parser.add_argument("--hours", type=int, default=24, help="Hours of history to request (ignored if --pass-time is set)")
        parser.add_argument("--pass-time", help="ISO format satellite pass timestamp to scrape -5min to +5min around")
        return parser

    def _get_use_case(self) -> IngestAIS:
        if self._use_case is not None:
            return self._use_case
        settings = self._settings_loader()
        return IngestAIS(
            DynamicAISPluginRegistry(),
            SQLiteAISRepository(settings.database_path),
        )

    def execute(self, args: argparse.Namespace, stdout: TextIO) -> int:
        if args.pass_time:
            p_time = datetime.fromisoformat(args.pass_time.replace("Z", "+00:00"))
            if p_time.utcoffset() is None:
                p_time = p_time.replace(tzinfo=timezone.utc)
            start = p_time.astimezone(timezone.utc) - timedelta(minutes=5)
            end = p_time.astimezone(timezone.utc) + timedelta(minutes=5)
        else:
            if args.hours <= 0:
                raise ValueError("Hours of history must be positive")
            end = self._clock()
            start = end - timedelta(hours=args.hours)

        result = self._get_use_case().execute(
            BoundingBox.from_sequence(args.bbox),
            (start, end),
            args.plugin,
        )

        print(f"Total records inserted: {result['total_inserted']}", file=stdout)
        for log in result["logs"]:
            suffix = f" - {log['error']}" if log["error"] else ""
            print(f"{log['plugin']}: {log['status']} ({log['records']} records){suffix}", file=stdout)
        return 1 if any(log["status"] == "FAILED" for log in result["logs"]) else 0


def main(argv: Sequence[str] | None = None) -> int:
    return IngestCommand().run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
