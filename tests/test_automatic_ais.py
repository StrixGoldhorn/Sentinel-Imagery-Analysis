"""Focused tests for automatic AIS triggering and scheduler delegation."""

from datetime import datetime, timedelta, timezone
import unittest

from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs
from sentinel_analysis.application.use_cases.trigger_automatic_ais import TriggerAutomaticAISScrape
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox, PostPassIngestionJob


BBOX = BoundingBox(103.0, 1.0, 104.0, 2.0)


class IngestStub:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or {"total_inserted": 4, "logs": []}
        self.error = error

    def execute(self, bbox, time_range, trigger_reason=None):
        self.calls.append((bbox, time_range, trigger_reason))
        if self.error is not None:
            raise self.error
        return self.result


class PostPassRepoStub:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []

    def find_by_aoi_and_pass(self, aoi_id, pass_time):
        return self.existing

    def add(self, job):
        job_id = len(self.added) + 1
        object.__setattr__(job, "id", job_id)
        self.added.append(job)
        return job_id


class PredictorStub:
    def __init__(self, predictions):
        self.predictions = predictions

    def predict(self, bbox, api_key):
        return list(self.predictions)


class AOIRepoStub:
    def __init__(self, aoi):
        self.aoi = aoi
        self.updates = []

    def list(self):
        return [self.aoi]

    def update_prediction(self, aoi_id, next_scan, last_checked):
        self.updates.append((aoi_id, next_scan, last_checked))


class PassMonitorStub:
    def __init__(self):
        self.calls = []

    def schedule_or_start(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "ACTIVE"}


class TestTriggerAutomaticAISScrape(unittest.TestCase):
    def setUp(self):
        self.aoi = AreaOfInterest("Test AOI", BBOX, id=7)
        self.pass_time = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        self.window_end = self.pass_time + timedelta(minutes=5)
        self.info = {
            "satellite": "Sentinel-1C",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 142,
            "source": "HISTORICAL_MISSION",
            "contribution": "historical",
            "basis_product_id": "S1C_TEST",
            "basis_acquisition_time": self.pass_time - timedelta(days=12),
            "basis_satellite": "Sentinel-1C",
            "basis_relative_orbit": 142,
        }

    def test_rejects_unpersisted_or_non_historical_passes(self):
        ingest = IngestStub()
        repo = PostPassRepoStub()
        use_case = TriggerAutomaticAISScrape(ingest, repo)

        for aoi, info in (
            (AreaOfInterest("New AOI", BBOX), self.info),
            (self.aoi, {"source": "N2YO", "contribution": "n2yo"}),
            (self.aoi, {"satellite": "Sentinel-1C"}),
        ):
            with self.subTest(aoi=aoi.id, info=info):
                with self.assertRaises(ValueError):
                    use_case.execute(aoi, self.pass_time, info, self.window_end)

        self.assertEqual(ingest.calls, [])
        self.assertEqual(repo.added, [])

    def test_creates_job_with_metadata_and_clamped_utc_time_window(self):
        ingest = IngestStub({"total_inserted": 4, "logs": [{"status": "SUCCESS"}]})
        repo = PostPassRepoStub()
        use_case = TriggerAutomaticAISScrape(ingest, repo)
        now = datetime(2026, 9, 1, 11, 58, tzinfo=timezone.utc)

        result = use_case.execute(
            self.aoi,
            self.pass_time.replace(tzinfo=None),
            self.info,
            self.window_end,
            now=now,
        )

        self.assertEqual(result["total_inserted"], 4)
        self.assertEqual(result["post_pass_job_id"], 1)
        self.assertEqual(len(repo.added), 1)
        job = repo.added[0]
        self.assertEqual(job.satellite, "Sentinel-1C")
        self.assertEqual(job.relative_orbit, 142)
        self.assertEqual(job.trigger_type, "AUTOMATIC_AIS")
        self.assertEqual(job.prediction_source, "HISTORICAL_MISSION")
        self.assertEqual(job.basis_product_id, "S1C_TEST")
        self.assertEqual(job.workflow_id, "aoi:7:pass:2026-09-01T12:00:00+00:00")

        _, time_range, reason = ingest.calls[0]
        self.assertEqual(time_range, (now - timedelta(minutes=1), now + timedelta(minutes=1)))
        self.assertIn("Test AOI", reason)

    def test_existing_job_is_reused_without_duplicate_registration(self):
        existing = PostPassIngestionJob(
            id=99,
            aoi_id=7,
            pass_time=self.pass_time,
            status="PENDING_PASS",
        )
        ingest = IngestStub()
        repo = PostPassRepoStub(existing=existing)
        use_case = TriggerAutomaticAISScrape(ingest, repo)

        result = use_case.execute(
            self.aoi,
            self.pass_time,
            self.info,
            self.window_end,
            now=self.pass_time,
        )

        self.assertEqual(result["post_pass_job_id"], 99)
        self.assertEqual(repo.added, [])
        self.assertEqual(len(ingest.calls), 1)

    def test_provider_failure_happens_after_job_registration(self):
        ingest = IngestStub(error=RuntimeError("provider unavailable"))
        repo = PostPassRepoStub()
        use_case = TriggerAutomaticAISScrape(ingest, repo)

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            use_case.execute(self.aoi, self.pass_time, self.info, self.window_end, now=self.pass_time)

        self.assertEqual(len(repo.added), 1)
        self.assertEqual(repo.added[0].status, "PENDING_PASS")

    def test_ended_pass_registers_job_for_immediate_catalog_polling(self):
        ingest = IngestStub()
        repo = PostPassRepoStub()
        use_case = TriggerAutomaticAISScrape(ingest, repo)
        now = self.window_end + timedelta(seconds=1)

        use_case.execute(self.aoi, self.pass_time, self.info, self.window_end, now=now)

        self.assertEqual(repo.added[0].status, "POLLING_CATALOG")
        self.assertEqual(repo.added[0].next_poll_at, now)


class TestAutomaticAISchedulingDelegation(unittest.TestCase):
    def test_active_historical_pass_is_delegated_to_monitor(self):
        now = datetime.now(timezone.utc)
        aoi = AreaOfInterest("Active AOI", BBOX, id=8, auto_capture_enabled=True)
        monitor = PassMonitorStub()
        ingest = IngestStub()
        scheduler = CheckAndScheduleAOIs(
            AOIRepoStub(aoi),
            PredictorStub([{
                "time": (now + timedelta(minutes=2)).isoformat(),
                "satellite": "Sentinel-1C",
                "source": "HISTORICAL_MISSION",
                "contribution": "historical",
            }]),
            ingest_ais=ingest,
            pass_monitor=monitor,
        )

        result = scheduler.execute("api-key")

        self.assertEqual(result[0]["status"], "FLYPAST_ACTIVE")
        self.assertEqual(len(monitor.calls), 1)
        self.assertEqual(monitor.calls[0]["aoi"], aoi)
        self.assertEqual(monitor.calls[0]["active_pass_info"]["satellite"], "Sentinel-1C")
        self.assertEqual(ingest.calls, [])

    def test_unknown_provenance_is_not_delegated_to_monitor(self):
        now = datetime.now(timezone.utc)
        aoi = AreaOfInterest("Unknown AOI", BBOX, id=9, auto_capture_enabled=True)
        monitor = PassMonitorStub()
        scheduler = CheckAndScheduleAOIs(
            AOIRepoStub(aoi),
            PredictorStub([{
                "time": (now + timedelta(minutes=1)).isoformat(),
                "satellite": "Sentinel-1A",
            }]),
            pass_monitor=monitor,
        )

        result = scheduler.execute("api-key")

        self.assertEqual(result[0]["status"], "NO_HISTORICAL_PREDICTION")
        self.assertEqual(monitor.calls, [])


if __name__ == "__main__":
    unittest.main()
