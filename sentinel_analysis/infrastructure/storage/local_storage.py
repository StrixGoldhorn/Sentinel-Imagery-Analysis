"""Filesystem storage manager for quotas, compression archival, and retention cleanup."""

import json
import logging
import os
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sentinel_analysis.domain.entities import ArchivalOutcome, StorageQuotaReport

logger = logging.getLogger(__name__)


class LocalStorageManager:
    """Manages disk usage tracking, .tar.gz scan packaging, and cache eviction."""

    def __init__(
        self,
        output_root: Path | str,
        cache_root: Path | str,
        database_path: Path | str,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.cache_root = Path(cache_root).resolve()
        self.database_path = Path(database_path).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _directory_size(target_dir: Path) -> int:
        if not target_dir.is_dir():
            return 0
        total = 0
        try:
            for item in target_dir.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except (OSError, FileNotFoundError):
                        pass
        except (OSError, FileNotFoundError):
            pass
        return total

    def _safe_scan_folder(self, folder_name: str) -> Path:
        if (
            not isinstance(folder_name, str)
            or not folder_name.strip()
            or folder_name != folder_name.strip()
            or Path(folder_name).name != folder_name
            or folder_name in {".", ".."}
        ):
            raise ValueError(f"Invalid scan folder name: {folder_name}")
        scan_dir = (self.output_root / folder_name).resolve()
        if scan_dir.parent != self.output_root:
            raise ValueError(f"Scan directory path traversal rejected: {folder_name}")
        return scan_dir

    def get_quota_report(self, quota_bytes: int) -> StorageQuotaReport:
        if quota_bytes <= 0:
            raise ValueError("quota_bytes must be a positive integer")

        scans_bytes = self._directory_size(self.output_root)
        cache_bytes = self._directory_size(self.cache_root)

        database_bytes = 0
        for db_file in [
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ]:
            if db_file.is_file():
                try:
                    database_bytes += db_file.stat().st_size
                except (OSError, FileNotFoundError):
                    pass

        total_bytes = scans_bytes + cache_bytes + database_bytes
        usage_pct = (total_bytes / quota_bytes) * 100.0 if quota_bytes > 0 else 0.0

        scan_dirs = [
            p for p in self.output_root.iterdir()
            if p.is_dir() and p.name != "archives" and not p.name.startswith(".")
        ]
        scan_count = len(scan_dirs)

        oldest_date: Optional[datetime] = None
        for sdir in scan_dirs:
            meta_path = sdir / "metadata.json"
            dt: Optional[datetime] = None
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    raw_dt = meta.get("acquisition_datetime")
                    if raw_dt:
                        dt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
                except (OSError, ValueError, KeyError):
                    pass
            if dt is None:
                try:
                    dt = datetime.fromtimestamp(sdir.stat().st_mtime, tz=timezone.utc)
                except (OSError, FileNotFoundError):
                    pass
            if dt is not None:
                if oldest_date is None or dt < oldest_date:
                    oldest_date = dt

        return StorageQuotaReport(
            total_bytes_used=total_bytes,
            quota_bytes=quota_bytes,
            usage_percent=usage_pct,
            scans_bytes=scans_bytes,
            cache_bytes=cache_bytes,
            database_bytes=database_bytes,
            scan_count=scan_count,
            oldest_scan_date=oldest_date,
            quota_exceeded=total_bytes > quota_bytes,
        )

    def archive_scan(self, folder_name: str, remove_original: bool = False) -> Path:
        scan_dir = self._safe_scan_folder(folder_name)
        if not scan_dir.is_dir():
            raise FileNotFoundError(f"Scan workspace not found: {folder_name}")

        archive_dir = self.output_root / "archives"
        archive_dir.mkdir(parents=True, exist_ok=True)

        final_archive = archive_dir / f"{folder_name}.tar.gz"
        tmp_archive = archive_dir / f"{folder_name}.tar.gz.tmp"

        try:
            with tarfile.open(tmp_archive, "w:gz") as tar:
                tar.add(scan_dir, arcname=folder_name)
            tmp_archive.replace(final_archive)
        except Exception:
            if tmp_archive.exists():
                tmp_archive.unlink(missing_ok=True)
            raise

        if remove_original:
            try:
                shutil.rmtree(scan_dir)
            except OSError as exc:
                logger.warning("Failed to remove original scan workspace %s: %s", scan_dir, exc)

        return final_archive

    def get_archive_path(self, folder_name: str) -> Optional[Path]:
        clean_name = Path(folder_name).name
        archive_path = self.output_root / "archives" / f"{clean_name}.tar.gz"
        return archive_path if archive_path.is_file() else None

    def cleanup_retention(
        self,
        scan_retention_days: int,
        cache_retention_days: int,
    ) -> ArchivalOutcome:
        now = datetime.now(timezone.utc)
        scan_cutoff = now - timedelta(days=scan_retention_days)
        cache_cutoff = now - timedelta(days=cache_retention_days)

        archived: list[str] = []
        archive_paths: list[str] = []
        bytes_freed = 0
        pruned_cache_files = 0

        # 1. Archive scans older than scan_retention_days
        if self.output_root.is_dir():
            for entry in list(self.output_root.iterdir()):
                if (
                    entry.is_dir()
                    and entry.name != "archives"
                    and not entry.name.startswith(".")
                ):
                    scan_dt: Optional[datetime] = None
                    meta_path = entry / "metadata.json"
                    if meta_path.is_file():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                            raw_dt = meta.get("acquisition_datetime")
                            if raw_dt:
                                scan_dt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
                        except (OSError, ValueError):
                            pass
                    if scan_dt is None:
                        try:
                            scan_dt = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
                        except (OSError, FileNotFoundError):
                            pass

                    if scan_dt is not None and scan_dt < scan_cutoff:
                        before_bytes = self._directory_size(entry)
                        try:
                            archive_file = self.archive_scan(entry.name, remove_original=True)
                            after_bytes = archive_file.stat().st_size
                            freed = max(0, before_bytes - after_bytes)
                            bytes_freed += freed
                            archived.append(entry.name)
                            archive_paths.append(str(archive_file))
                        except Exception as exc:
                            logger.error("Failed to archive expired scan %s: %s", entry.name, exc)

        # 2. Prune cache tiles older than cache_retention_days
        if self.cache_root.is_dir():
            for cfile in list(self.cache_root.iterdir()):
                if cfile.is_file():
                    try:
                        file_mtime = datetime.fromtimestamp(cfile.stat().st_mtime, tz=timezone.utc)
                        if file_mtime < cache_cutoff:
                            fsize = cfile.stat().st_size
                            cfile.unlink(missing_ok=True)
                            bytes_freed += fsize
                            pruned_cache_files += 1
                    except (OSError, FileNotFoundError):
                        pass

        return ArchivalOutcome(
            archived_scans=tuple(archived),
            pruned_cache_files=pruned_cache_files,
            bytes_freed=bytes_freed,
            archive_paths=tuple(archive_paths),
            timestamp=now,
        )
