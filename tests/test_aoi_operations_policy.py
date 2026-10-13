"""Tests for AOI priority, collection budget, desired revisit rate, and retention policy."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.application.use_cases.manage_aois import (
    AddAreaOfInterest,
    EnforceAoiRetentionPolicy,
    RecordAoiExpenditure,
    UpdateAreaOfInterestPolicy,
)
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox, Scan
from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.interfaces.web.serialization import serialize_aoi


class FakeScanRepo:
    def __init__(self, scans=None):
        self._scans = list(scans or [])
        self.deleted_ids = []

    def list(self):
        return [s for s in self._scans if s.id not in self.deleted_ids]

    def delete(self, scan_id):
        self.deleted_ids.append(scan_id)


class TestAoiOperationsPolicy(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.repo = SQLiteAreaOfInterestRepository(self.db_path)
        self.bbox = BoundingBox(10.0, 20.0, 11.0, 21.0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_domain_validation_priority(self):
        aoi = AreaOfInterest(
            name="Test AOI",
            bbox=self.bbox,
            priority="CRITICAL",
        )
        self.assertEqual(aoi.priority, "CRITICAL")

        with self.assertRaises(DomainValidationError):
            AreaOfInterest(name="Bad", bbox=self.bbox, priority="SUPER_HIGH")

    def test_domain_validation_budget_and_revisit(self):
        aoi = AreaOfInterest(
            name="Budget AOI",
            bbox=self.bbox,
            collection_budget_usd=5000.0,
            desired_revisit_rate_hours=12.0,
            retention_policy_days=30,
        )
        self.assertEqual(aoi.collection_budget_usd, 5000.0)
        self.assertEqual(aoi.desired_revisit_rate_hours, 12.0)
        self.assertEqual(aoi.retention_policy_days, 30)

        with self.assertRaises(DomainValidationError):
            AreaOfInterest(name="Bad", bbox=self.bbox, collection_budget_usd=-10)

        with self.assertRaises(DomainValidationError):
            AreaOfInterest(name="Bad", bbox=self.bbox, desired_revisit_rate_hours=0)

        with self.assertRaises(DomainValidationError):
            AreaOfInterest(name="Bad", bbox=self.bbox, retention_policy_days=-5)

    def test_sqlite_persistence_and_update(self):
        aoi_id = self.repo.add(
            AreaOfInterest(
                name="Persisted AOI",
                bbox=self.bbox,
                priority="HIGH",
                collection_budget_usd=1000.0,
                desired_revisit_rate_hours=24.0,
                retention_policy_days=60,
            )
        )
        loaded = self.repo.get(aoi_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.priority, "HIGH")
        self.assertEqual(loaded.collection_budget_usd, 1000.0)
        self.assertEqual(loaded.budget_spent_usd, 0.0)
        self.assertEqual(loaded.desired_revisit_rate_hours, 24.0)
        self.assertEqual(loaded.retention_policy_days, 60)

        # Update policy
        updated = self.repo.update_operations_policy(
            aoi_id=aoi_id,
            priority="CRITICAL",
            collection_budget_usd=2500.0,
            desired_revisit_rate_hours=6.0,
            retention_policy_days=90,
        )
        self.assertEqual(updated.priority, "CRITICAL")
        self.assertEqual(updated.collection_budget_usd, 2500.0)
        self.assertEqual(updated.desired_revisit_rate_hours, 6.0)
        self.assertEqual(updated.retention_policy_days, 90)

        # Record expenditure
        with_spent = self.repo.record_expenditure(aoi_id, 350.0)
        self.assertEqual(with_spent.budget_spent_usd, 350.0)

        reloaded = self.repo.get(aoi_id)
        self.assertEqual(reloaded.budget_spent_usd, 350.0)

    def test_use_cases(self):
        add_uc = AddAreaOfInterest(self.repo)
        update_uc = UpdateAreaOfInterestPolicy(self.repo)
        spend_uc = RecordAoiExpenditure(self.repo)

        aoi_id = add_uc.execute(
            name="Alpha Port",
            bbox=self.bbox,
            priority="LOW",
            collection_budget_usd=500.0,
        )

        updated = update_uc.execute(aoi_id, priority="HIGH", collection_budget_usd=1200.0)
        self.assertEqual(updated.priority, "HIGH")
        self.assertEqual(updated.collection_budget_usd, 1200.0)

        spent = spend_uc.execute(aoi_id, 250.0)
        self.assertEqual(spent.budget_spent_usd, 250.0)

        with self.assertRaises(AreaOfInterestNotFoundError):
            update_uc.execute(99999, priority="HIGH")

        with self.assertRaises(AreaOfInterestNotFoundError):
            spend_uc.execute(99999, 100.0)

    def test_enforce_retention_policy(self):
        aoi_id = self.repo.add(
            AreaOfInterest(
                name="Retention AOI",
                bbox=self.bbox,
                retention_policy_days=10,
            )
        )
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=20)
        recent_time = now - timedelta(days=5)

        class MockScan:
            def __init__(self, scan_id, aoi_name, acquired_at):
                self.id = scan_id
                self.aoi_name = aoi_name
                self.acquired_at = acquired_at

        fake_scans = FakeScanRepo([
            MockScan(1, "Retention AOI", old_time),
            MockScan(2, "Retention AOI", recent_time),
            MockScan(3, "Other AOI", old_time),
        ])

        enforce_uc = EnforceAoiRetentionPolicy(self.repo, fake_scans)
        result = enforce_uc.execute(aoi_id)

        self.assertEqual(result["aoi_id"], aoi_id)
        self.assertEqual(result["pruned_scans"], 1)
        self.assertIn(1, result["pruned_scan_ids"])
        self.assertNotIn(2, fake_scans.deleted_ids)
        self.assertNotIn(3, fake_scans.deleted_ids)

    def test_serialization(self):
        aoi = AreaOfInterest(
            id=12,
            name="Serialization AOI",
            bbox=self.bbox,
            priority="CRITICAL",
            collection_budget_usd=2000.0,
            budget_spent_usd=500.0,
            desired_revisit_rate_hours=8.0,
            retention_policy_days=45,
        )
        serialized = serialize_aoi(aoi)
        self.assertEqual(serialized["priority"], "CRITICAL")
        self.assertEqual(serialized["collection_budget_usd"], 2000.0)
        self.assertEqual(serialized["budget_spent_usd"], 500.0)
        self.assertEqual(serialized["budget_remaining_usd"], 1500.0)
        self.assertEqual(serialized["desired_revisit_rate_hours"], 8.0)
        self.assertEqual(serialized["retention_policy_days"], 45)

    def test_scheduling_prioritization_and_budget_exhaustion(self):
        from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs

        class StubPredictor:
            def __init__(self):
                self.calls = []

            def predict(self, bbox, api_key):
                self.calls.append(bbox)
                return []

        # Add 3 AOIs: Low, Critical (budget exhausted), High
        aoi_low = AreaOfInterest(id=1, name="Low AOI", bbox=self.bbox, auto_capture_enabled=True, priority="LOW")
        aoi_crit_exhausted = AreaOfInterest(
            id=2, name="Crit AOI", bbox=self.bbox, auto_capture_enabled=True, priority="CRITICAL",
            collection_budget_usd=100.0, budget_spent_usd=100.0
        )
        aoi_high = AreaOfInterest(id=3, name="High AOI", bbox=self.bbox, auto_capture_enabled=True, priority="HIGH")

        class StubRepo:
            def list(self):
                return [aoi_low, aoi_crit_exhausted, aoi_high]

        predictor = StubPredictor()
        scheduler = CheckAndScheduleAOIs(aoi_repository=StubRepo(), pass_predictor=predictor)
        results = scheduler.execute(api_key="test-key")

        # Crit AOI is evaluated first due to priority, but skipped due to budget
        budget_exhausted_results = [r for r in results if r.get("status") == "budget_exhausted"]
        self.assertEqual(len(budget_exhausted_results), 1)
        self.assertEqual(budget_exhausted_results[0]["aoi_id"], 2)
        self.assertEqual(budget_exhausted_results[0]["priority"], "CRITICAL")


if __name__ == "__main__":
    unittest.main()
