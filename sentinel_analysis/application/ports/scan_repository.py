"""Persistence contract for imagery scans."""

from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from sentinel_analysis.domain.entities import Scan


@runtime_checkable
class ScanRepository(Protocol):
    """Persist scan metadata and manage scan workspaces."""

    def prepare(self, folder_name: str) -> Path:
        ...

    def save(self, scan: Scan) -> None:
        ...

    def get(self, folder_name: str) -> Scan | None:
        ...

    def list(self) -> Sequence[Scan]:
        ...

    def update_custom_name(self, folder_name: str, custom_name: str | None) -> None:
        ...

    def delete(self, folder_name: str) -> None:
        ...
