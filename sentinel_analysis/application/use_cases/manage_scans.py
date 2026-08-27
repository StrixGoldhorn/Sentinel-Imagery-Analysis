"""Queries and commands for stored scans."""

from sentinel_analysis.application.ports.repositories import ScanRepository
from sentinel_analysis.domain.entities import Scan
from sentinel_analysis.domain.exceptions import ScanNotFoundError


class GetScan:
    def __init__(self, scans: ScanRepository) -> None:
        self._scans = scans

    def execute(self, folder_name: str) -> Scan:
        scan = self._scans.get(folder_name)
        if scan is None:
            raise ScanNotFoundError(f"Scan not found: {folder_name}")
        return scan


class ListScans:
    def __init__(self, scans: ScanRepository) -> None:
        self._scans = scans

    def execute(self) -> list[Scan]:
        return self._scans.list()


class RenameScan:
    def __init__(self, scans: ScanRepository) -> None:
        self._scans = scans

    def execute(self, folder_name: str, custom_name: str | None) -> None:
        if self._scans.get(folder_name) is None:
            raise ScanNotFoundError(f"Scan not found: {folder_name}")
        self._scans.update_custom_name(folder_name, custom_name)

