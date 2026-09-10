"""Unit and integration tests for Autonomous Post-Pass Imagery Ingestion."""

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
import concurrent.futures
from unittest.mock import MagicMock

from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.application.use_cases.ingest_post_pass_imagery import (
    IngestPostPassImagery,
    _extract_acq_datetime,
)
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

    def test_due_job_is_claimed_by_only_one_worker(self):
        now = datetime.now(timezone.utc)
        job_id = self.repo.add(PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=20),
            status="POLLING_CATALOG",
            next_poll_at=now - timedelta(seconds=1),
        ))
        second_repo = SQLitePostPassIngestionRepository(self.db_path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            claimed = list(executor.map(lambda repo: repo.claim_jobs_due_for_poll(now), [self.repo, second_repo]))

        self.assertEqual(sum(len(group) for group in claimed), 1)
        self.assertEqual(self.repo.get(job_id).status, "QUERYING_CATALOG")

        events = list(reversed(self.repo.list_events(job_id)))
        self.assertEqual(events[0]["reason"], "JOB_CREATED")
        self.assertEqual(events[-1]["reason"], "JOB_CLAIMED")
        self.assertEqual(events[-1]["old_status"], "POLLING_CATALOG")
        self.assertEqual(events[-1]["new_status"], "QUERYING_CATALOG")

    def test_completed_poll_attempts_include_localizable_timestamps(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        job_id = self.repo.add(PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=30),
            status="POLLING_CATALOG",
            attempts=0,
            next_poll_at=now - timedelta(seconds=1),
        ))

        claimed = self.repo.claim_job(job_id, now)
        self.assertIsNotNone(claimed)
        self.repo.update(replace(
            claimed,
            status="POLLING_CATALOG",
            attempts=1,
            last_polled_at=now,
            next_poll_at=now + timedelta(hours=1),
        ))

        attempts = self.repo.list_poll_attempts(job_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["attempt"], 1)
        self.assertEqual(attempts[0]["reason"], "POLL_SCHEDULED")
        self.assertEqual(attempts[0]["started_at"], now.isoformat())
        self.assertIsNotNone(attempts[0]["completed_at"])

    def test_causal_prediction_basis_is_persisted(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        job_id = self.repo.add(PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now + timedelta(days=2),
            satellite="Sentinel-1A",
            orbit_direction="ASCENDING",
            relative_orbit=142,
            trigger_type="AUTOMATIC_AIS",
            prediction_source="HISTORICAL_MISSION",
            workflow_id="workflow-1",
            basis_product_id="S1A_BASIS",
            basis_acquisition_time=now - timedelta(days=10),
            basis_satellite="Sentinel-1A",
            basis_relative_orbit=142,
        ))
        fetched = self.repo.get(job_id)
        self.assertEqual(fetched.relative_orbit, 142)
        self.assertEqual(fetched.trigger_type, "AUTOMATIC_AIS")
        self.assertEqual(fetched.basis_product_id, "S1A_BASIS")
        self.assertEqual(fetched.basis_acquisition_time, now - timedelta(days=10))

    def test_scheduler_lease_allows_only_one_owner_until_expiry(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(self.repo.try_acquire_scheduler_lease("scheduler", "owner-1", now, 30))
        self.assertFalse(self.repo.try_acquire_scheduler_lease("scheduler", "owner-2", now, 30))
        self.assertTrue(self.repo.try_acquire_scheduler_lease(
            "scheduler", "owner-2", now + timedelta(seconds=31), 30
        ))


def test_production_catalog_acquisition_time_shape_is_parsed() -> None:
    expected = datetime(2026, 9, 1, 12, 34, 56, tzinfo=timezone.utc)
    assert _extract_acq_datetime({
        "product_id": "S1A_PRODUCT",
        "acquisition_time": expected.isoformat(),
        "platform": "Sentinel-1A",
    }) == expected


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

    def test_batch_limit_is_forwarded_to_atomic_claim(self):
        jobs = MagicMock()
        jobs.claim_jobs_due_for_poll.return_value = []
        use_case = IngestPostPassImagery(
            post_pass_repository=jobs,
            aoi_repository=MagicMock(),
            imagery_provider=MagicMock(),
            create_scan=MagicMock(),
        )
        self.assertEqual(use_case.execute(batch_limit=25), [])
        jobs.claim_jobs_due_for_poll.assert_called_once()
        self.assertEqual(jobs.claim_jobs_due_for_poll.call_args.kwargs["limit"], 25)

    def test_fixed_hourly_interval_when_no_image_ready(self):
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
        self.assertGreaterEqual(
            (updated_job.next_poll_at - now).total_seconds(),
            59 * 60,
        )

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
        self.assertEqual(
            self.mock_create_scan.execute.call_args.kwargs["acquisition"].product_id,
            mock_acq.product_id,
        )

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

    def test_keeps_polling_when_newer_imagery_does_not_match_target_pass(self):
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
        self.assertEqual(results[0]["status"], "POLLING_CATALOG")

        updated = self.post_pass_repo.get(job_id)
        self.assertEqual(updated.status, "POLLING_CATALOG")
        self.assertIsNotNone(updated.next_poll_at)

    def test_latest_acquisition_outside_window_does_not_fail_target_job(self):
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

        self.mock_imagery.search_historical_acquisitions.return_value = []
        newer_acq = Acquisition(
            acquired_at=expected_time + timedelta(hours=2, minutes=30),
            satellite="Sentinel-1A",
            product_type="GRD",
            product_id="S1A_IW_GRDH_LATEST_EXCEEDED",
        )
        self.mock_imagery.find_latest_acquisition.return_value = newer_acq

        results = self.use_case.execute()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "POLLING_CATALOG")

        updated = self.post_pass_repo.get(job_id)
        self.assertEqual(updated.status, "POLLING_CATALOG")
        self.assertIsNotNone(updated.next_poll_at)

    def test_completes_when_imagery_exists_within_plus_minus_one_hour(self):
        now = datetime.now(timezone.utc)
        expected_time = now - timedelta(minutes=40)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=expected_time,
            expected_imagery_time=expected_time,
            status="POLLING_CATALOG",
            attempts=1,
            next_poll_at=now - timedelta(seconds=1),
        )
        job_id = self.post_pass_repo.add(job)

        matching_acq = Acquisition(
            acquired_at=expected_time + timedelta(minutes=15),
            satellite="Sentinel-1A",
            product_type="GRD",
            product_id="S1A_IW_GRDH_MATCH_1HR",
        )
        self.mock_imagery.search_historical_acquisitions.return_value = [matching_acq]
        mock_scan = Scan(
            folder_name="2026-09-01_matched_1hr",
            bbox=BoundingBox(-1.0, 50.0, -0.5, 50.5),
            acquisition=matching_acq,
            image_path=str(self.test_dir / "test.png"),
            metadata={},
        )
        self.mock_create_scan.execute.return_value = mock_scan

        results = self.use_case.execute()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "COMPLETED")
        updated = self.post_pass_repo.get(job_id)
        self.assertEqual(updated.status, "COMPLETED")


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
        self.mock_ingest_ais = MagicMock()
        self.mock_ingest_ais.execute.return_value = {"total_inserted": 0, "logs": []}

        self.schedule_use_case = CheckAndScheduleAOIs(
            aoi_repository=self.aoi_repo,
            pass_predictor=self.mock_predictor,
            create_scan=None,
            ingest_ais=self.mock_ingest_ais,
            post_pass_repository=self.post_pass_repo,
            ingest_post_pass=self.mock_ingest_post_pass,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_does_not_register_job_when_no_active_flypast_autoscan(self):
        now = datetime.now(timezone.utc)
        completed_pass_time = now - timedelta(minutes=20)
        future_pass_time = now + timedelta(hours=2)

        self.mock_predictor.predict.return_value = [
            {
                "time": completed_pass_time.isoformat(),
                "satellite": "Sentinel-1A",
                "orbit_direction": "ASCENDING",
                "source": "HISTORICAL_MISSION",
                "contribution": "historical",
            },
            {
                "time": future_pass_time.isoformat(),
                "satellite": "Sentinel-1B",
                "orbit_direction": "DESCENDING",
                "source": "HISTORICAL_MISSION",
                "contribution": "historical",
            },
        ]

        self.schedule_use_case.execute(api_key="test_key")

        # Verify no job was registered for completed pass when app was offline (no autoscan)
        existing = self.post_pass_repo.find_by_aoi_and_pass(self.aoi_id, completed_pass_time)
        self.assertIsNone(existing)

        # Verify future pass is not pre-emptively registered before autoscan
        future_job = self.post_pass_repo.find_by_aoi_and_pass(self.aoi_id, future_pass_time)
        self.assertIsNone(future_job)
        self.mock_ingest_ais.execute.assert_not_called()

    def test_registers_job_during_active_flypast(self):
        now = datetime.now(timezone.utc)
        active_pass_time = now + timedelta(seconds=30)  # Happening right now (inside ±5m window)

        self.mock_predictor.predict.return_value = [
            {
                "time": active_pass_time.isoformat(),
                "satellite": "Sentinel-1C",
                "orbit_direction": "ASCENDING",
                "source": "HISTORICAL_MISSION",
                "contribution": "historical",
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

    def test_automated_post_pass_ingestion_ignores_n2yo_only_predictions(self):
        now = datetime.now(timezone.utc)
        n2yo_active_pass = now + timedelta(seconds=15)

        self.mock_predictor.predict.return_value = [
            {
                "time": n2yo_active_pass.isoformat(),
                "satellite": "Sentinel-1A",
                "source": "N2YO",
                "contribution": "n2yo",
            },
        ]

        self.schedule_use_case.execute(api_key="test_key")

        # N2YO-only pass should NOT have been registered even during active flypast
        n2yo_job = self.post_pass_repo.find_by_aoi_and_pass(self.aoi_id, n2yo_active_pass)
        self.assertIsNone(n2yo_job)
        self.mock_ingest_ais.execute.assert_not_called()

        # Combined/historical pass during active flypast SHOULD have been registered
        valid_active_pass = now + timedelta(seconds=20)
        self.mock_predictor.predict.return_value = [
            {
                "time": valid_active_pass.isoformat(),
                "satellite": "Sentinel-1C",
                "source": "COMBINED",
                "contribution": "both",
            },
        ]

        self.schedule_use_case.execute(api_key="test_key")
        valid_job = self.post_pass_repo.find_by_aoi_and_pass(self.aoi_id, valid_active_pass)
        self.assertIsNotNone(valid_job)
        self.assertEqual(valid_job.status, "PENDING_PASS")
        self.mock_ingest_ais.execute.assert_called_once()


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
        self.app = create_app(container=self.container, start_background_workers=False)
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
        self.container.shutdown(timeout=0.5)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_and_manipulate_post_pass_jobs(self):
        now = datetime.now(timezone.utc)
        job = PostPassIngestionJob(
            aoi_id=self.aoi_id,
            pass_time=now - timedelta(minutes=45),
            satellite="Sentinel-1C",
            status="FAILED",
            attempts=2,
        )
        job_id = self.container.post_pass_repository.add(job)
        self.container.ingest_post_pass = MagicMock()
        self.container.ingest_post_pass.execute.return_value = []

        # GET jobs
        res = self.client.get("/api/schedule/post_pass_jobs")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["count"], 1)
        self.assertEqual(data["jobs"][0]["id"], job_id)
        self.assertEqual(data["jobs"][0]["satellite"], "Sentinel-1C")
        self.assertEqual(data["jobs"][0]["status_group"], "TERMINAL")
        self.assertTrue(data["jobs"][0]["terminal"])

        # Retry job
        res_retry = self.client.post(f"/api/schedule/post_pass_jobs/{job_id}/retry")
        self.assertEqual(res_retry.status_code, 202)
        retry_data = res_retry.get_json()
        self.assertEqual(retry_data["status"], "accepted")
        self.assertIn("task_id", retry_data)

        # Active jobs are protected from deletion.
        res_del = self.client.delete(f"/api/schedule/post_pass_jobs/{job_id}")
        self.assertEqual(res_del.status_code, 409)

        # Once terminal, the job can be deleted.
        current = self.container.post_pass_repository.get(job_id)
        self.container.post_pass_repository.update(replace(current, status="FAILED"))
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
