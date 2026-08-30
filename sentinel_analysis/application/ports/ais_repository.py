from datetime import datetime
from typing import Iterable, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import AISRecord, BoundingBox


@runtime_checkable
class AISRepository(Protocol):
    """Persist AIS records and ingestion execution outcomes."""

    def save_records(self, records: Iterable[AISRecord], source_plugin: str) -> int:
        ...

    def log_execution(
        self,
        plugin_name: str,
        status: str,
        records_inserted: int,
        error_message: str | None = None,
    ) -> None:
        ...

    def get_vessel_positions(
        self,
        bbox: BoundingBox | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        limit: int = 500,
        latest_only: bool = True,
    ) -> list[dict]:
        ...

    def get_timeline_bounds(self) -> dict[str, object]:
        ...

