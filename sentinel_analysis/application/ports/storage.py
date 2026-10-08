"""Application port for storage management, archival, and quota reporting."""

from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import ArchivalOutcome, StorageQuotaReport


@runtime_checkable
class StorageManager(Protocol):
    """Protocol for disk usage tracking, scan archival, and cache retention."""

    def get_quota_report(self, quota_bytes: int) -> StorageQuotaReport:
        """Calculate disk usage breakdown across scans, cache, and database."""
        ...

    def archive_scan(self, folder_name: str, remove_original: bool = False) -> Path:
        """Compress a scan workspace into a .tar.gz archive and return archive path."""
        ...

    def get_archive_path(self, folder_name: str) -> Optional[Path]:
        """Return the path to an existing .tar.gz archive for a scan if present."""
        ...

    def cleanup_retention(
        self,
        scan_retention_days: int,
        cache_retention_days: int,
    ) -> ArchivalOutcome:
        """Archive scans older than scan_retention_days and prune cache older than cache_retention_days."""
        ...
