"""Unit and integration tests for Autonomous Post-Pass Imagery Ingestion."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.application.use_cases.ingest_post_pass_imagery import IngestPostPassImagery
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs
from sentinel_analysis.domain.entities import (
    Acquisition,
    AreaOfInterest,
    BoundingBox,
    DomainValidationError,
    PostPassIngestionJob,
    Scan,
)
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.infrastructure.persistence.sqlite_post_pass import SQLitePostPassIngestionRepository


RUNTIME_DIR = Path(__file__).resolve().parent / "runtime" / "test_post_pass"


class TestPostPassIngestionJobDomain(unittest.TestCase):
    def test_valid_job_creation(self):
        now = datetime.now(timezone.utc)
        job = PostPassIngestionJob(
            aoi_id=1,
            pass_time=now,
            satellite="Sentinel-1A",
            orbit_direction="ASCENDING",
            status="POLLING_CATALOG",
            attempts=0,
            created_at=now,
        )
        self.assertEqual(job.aoi_id, 1)
        self.assertEqual(job.satellite, "Sentinel-1A")
        self.assertEqual(job.status, "POLLING_CATALOG")

    def test_invalid_job_aoi_id(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(DomainValidationError):
            PostPassIngestionJob(aoi_id=-1, pass_time=now)
        with self.assertRaises(DomainValidationError):
            PostPassIngestionJob(aoi_id=0, pass_time=now)

    def test_invalid_job_status(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(DomainValidationError):
            PostPassIngestionJob(aoi_id=1, pass_time=now, status="INVALID_STATUS")


class TestSQLitePostPassRepository(unittest.TestCase):
    def setUp(self):
        self.test_dir = RUNTIME_DIR / f"repo_{self._testMethodName}"
        shutil.rmtree(self.test_dir, ignore_errors=True)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.test_dir / "test_sentinel.db"
        self.aoi_repo = SQLiteAreaOfInterestRepository(self.db_path)
        self.repo = SQLitePostPassIngestionRepository(self.db_path)

        # Create a test AOI
        self.aoi_id = self.aoi_repo.add(
            AreaOfInterest(
                name="Singapore Strait",
                bbox=BoundingBox(103.5, 1.1, 104.0, 1.4),
                auto_capture_enabled=True,
            )
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_add_and_get_job(self):
        pass_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=pass_time,
            satellite="Sentinel-1B",
            orbit_direction="DESCENDING",
            status="POLLING_CATALOG",
            attempts=1,
            last_polled_at=datetime.now(timezone.utc),
            next_poll_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        )
        job_id = self.repo.add(job)
        self.assertIsInstance(job_id, int)
        self.assertGreater(job_id, 0)

        fetched = self.repo.get(job_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.aoi_id, self.aoi_id)
        self.assertEqual(fetched.satellite, "Sentinel-1B")
        self.assertEqual(fetched.status, "POLLING_CATALOG")
        self.assertEqual(fetched.aoi_name, "Singapore Strait")

    def test_find_by_aoi_and_pass(self):
        pass_time = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=pass_time,
            satellite="Sentinel-1",
            status="POLLING_CATALOG",
        )
        self.repo.add(job)

        found = self.repo.find_by_aoi_and_pass(self.aoi_id, pass_time)
        self.assertIsNotNone(found)
        self.assertEqual(found.aoi_id, self.aoi_id)

    def test_get_jobs_due_for_poll(self):
        now = datetime.now(timezone.utc)
        # Job due now
        job1 = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=20),
            status="POLLING_CATALOG",
            next_poll_at=now - timedelta(minutes=1),
        )
        # Job due in the future
        job2 = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=10),
            status="POLLING_CATALOG",
            next_poll_at=now + timedelta(minutes=10),
        )
        self.repo.add(job1)
        self.repo.add(job2)

        due_jobs = self.repo.get_jobs_due_for_poll(now)
        self.assertEqual(len(due_jobs), 1)
        self.assertEqual(due_jobs[0].pass_time.isoformat(), job1.pass_time.isoformat())

    def test_update_and_delete_job(self):
        now = datetime.now(timezone.utc)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=20),
            status="POLLING_CATALOG",
        )
        job_id = self.repo.add(job)

        completed_job = PostPassIngestionJob(
            id=job_id,
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=20),
            status="COMPLETED",
            attempts=3,
            scan_folder="2026-09-01_singapore_strait",
            completed_at=now,
        )
        self.repo.update(completed_job)

        fetched = self.repo.get(job_id)
        self.assertEqual(fetched.status, "COMPLETED")
        self.assertEqual(fetched.scan_folder, "2026-09-01_singapore_strait")
        self.assertEqual(fetched.attempts, 3)

        self.repo.delete(job_id)
        self.assertIsNone(self.repo.get(job_id))


class TestIngestPostPassImageryUseCase(unittest.TestCase):
    def setUp(self):
        self.test_dir = RUNTIME_DIR / f"use_case_{self._testMethodName}"
        shutil.rmtree(self.test_dir, ignore_errors=True)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.test_dir / "test_sentinel.db"
        self.aoi_repo = SQLiteAreaOfInterestRepository(self.db_path)
        self.post_pass_repo = SQLitePostPassIngestionRepository(self.db_path)

        self.aoi_id = self.aoi_repo.add(
            AreaOfInterest(
                name="English Channel",
                bbox=BoundingBox(-1.0, 50.0, -0.5, 50.5),
                auto_capture_enabled=True,
            )
        )

        self.mock_imagery = MagicMock()
        self.mock_create_scan = MagicMock()
        self.mock_detect_ships = MagicMock()

        self.use_case = IngestPostPassImagery(
            post_pass_repository=self.post_pass_repo,
            aoi_repository=self.aoi_repo,
            imagery_provider=self.mock_imagery,
            create_scan=self.mock_create_scan,
            detect_ships=self.mock_detect_ships,
            max_wait_hours=24.0,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_progressive_backoff_when_no_image_ready(self):
        now = datetime.now(timezone.utc)
        pass_time = now - timedelta(minutes=15)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=pass_time,
            status="POLLING_CATALOG",
            attempts=0,
            next_poll_at=now - timedelta(seconds=1),
        )
        job_id = self.post_pass_repo.add(job)

        # Copernicus STAC returns empty list (product not published yet)
        self.mock_imagery.search_historical_acquisitions.return_value = []

        results = self.use_case.execute()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "POLLING_CATALOG")
        self.assertEqual(results[0]["attempts"], 1)

        updated_job = self.post_pass_repo.get(job_id)
        self.assertEqual(updated_job.attempts, 1)
        self.assertEqual(updated_job.status, "POLLING_CATALOG")
        self.assertIsNotNone(updated_job.next_poll_at)

    def test_successful_ingestion_and_scan_creation(self):
        now = datetime.now(timezone.utc)
        pass_time = now - timedelta(minutes=25)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=pass_time,
            status="POLLING_CATALOG",
            attempts=1,
            next_poll_at=now - timedelta(seconds=1),
        )
        job_id = self.post_pass_repo.add(job)

        # Mock Copernicus STAC returning matching acquisition
        mock_acq = Acquisition(
            acquired_at=pass_time,
            satellite="Sentinel-1A",
            product_type="GRD",
            product_id="S1A_IW_GRDH_1SDV_20260901T120000_20260901T120025_000000_000000_ABCD",
        )
        self.mock_imagery.search_historical_acquisitions.return_value = [mock_acq]

        # Mock CreateScan execution
        mock_scan = Scan(
            folder_name="2026-09-01_english_channel",
            bbox=BoundingBox(-1.0, 50.0, -0.5, 50.5),
            acquisition=mock_acq,
            image_path=str(self.test_dir / "test.png"),
            metadata={},
        )
        self.mock_create_scan.execute.return_value = mock_scan

        results = self.use_case.execute()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "COMPLETED")
        self.assertEqual(results[0]["scan_folder"], "2026-09-01_english_channel")

        updated_job = self.post_pass_repo.get(job_id)
        self.assertEqual(updated_job.status, "COMPLETED")
        self.assertEqual(updated_job.scan_folder, "2026-09-01_english_channel")
        self.mock_create_scan.execute.assert_called_once()

    def test_job_times_out_after_max_wait_hours(self):
        now = datetime.now(timezone.utc)
        pass_time = now - timedelta(hours=25)  # Over 24 hour threshold
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=pass_time,
            status="POLLING_CATALOG",
            attempts=10,
            next_poll_at=now - timedelta(minutes=1),
        )
        job_id = self.post_pass_repo.add(job)

        results = self.use_case.execute()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "TIMED_OUT")

        updated_job = self.post_pass_repo.get(job_id)
        self.assertEqual(updated_job.status, "TIMED_OUT")

    def test_success_when_imagery_is_within_plus_minus_one_hour(self):
        now = datetime.now(timezone.utc)
        expected_time = now - timedelta(minutes=45)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=expected_time - timedelta(minutes=10),
            expected_imagery_time=expected_time,
            status="POLLING_CATALOG",
            attempts=1,
            next_poll_at=now - timedelta(seconds=1),
        )
        job_id = self.post_pass_repo.add(job)

        # Mock Copernicus STAC returning acquisition acquired 30 min after expected time (within ±1hr)
        mock_acq = Acquisition(
            acquired_at=expected_time + timedelta(minutes=30),
            satellite="Sentinel-1A",
            product_type="GRD",
            product_id="S1A_IW_GRDH_1SDV_MATCH",
        )
        self.mock_imagery.search_historical_acquisitions.return_value = [mock_acq]
        mock_scan = Scan(
            folder_name="2026-09-01_matched_scan",
            bbox=BoundingBox(-1.0, 50.0, -0.5, 50.5),
            acquisition=mock_acq,
            image_path=str(self.test_dir / "test.png"),
            metadata={},
        )
        self.mock_create_scan.execute.return_value = mock_scan

        results = self.use_case.execute()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "COMPLETED")
        self.assertEqual(results[0]["scan_folder"], "2026-09-01_matched_scan")

        updated = self.post_pass_repo.get(job_id)
        self.assertEqual(updated.status, "COMPLETED")

    def test_fails_and_alerts_when_more_recent_imagery_detected(self):
        now = datetime.now(timezone.utc)
        expected_time = now - timedelta(hours=3)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=expected_time,
            expected_imagery_time=expected_time,
            status="POLLING_CATALOG",
            attempts=1,
            next_poll_at=now - timedelta(seconds=1),
        )
        job_id = self.post_pass_repo.add(job)

        # In window search: no products.
        # In recent search (> expected + 1hr): returns an acquisition acquired 2.5 hours after expected time.
        def mock_search(bbox, start_date=None, end_date=None, limit=5):
            if start_date and start_date >= expected_time + timedelta(hours=1):
                return [
                    Acquisition(
                        acquired_at=expected_time + timedelta(hours=2, minutes=30),
                        satellite="Sentinel-1A",
                        product_type="GRD",
                        product_id="S1A_IW_GRDH_NEWER_PRODUCT",
                    )
                ]
            return []

        self.mock_imagery.search_historical_acquisitions.side_effect = mock_search

        results = self.use_case.execute()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAILED")
        self.assertIn("Timing mismatch", results[0]["error"])

        updated = self.post_pass_repo.get(job_id)
        self.assertEqual(updated.status, "FAILED")
        self.assertIn("Timing mismatch", updated.error_message)
        self.assertIn("more recent imagery", updated.error_message)
        self.assertIsNone(updated.next_poll_at)  # Stopped polling


class TestCheckAndScheduleAOIsIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = RUNTIME_DIR / f"sched_{self._testMethodName}"
        shutil.rmtree(self.test_dir, ignore_errors=True)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.test_dir / "test_sentinel.db"
        self.aoi_repo = SQLiteAreaOfInterestRepository(self.db_path)
        self.post_pass_repo = SQLitePostPassIngestionRepository(self.db_path)

        self.aoi_id = self.aoi_repo.add(
            AreaOfInterest(
                name="Strait of Gibraltar",
                bbox=BoundingBox(-5.8, 35.8, -5.3, 36.1),
                auto_capture_enabled=True,
            )
        )

        self.mock_predictor = MagicMock()
        self.mock_ingest_post_pass = MagicMock()

        self.schedule_use_case = CheckAndScheduleAOIs(
            aoi_repository=self.aoi_repo,
            pass_predictor=self.mock_predictor,
            create_scan=None,
            ingest_ais=None,
            post_pass_repository=self.post_pass_repo,
            ingest_post_pass=self.mock_ingest_post_pass,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_registers_post_pass_job_on_completed_pass(self):
        now = datetime.now(timezone.utc)
        completed_pass_time = now - timedelta(minutes=20)
        future_pass_time = now + timedelta(hours=2)

        self.mock_predictor.predict.return_value = [
            {
                "time": completed_pass_time.isoformat(),
                "satellite": "Sentinel-1A",
                "orbit_direction": "ASCENDING",
            },
            {
                "time": future_pass_time.isoformat(),
                "satellite": "Sentinel-1B",
                "orbit_direction": "DESCENDING",
            },
        ]

        self.schedule_use_case.execute(api_key="test_key")

        # Verify job was registered for completed pass
        existing = self.post_pass_repo.find_by_aoi_and_pass(self.aoi_id, completed_pass_time)
        self.assertIsNotNone(existing)
        self.assertEqual(existing.status, "POLLING_CATALOG")
        self.assertEqual(existing.satellite, "Sentinel-1A")

        # Verify future pass is queued as PENDING_PASS
        future_job = self.post_pass_repo.find_by_aoi_and_pass(self.aoi_id, future_pass_time)
        self.assertIsNotNone(future_job)
        self.assertEqual(future_job.status, "PENDING_PASS")
        self.assertEqual(future_job.satellite, "Sentinel-1B")

        # Verify post pass ingestion execute was called
        self.mock_ingest_post_pass.execute.assert_called_once()

    def test_registers_job_during_active_flypast(self):
        now = datetime.now(timezone.utc)
        active_pass_time = now + timedelta(seconds=30)  # Happening right now (inside ±5m window)

        self.mock_predictor.predict.return_value = [
            {
                "time": active_pass_time.isoformat(),
                "satellite": "Sentinel-1C",
                "orbit_direction": "ASCENDING",
            }
        ]

        results = self.schedule_use_case.execute(api_key="test_key")

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["flypast_active"])
        self.assertEqual(results[0]["status"], "FLYPAST_ACTIVE")

        # Verify job is already queued in the post-pass repo during active flypast
        queued_job = self.post_pass_repo.find_by_aoi_and_pass(self.aoi_id, active_pass_time)
        self.assertIsNotNone(queued_job)
        self.assertEqual(queued_job.status, "PENDING_PASS")
        self.assertEqual(queued_job.satellite, "Sentinel-1C")
        self.assertGreaterEqual(queued_job.next_poll_at, active_pass_time + timedelta(minutes=5))

        # Active jobs list should include this pending job
        active_jobs = self.post_pass_repo.get_active_jobs()
        self.assertEqual(len(active_jobs), 1)
        self.assertEqual(active_jobs[0].status, "PENDING_PASS")

    def test_pending_pass_transition_when_pass_ends(self):
        now = datetime.now(timezone.utc)
        pass_time = now - timedelta(minutes=6)  # Pass just completed (> 5 mins ago)

        # Insert PENDING_PASS job
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=pass_time,
            status="PENDING_PASS",
            next_poll_at=pass_time + timedelta(minutes=5),
        )
        job_id = self.post_pass_repo.add(job)

        # get_jobs_due_for_poll should transition it to POLLING_CATALOG and return it
        due_jobs = self.post_pass_repo.get_jobs_due_for_poll(now)
        self.assertEqual(len(due_jobs), 1)
        self.assertEqual(due_jobs[0].id, job_id)
        self.assertEqual(due_jobs[0].status, "POLLING_CATALOG")

        # Check in DB
        fetched = self.post_pass_repo.get(job_id)
        self.assertEqual(fetched.status, "POLLING_CATALOG")


class TestPostPassWebAPI(unittest.TestCase):
    def setUp(self):
        from sentinel_analysis.bootstrap.container import ApplicationContainer
        from sentinel_analysis.bootstrap.config import Settings
        from sentinel_analysis.interfaces.web.application import create_app

        self.test_dir = RUNTIME_DIR / f"web_{self._testMethodName}"
        shutil.rmtree(self.test_dir, ignore_errors=True)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.test_dir / "test_web_sentinel.db"
        self.output_root = self.test_dir / "output"
        self.cache_root = self.test_dir / "cache"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self.settings = Settings(
            project_root=self.test_dir,
            database_path=self.db_path,
            output_root=self.output_root,
            copernicus_username="mock_user",
            copernicus_password="mock_pass",
            n2yo_api_key="mock_key",
            cache_root=self.cache_root,
        )
        self.container = ApplicationContainer(self.settings)
        self.app = create_app(container=self.container)
        self.client = self.app.test_client()

        # Add test AOI
        self.aoi_id = self.container.aoi_repository.add(
            AreaOfInterest(
                name="Panama Canal",
                bbox=BoundingBox(-79.7, 8.9, -79.4, 9.4),
                auto_capture_enabled=True,
            )
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_and_manipulate_post_pass_jobs(self):
        now = datetime.now(timezone.utc)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=45),
            satellite="Sentinel-1C",
            status="POLLING_CATALOG",
            attempts=2,
        )
        job_id = self.container.post_pass_repository.add(job)

        # GET jobs
        res = self.client.get("/api/schedule/post_pass_jobs")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["count"], 1)
        self.assertEqual(data["jobs"][0]["id"], job_id)
        self.assertEqual(data["jobs"][0]["satellite"], "Sentinel-1C")

        # Retry job
        res_retry = self.client.post(f"/api/schedule/post_pass_jobs/{job_id}/retry")
        self.assertEqual(res_retry.status_code, 200)
        retry_data = res_retry.get_json()
        self.assertEqual(retry_data["status"], "success")

        # Delete job
        res_del = self.client.delete(f"/api/schedule/post_pass_jobs/{job_id}")
        self.assertEqual(res_del.status_code, 200)
        del_data = res_del.get_json()
        self.assertEqual(del_data["status"], "success")

        # Verify deletion
        self.assertIsNone(self.container.post_pass_repository.get(job_id))

    def test_create_custom_post_pass_job(self):
        now = datetime.now(timezone.utc)
        pass_time = now + timedelta(hours=2)
        exp_time = pass_time + timedelta(minutes=15)

        # 1. Validation failure: missing aoi_id
        res_bad = self.client.post("/api/schedule/post_pass_jobs", json={
            "pass_time": pass_time.isoformat(),
        })
        self.assertEqual(res_bad.status_code, 400)

        # 2. Successful creation
        res = self.client.post("/api/schedule/post_pass_jobs", json={
            "aoi_id": self.aoi_id,
            "pass_time": pass_time.isoformat(),
            "expected_imagery_time": exp_time.isoformat(),
            "satellite": "Sentinel-1B",
            "orbit_direction": "ASCENDING",
            "poll_immediately": False,
        })
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        job_id = data["job_id"]

        # Verify job in repo
        job = self.container.post_pass_repository.get(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job.aoi_id, self.aoi_id)
        self.assertEqual(job.satellite, "Sentinel-1B")
        self.assertEqual(job.orbit_direction, "ASCENDING")
        self.assertEqual(job.status, "PENDING_PASS")
        self.assertEqual(job.expected_imagery_time.isoformat(), exp_time.isoformat())

        # Verify job appears in GET list
        res_list = self.client.get("/api/schedule/post_pass_jobs")
        self.assertEqual(res_list.status_code, 200)
        jobs_data = res_list.get_json()["jobs"]
        matched_job = next((j for j in jobs_data if j["id"] == job_id), None)
        self.assertIsNotNone(matched_job)
        self.assertEqual(matched_job["expected_imagery_time"], exp_time.isoformat())


if __name__ == "__main__":
    unittest.main()

