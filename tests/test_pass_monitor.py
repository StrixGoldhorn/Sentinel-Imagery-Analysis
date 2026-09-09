"""Unit tests for the dedicated BackgroundPassMonitor."""

from datetime import datetime, timedelta, timezone
import time
import unittest
import threading

from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.infrastructure.scheduler.pass_monitor import BackgroundPassMonitor


BBOX = BoundingBox(103.0, 1.0, 104.0, 2.0)


class StubIngestAIS:
    def __init__(self, records_to_insert: int = 5):
        self.calls: list[tuple] = []
        self.records_to_insert = records_to_insert

    def execute(self, bbox, time_range, trigger_reason=None):
        self.calls.append((bbox, time_range, trigger_reason))
        return {"total_inserted": self.records_to_insert}


class StubPostPassRepo:
    def __init__(self):
        self.jobs = {}
        self._next_id = 1

    def find_by_aoi_and_pass(self, aoi_id, pass_time):
        for job in self.jobs.values():
            if job.aoi_id == aoi_id and job.pass_time == pass_time:
                return job
        return None

    def add(self, job):
        job_id = self._next_id
        self._next_id += 1
        stored = job
        if stored.id is None:
            object.__setattr__(stored, "id", job_id)
        self.jobs[job_id] = stored
        return job_id

    def update(self, job):
        if job.id in self.jobs:
            self.jobs[job.id] = job

    def get_active_jobs(self):
        return [j for j in self.jobs.values() if j.status in ("PENDING_PASS", "POLLING_CATALOG")]


class TestBackgroundPassMonitor(unittest.TestCase):
    @staticmethod
    def wait_until(predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def test_immediate_monitoring_and_ais_scraping(self):
        ingest_stub = StubIngestAIS(records_to_insert=7)
        repo_stub = StubPostPassRepo()
        # Fast interval for testing
        monitor = BackgroundPassMonitor(
            ingest_ais=ingest_stub,
            post_pass_repo=repo_stub,
            interval_seconds=0.1,
        )

        aoi = AreaOfInterest(name="Test AOI", bbox=BBOX, id=101)
        now = datetime.now(timezone.utc)
        # Window from now - 0.2s to now + 0.3s (pass_time is now, window_minutes=0.005 ~= 0.3s)
        window_minutes = 0.005  # ~0.3 seconds

        entry = monitor.schedule_or_start(
            aoi,
            pass_time=now,
            active_pass_info={"satellite": "Sentinel-1A", "contribution": "both"},
            window_minutes=window_minutes,
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry["aoi_id"], 101)
        # Wait for loop to tick at least once
        time.sleep(0.2)

        self.assertGreaterEqual(len(ingest_stub.calls), 1)
        # Post-pass job should be registered upon active autoscan
        job = repo_stub.find_by_aoi_and_pass(101, now)
        self.assertIsNotNone(job)
        self.assertEqual(job.aoi_name, "Test AOI")

        # Wait for window to finish
        time.sleep(0.25)
        monitor.stop_all()

    def test_n2yo_only_pass_does_not_trigger_automatic_scrape_or_job(self):
        ingest_stub = StubIngestAIS(records_to_insert=3)
        repo_stub = StubPostPassRepo()
        monitor = BackgroundPassMonitor(
            ingest_ais=ingest_stub,
            post_pass_repo=repo_stub,
            interval_seconds=0.1,
        )

        aoi = AreaOfInterest(name="N2YO AOI", bbox=BBOX, id=202)
        now = datetime.now(timezone.utc)

        monitor.schedule_or_start(
            aoi,
            pass_time=now,
            active_pass_info={"satellite": "Sentinel-1A", "contribution": "n2yo"},
            window_minutes=0.005,
        )

        time.sleep(0.2)
        monitor.stop_all()

        self.assertEqual(len(ingest_stub.calls), 0)
        # N2YO may corroborate history, but cannot independently trigger automation.
        job = repo_stub.find_by_aoi_and_pass(202, now)
        self.assertIsNone(job)

    def test_unknown_prediction_provenance_does_not_trigger_automation(self):
        ingest_stub = StubIngestAIS(records_to_insert=3)
        repo_stub = StubPostPassRepo()
        monitor = BackgroundPassMonitor(
            ingest_ais=ingest_stub,
            post_pass_repo=repo_stub,
            interval_seconds=0.1,
        )
        aoi = AreaOfInterest(name="Unknown Source AOI", bbox=BBOX, id=203)
        now = datetime.now(timezone.utc)

        entry = monitor.schedule_or_start(
            aoi,
            pass_time=now,
            active_pass_info={"satellite": "Sentinel-1A"},
            window_minutes=0.005,
        )

        self.assertIsNone(entry)
        self.assertEqual(ingest_stub.calls, [])
        self.assertIsNone(repo_stub.find_by_aoi_and_pass(203, now))

    def test_deduplication_prevents_duplicate_threads(self):
        ingest_stub = StubIngestAIS()
        monitor = BackgroundPassMonitor(
            ingest_ais=ingest_stub,
            interval_seconds=0.1,
        )

        aoi = AreaOfInterest(name="Dedup AOI", bbox=BBOX, id=303)
        now = datetime.now(timezone.utc)

        historical = {"satellite": "Sentinel-1A", "contribution": "historical"}
        entry1 = monitor.schedule_or_start(aoi, pass_time=now, active_pass_info=historical, window_minutes=0.01)
        entry2 = monitor.schedule_or_start(aoi, pass_time=now, active_pass_info=historical, window_minutes=0.01)

        self.assertIs(entry1, entry2)
        time.sleep(0.15)
        monitor.stop_all()

    def test_automatic_ais_failure_does_not_suppress_post_pass_job(self):
        class FailingAIS:
            def __init__(self):
                self.called = threading.Event()

            def execute(self, *args, **kwargs):
                self.called.set()
                raise RuntimeError("provider unavailable")

        repo_stub = StubPostPassRepo()
        failing_ais = FailingAIS()
        monitor = BackgroundPassMonitor(
            ingest_ais=failing_ais,
            post_pass_repo=repo_stub,
            interval_seconds=0.1,
        )
        aoi = AreaOfInterest(name="Resilient AOI", bbox=BBOX, id=606)
        now = datetime.now(timezone.utc)

        with self.assertLogs(
            "sentinel_analysis.infrastructure.scheduler.pass_monitor",
            level="WARNING",
        ) as logs:
            monitor.schedule_or_start(
                aoi,
                pass_time=now,
                active_pass_info={"satellite": "Sentinel-1C", "contribution": "historical"},
                window_minutes=0.005,
            )
            self.assertTrue(failing_ais.called.wait(timeout=1.0))
            monitor.stop_all()

        self.assertIsNotNone(repo_stub.find_by_aoi_and_pass(606, now))
        self.assertTrue(any("provider unavailable" in message for message in logs.output))
        self.assertTrue(
            self.wait_until(
                lambda: monitor.get_active_monitors()[0]["error"] == "provider unavailable",
            )
        )

    def test_natural_window_completion_transitions_job_to_polling(self):
        ingest_stub = StubIngestAIS()
        repo_stub = StubPostPassRepo()
        monitor = BackgroundPassMonitor(
            ingest_ais=ingest_stub,
            post_pass_repo=repo_stub,
            interval_seconds=0.5,
        )
        aoi = AreaOfInterest(name="Completion AOI", bbox=BBOX, id=707)
        now = datetime.now(timezone.utc)

        monitor.schedule_or_start(
            aoi,
            pass_time=now,
            active_pass_info={"satellite": "Sentinel-1A", "contribution": "historical"},
            window_minutes=0.005,
        )

        try:
            self.assertTrue(
                self.wait_until(
                    lambda: monitor.get_active_monitors()[0]["status"] == "COMPLETED",
                    timeout=2.0,
                )
            )
            job = repo_stub.find_by_aoi_and_pass(707, now)
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "POLLING_CATALOG")
        finally:
            monitor.stop_all()

    def test_stop_for_aoi_only_cancels_selected_monitor(self):
        monitor = BackgroundPassMonitor(ingest_ais=StubIngestAIS(), interval_seconds=1.0)
        now = datetime.now(timezone.utc)
        first = AreaOfInterest(name="First AOI", bbox=BBOX, id=808)
        second = AreaOfInterest(name="Second AOI", bbox=BBOX, id=809)

        monitor.schedule_or_start(
            first,
            pass_time=now,
            active_pass_info={"contribution": "historical"},
            window_minutes=5.0,
        )
        monitor.schedule_or_start(
            second,
            pass_time=now,
            active_pass_info={"contribution": "historical"},
            window_minutes=5.0,
        )
        monitor.stop_for_aoi(808)

        try:
            self.assertFalse(monitor.is_monitoring(808, now))
            self.assertTrue(monitor.is_monitoring(809, now))
            statuses = {entry["aoi_id"]: entry["status"] for entry in monitor.get_active_monitors()}
            self.assertEqual(statuses[808], "CANCELLED")
            self.assertEqual(statuses[809], "ACTIVE")
        finally:
            monitor.stop_all()

    def test_past_pass_is_not_scheduled(self):
        ingest_stub = StubIngestAIS()
        monitor = BackgroundPassMonitor(ingest_ais=ingest_stub, interval_seconds=0.1)

        aoi = AreaOfInterest(name="Past AOI", bbox=BBOX, id=404)
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)

        entry = monitor.schedule_or_start(aoi, pass_time=past_time, window_minutes=5.0)
        self.assertIsNone(entry)
        self.assertEqual(len(monitor.get_active_monitors()), 0)

    def test_stop_all_cancels_monitors(self):
        ingest_stub = StubIngestAIS()
        monitor = BackgroundPassMonitor(ingest_ais=ingest_stub, interval_seconds=1.0)

        aoi = AreaOfInterest(name="Cancel AOI", bbox=BBOX, id=505)
        now = datetime.now(timezone.utc)

        monitor.schedule_or_start(
            aoi,
            pass_time=now,
            active_pass_info={"satellite": "Sentinel-1A", "contribution": "historical"},
            window_minutes=5.0,
        )
        self.assertTrue(monitor.is_monitoring(505, now))

        monitor.stop_all()
        # After stopping, status transitions to CANCELLED
        monitors = monitor.get_active_monitors()
        self.assertTrue(all(m["status"] == "CANCELLED" for m in monitors))


if __name__ == "__main__":
    unittest.main()
