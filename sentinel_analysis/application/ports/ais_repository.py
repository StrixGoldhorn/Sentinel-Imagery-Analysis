"""Persistence contract for normalized AIS records and ingestion logs."""

from typing import Iterable, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import AISRecord


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
