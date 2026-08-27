"""Queries and commands for stored scans."""

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import Scan
from sentinel_analysis.domain.exceptions import ScanNotFoundError


class GetScan:
    def __init__(self, scans: ScanRepository) -> None:
        self._scans = scans

    def execute(self, folder_name: str) -> Scan:
        folder_name = self._validated_folder_name(folder_name)
        scan = self._scans.get(folder_name)
        if scan is None:
            raise ScanNotFoundError(f"Scan not found: {folder_name}")
        return scan

    @staticmethod
    def _validated_folder_name(folder_name: str) -> str:
        if not isinstance(folder_name, str) or not folder_name.strip():
            raise ValueError("Scan folder name is required")
        return folder_name.strip()


class ListScans:
    def __init__(self, scans: ScanRepository) -> None:
        self._scans = scans

    def execute(self) -> list[Scan]:
        return list(self._scans.list())


class RenameScan:
    def __init__(self, scans: ScanRepository) -> None:
        self._scans = scans

    def execute(self, folder_name: str, custom_name: str | None) -> None:
        folder_name = GetScan._validated_folder_name(folder_name)
        if custom_name is not None:
            if not isinstance(custom_name, str):
                raise ValueError("Custom scan name must be a string or null")
            custom_name = custom_name.strip() or None
        if self._scans.get(folder_name) is None:
            raise ScanNotFoundError(f"Scan not found: {folder_name}")
        self._scans.update_custom_name(folder_name, custom_name)
