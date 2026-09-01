"""Unit tests for planned scrapes scheduling, persistence, and worker status."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from sentinel_analysis.application.use_cases.get_schedule import GetUpcomingScrapes
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.infrastructure.persistence.database import SQLiteDatabase
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.infrastructure.scheduler.pass_scheduler import PassSchedulerWorker


class StubPredictor:
    def __init__(self, predictions=None):
        self.predictions = predictions or []

    def predict(self, bbox, api_key):
        return self.predictions


class StubAOIRepo:
    def __init__(self, aois):
        self._aois = aois

    def list(self):
        return self._aois

    def get(self, aoi_id):
        for a in self._aois:
            if a.id == aoi_id:
                return a
        return None

    def update_prediction(self, aoi_id, next_scan, last_checked):
        pass


class TestGetUpcomingScrapes(unittest.TestCase):
    def test_get_upcoming_scrapes_sorting_and_filtering(self):
        now = datetime.now(timezone.utc)
        pass1 = now + timedelta(hours=2)
        pass2 = now + timedelta(days=2)
        past_pass = now - timedelta(hours=1)

        aoi1 = AreaOfInterest(
            id=1,
            name="Singapore Port",
            bbox=BoundingBox(103.8, 1.2, 103.9, 1.3),
            auto_capture_enabled=True,
        )
        aoi2 = AreaOfInterest(
            id=2,
            name="Malacca North",
            bbox=BoundingBox(100.1, 4.2, 100.5, 4.5),
            auto_capture_enabled=False,
        )

        predictions = [
            {
                "time": pass2.isoformat(),
                "satellite": "Sentinel-1A",
                "orbit_direction": "ASCENDING",
                "relative_orbit": 171,
                "confidence_score": 0.9,
                "max_elevation": 55,
                "source": "COMBINED",
            },
            {
                "time": pass1.isoformat(),
                "satellite": "Sentinel-1C",
                "orbit_direction": "DESCENDING",
                "relative_orbit": 45,
                "confidence_score": 0.85,
                "max_elevation": 68,
                "source": "COMBINED",
            },
            {
                "time": past_pass.isoformat(),
                "satellite": "Sentinel-1A",
            },
        ]

        predictor = StubPredictor(predictions)
        repo = StubAOIRepo([aoi1, aoi2])
        use_case = GetUpcomingScrapes(repo, predictor)

        # Test full list
        result = use_case.execute(api_key="test_key", days_ahead=7)
        events = result["events"]
        metrics = result["metrics"]

        self.assertEqual(metrics["total_aois"], 2)
        self.assertEqual(metrics["auto_capture_count"], 1)
        self.assertEqual(len(events), 4)  # 2 future passes per AOI

        # Verify sorted chronologically
        for i in range(len(events) - 1):
            self.assertLessEqual(events[i]["pass_time"], events[i + 1]["pass_time"])

        # Test auto_capture_only filter
        result_auto = use_case.execute(api_key="test_key", auto_capture_only=True)
        self.assertEqual(len(result_auto["events"]), 2)
        for e in result_auto["events"]:
            self.assertTrue(e["auto_capture_enabled"])
            self.assertEqual(e["aoi_id"], 1)

    def test_missing_api_key_raises_error(self):
        repo = StubAOIRepo([])
        predictor = StubPredictor([])
        use_case = GetUpcomingScrapes(repo, predictor)
        with self.assertRaises(ValueError):
            use_case.execute(api_key="")


class TestSchedulerWorkerAndLogs(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("tests/runtime/test_schedule_db.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        self.db = SQLiteDatabase(self.db_path)
        self.db.initialize()
        self.ais_repo = SQLiteAISRepository(self.db_path)
        self.aoi_repo = SQLiteAreaOfInterestRepository(self.db_path)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_scraper_logs_persistence(self):
        self.ais_repo.log_execution("MockPluginA", "SUCCESS", 42, None)
        self.ais_repo.log_execution("MockPluginB", "FAILED", 0, "Network timeout")

        logs = self.ais_repo.get_scraper_logs(limit=10)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["plugin_name"], "MockPluginB")
        self.assertEqual(logs[0]["status"], "FAILED")
        self.assertEqual(logs[0]["error_message"], "Network timeout")
        self.assertEqual(logs[1]["plugin_name"], "MockPluginA")
        self.assertEqual(logs[1]["records_inserted"], 42)

    def test_pass_scheduler_worker_status_and_trigger(self):
        check_aois = CheckAndScheduleAOIs(self.aoi_repo, StubPredictor([]))
        worker = PassSchedulerWorker(check_aois, api_key="dummy_key", poll_interval_seconds=60.0)

        status = worker.get_status()
        self.assertFalse(status["is_running"])
        self.assertTrue(status["api_key_configured"])
        self.assertIsNone(status["last_run_at"])

        results = worker.trigger_check()
        self.assertEqual(results, [])

        status_after = worker.get_status()
        self.assertIsNotNone(status_after["last_run_at"])
        self.assertIsNone(status_after["last_error"])


if __name__ == "__main__":
    unittest.main()
