"""Persistence ports used by application use cases."""

from typing import Protocol

from datetime import datetime
from pathlib import Path

from sentinel_analysis.domain.entities import AISRecord, AreaOfInterest, Scan


class ScanRepository(Protocol):
    def prepare(self, folder_name: str) -> Path:
        ...

    def save(self, scan: Scan) -> None:
        ...

    def get(self, folder_name: str) -> Scan | None:
        ...

    def list(self) -> list[Scan]:
        ...

    def update_custom_name(self, folder_name: str, custom_name: str | None) -> None:
        ...

    def delete(self, folder_name: str) -> None:
        ...


class AreaOfInterestRepository(Protocol):
    def list(self) -> list[AreaOfInterest]:
        ...

    def add(self, aoi: AreaOfInterest) -> int:
        ...

    def get(self, aoi_id: int) -> AreaOfInterest | None:
        ...

    def update_prediction(
        self,
        aoi_id: int,
        next_scan: datetime,
        last_checked: datetime,
    ) -> None:
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
