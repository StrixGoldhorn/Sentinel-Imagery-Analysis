"""Unit and integration tests for storage quotas, scan archival, and cache retention."""

import json
import os
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel_analysis.application.use_cases.manage_storage import (
    ArchiveScan,
    ExecuteStorageRetention,
    GetStorageQuota,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import ArchivalOutcome, StorageQuotaReport
from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.infrastructure.storage.local_storage import LocalStorageManager
from sentinel_analysis.interfaces.web.application import create_app


class TestStorageDomainEntities(unittest.TestCase):
    def test_storage_quota_report_valid(self) -> None:
        report = StorageQuotaReport(
            total_bytes_used=5000,
            quota_bytes=10000,
            usage_percent=50.0,
            scans_bytes=3000,
            cache_bytes=1500,
            database_bytes=500,
            scan_count=3,
            oldest_scan_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            quota_exceeded=False,
        )
        self.assertEqual(report.total_bytes_used, 5000)
        self.assertEqual(report.quota_bytes, 10000)
        self.assertEqual(report.usage_percent, 50.0)
        self.assertFalse(report.quota_exceeded)
        self.assertEqual(report.scan_count, 3)

    def test_storage_quota_report_validation_errors(self) -> None:
        with self.assertRaises(DomainValidationError):
            StorageQuotaReport(
                total_bytes_used=-1,
                quota_bytes=1000,
                usage_percent=0.0,
                scans_bytes=0,
                cache_bytes=0,
                database_bytes=0,
                scan_count=0,
            )
        with self.assertRaises(DomainValidationError):
            StorageQuotaReport(
                total_bytes_used=100,
                quota_bytes=0,  # Quota must be positive
                usage_percent=0.0,
                scans_bytes=0,
                cache_bytes=0,
                database_bytes=0,
                scan_count=0,
            )

    def test_archival_outcome_valid(self) -> None:
        outcome = ArchivalOutcome(
            archived_scans=("scan_1", "scan_2"),
            pruned_cache_files=5,
            bytes_freed=10240,
            archive_paths=("/path/scan_1.tar.gz", "/path/scan_2.tar.gz"),
            timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(outcome.archived_scans), 2)
        self.assertEqual(outcome.pruned_cache_files, 5)
        self.assertEqual(outcome.bytes_freed, 10240)


class TestLocalStorageManager(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "output"
        self.cache_root = self.root / "cache"
        self.db_path = self.root / "data.db"
        self.db_path.touch()
        self.db_path.write_bytes(b"x" * 2048)  # 2 KB database

        self.storage = LocalStorageManager(
            output_root=self.output_root,
            cache_root=self.cache_root,
            database_path=self.db_path,
        )

    def tearDown(self) -> None:
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_mock_scan(self, folder_name: str, days_old: int, file_size: int = 1024) -> Path:
        scan_dir = self.output_root / folder_name
        (scan_dir / "images").mkdir(parents=True, exist_ok=True)
        img_file = scan_dir / "images" / "stitched.png"
        img_file.write_bytes(b"A" * file_size)

        acquired_date = datetime.now(timezone.utc) - timedelta(days=days_old)
        metadata = {
            "acquisition_datetime": acquired_date.isoformat(),
            "satellite": "Sentinel-1",
            "settings": {"bbox": [103.0, 1.0, 104.0, 2.0], "datasource": "sentinel-1-grd"},
        }
        (scan_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        # Adjust folder mtime to simulate age
        old_mtime = time.time() - (days_old * 86400)
        try:
            os.utime(str(scan_dir), (old_mtime, old_mtime))
        except OSError:
            pass
        return scan_dir

    def test_quota_report_calculation(self) -> None:
        self._create_mock_scan("scan_recent", days_old=5, file_size=4096)
        self._create_mock_scan("scan_old", days_old=45, file_size=8192)

        # Create cache tiles
        (self.cache_root / "tile_1.png").write_bytes(b"T" * 1024)
        (self.cache_root / "tile_2.png").write_bytes(b"T" * 2048)

        # Quota: 100 KB
        report = self.storage.get_quota_report(quota_bytes=100 * 1024)
        self.assertEqual(report.scan_count, 2)
        self.assertGreater(report.scans_bytes, 12000)
        self.assertEqual(report.cache_bytes, 3072)
        self.assertEqual(report.database_bytes, 2048)
        self.assertGreater(report.total_bytes_used, 17000)
        self.assertFalse(report.quota_exceeded)
        self.assertIsNotNone(report.oldest_scan_date)

    def test_quota_exceeded_flag(self) -> None:
        self._create_mock_scan("scan_large", days_old=1, file_size=50000)
        # Quota: 10 KB
        report = self.storage.get_quota_report(quota_bytes=10 * 1024)
        self.assertTrue(report.quota_exceeded)
        self.assertGreater(report.usage_percent, 100.0)

    def test_archive_scan_creates_valid_tar(self) -> None:
        scan_dir = self._create_mock_scan("scan_to_archive", days_old=10, file_size=2048)
        archive_path = self.storage.archive_scan("scan_to_archive", remove_original=False)

        self.assertTrue(archive_path.is_file())
        self.assertEqual(archive_path.name, "scan_to_archive.tar.gz")
        self.assertTrue(scan_dir.is_dir())

        # Verify tar contents
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
            self.assertTrue(any("metadata.json" in n for n in names))
            self.assertTrue(any("stitched.png" in n for n in names))

        # Check get_archive_path
        found_path = self.storage.get_archive_path("scan_to_archive")
        self.assertEqual(found_path, archive_path)

    def test_archive_scan_remove_original(self) -> None:
        scan_dir = self._create_mock_scan("scan_to_purge", days_old=10, file_size=2048)
        archive_path = self.storage.archive_scan("scan_to_purge", remove_original=True)

        self.assertTrue(archive_path.is_file())
        self.assertFalse(scan_dir.exists())

    def test_archive_scan_invalid_folder_traversal(self) -> None:
        with self.assertRaises(ValueError):
            self.storage.archive_scan("../evil")
        with self.assertRaises(ValueError):
            self.storage.archive_scan("")
        with self.assertRaises(FileNotFoundError):
            self.storage.archive_scan("non_existent_scan")

    def test_cleanup_retention(self) -> None:
        # Scan 1: 5 days old (should remain)
        recent_scan = self._create_mock_scan("scan_recent", days_old=5, file_size=2048)
        # Scan 2: 40 days old (should be archived)
        old_scan = self._create_mock_scan("scan_expired", days_old=40, file_size=4096)

        # Cache tile 1: recent
        recent_cache = self.cache_root / "tile_recent.png"
        recent_cache.write_bytes(b"C" * 1024)

        # Cache tile 2: 10 days old
        stale_cache = self.cache_root / "tile_stale.png"
        stale_cache.write_bytes(b"C" * 2048)
        old_mtime = time.time() - (10 * 86400)
        os.utime(str(stale_cache), (old_mtime, old_mtime))

        outcome = self.storage.cleanup_retention(scan_retention_days=30, cache_retention_days=7)

        # Scan checks
        self.assertIn("scan_expired", outcome.archived_scans)
        self.assertNotIn("scan_recent", outcome.archived_scans)
        self.assertFalse(old_scan.exists())
        self.assertTrue(recent_scan.is_dir())
        self.assertTrue((self.output_root / "archives" / "scan_expired.tar.gz").is_file())

        # Cache checks
        self.assertEqual(outcome.pruned_cache_files, 1)
        self.assertFalse(stale_cache.exists())
        self.assertTrue(recent_cache.is_file())
        self.assertGreater(outcome.bytes_freed, 0)


class TestStorageUseCases(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.storage = LocalStorageManager(
            output_root=self.root / "output",
            cache_root=self.root / "cache",
            database_path=self.root / "data.db",
        )
        self.get_quota = GetStorageQuota(self.storage, default_quota_bytes=10000)
        self.archive_scan = ArchiveScan(self.storage)
        self.retention = ExecuteStorageRetention(
            self.storage,
            default_scan_retention_days=30,
            default_cache_retention_days=7,
        )

    def tearDown(self) -> None:
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_use_cases_delegation(self) -> None:
        report = self.get_quota.execute()
        self.assertEqual(report.quota_bytes, 10000)

        report_custom = self.get_quota.execute(quota_bytes=50000)
        self.assertEqual(report_custom.quota_bytes, 50000)

        with self.assertRaises(ValueError):
            self.archive_scan.execute("")


class TestStorageApiEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            project_root=root,
            database_path=root / "data.db",
            output_root=root / "output",
            cache_root=root / "cache",
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
            storage_quota_bytes=10 * 1024 * 1024,
            scan_retention_days=30,
            cache_retention_days=7,
        )
        self.container = ApplicationContainer(self.settings)
        self.app = create_app(self.settings, self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        if hasattr(self, "container") and self.container is not None:
            self.container.shutdown(timeout=0.5)
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_scan(self, folder_name: str) -> Path:
        scan_dir = self.settings.output_root / folder_name
        (scan_dir / "images").mkdir(parents=True, exist_ok=True)
        (scan_dir / "images" / "test.png").write_bytes(b"test-image")
        (scan_dir / "metadata.json").write_text(
            json.dumps({"acquisition_datetime": "2026-06-01T10:00:00Z"}),
            encoding="utf-8",
        )
        return scan_dir

    def test_api_quota_endpoint(self) -> None:
        response = self.client.get("/api/storage/quota")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertIn("quota", data)
        self.assertEqual(data["quota"]["quota_bytes"], 10 * 1024 * 1024)
        self.assertEqual(data["quota"]["scan_count"], 0)

    def test_api_archive_endpoint(self) -> None:
        self._create_scan("scan_api_1")
        response = self.client.post("/api/storage/scans/scan_api_1/archive", json={"remove_original": False})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["folder_name"], "scan_api_1")
        self.assertGreater(data["archive_size_bytes"], 0)

        # 404 for missing scan
        res_missing = self.client.post("/api/storage/scans/missing_scan/archive")
        self.assertEqual(res_missing.status_code, 404)

    def test_api_export_endpoint(self) -> None:
        self._create_scan("scan_api_export")
        response = self.client.get("/api/storage/scans/scan_api_export/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/gzip")
        self.assertIn("scan_api_export.tar.gz", response.headers.get("Content-Disposition", ""))
        response.close()

    def test_api_cleanup_endpoint(self) -> None:
        response = self.client.post("/api/storage/cleanup", json={"scan_retention_days": 15, "cache_retention_days": 3})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertIn("outcome", data)
        self.assertIsInstance(data["outcome"]["archived_scans"], list)

        # Invalid param validation
        bad_response = self.client.post("/api/storage/cleanup", json={"scan_retention_days": "not_an_int"})
        self.assertEqual(bad_response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
