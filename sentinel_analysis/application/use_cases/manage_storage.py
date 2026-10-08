"""Use cases for storage quotas, scan archival, and cache retention."""

from pathlib import Path

from sentinel_analysis.application.ports.storage import StorageManager
from sentinel_analysis.domain.entities import ArchivalOutcome, StorageQuotaReport


class GetStorageQuota:
    """Retrieve storage usage breakdown across scans, tile cache, and database."""

    def __init__(self, storage: StorageManager, default_quota_bytes: int = 10 * 1024 * 1024 * 1024) -> None:
        self._storage = storage
        self._default_quota_bytes = default_quota_bytes

    def execute(self, quota_bytes: int | None = None) -> StorageQuotaReport:
        target_quota = quota_bytes if quota_bytes is not None and quota_bytes > 0 else self._default_quota_bytes
        return self._storage.get_quota_report(target_quota)


class ArchiveScan:
    """Compress a scan workspace into an archived .tar.gz bundle."""

    def __init__(self, storage: StorageManager) -> None:
        self._storage = storage

    def execute(self, folder_name: str, remove_original: bool = False) -> Path:
        if not isinstance(folder_name, str) or not folder_name.strip():
            raise ValueError("Scan folder name is required")
        return self._storage.archive_scan(folder_name.strip(), remove_original=remove_original)


class ExecuteStorageRetention:
    """Enforce retention policies across aging scans and stale cache tiles."""

    def __init__(
        self,
        storage: StorageManager,
        default_scan_retention_days: int = 30,
        default_cache_retention_days: int = 7,
    ) -> None:
        self._storage = storage
        self._default_scan_retention_days = default_scan_retention_days
        self._default_cache_retention_days = default_cache_retention_days

    def execute(
        self,
        scan_retention_days: int | None = None,
        cache_retention_days: int | None = None,
    ) -> ArchivalOutcome:
        s_days = (
            scan_retention_days
            if scan_retention_days is not None and scan_retention_days > 0
            else self._default_scan_retention_days
        )
        c_days = (
            cache_retention_days
            if cache_retention_days is not None and cache_retention_days > 0
            else self._default_cache_retention_days
        )
        return self._storage.cleanup_retention(s_days, c_days)
