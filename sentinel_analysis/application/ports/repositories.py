"""Persistence ports used by application use cases."""

from typing import Protocol

from sentinel_analysis.domain.entities import AISRecord, Scan


class ScanRepository(Protocol):
    def save(self, scan: Scan) -> None:
        ...

    def get(self, folder_name: str) -> Scan | None:
        ...


class AISRepository(Protocol):
    def save_records(self, records: list[AISRecord], source_plugin: str) -> int:
        ...

    def log_execution(
        self,
        plugin_name: str,
        status: str,
        records_inserted: int,
        error_message: str | None = None,
    ) -> None:
        ...

