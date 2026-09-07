"""Unit and integration tests for configurable satellite selection (Sentinel-1A, 1B, 1C)."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel_analysis.application.use_cases.get_schedule import GetUpcomingScrapes
from sentinel_analysis.application.use_cases.manage_settings import UpdateSettings
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.domain.satellite import (
    DEFAULT_ENABLED_SATELLITES,
    SATELLITE_CATALOG,
    SATELLITE_NAME_TO_NORAD,
)
from sentinel_analysis.infrastructure.persistence.sqlite_settings import (
    DEFAULT_SETTINGS_DEFINITIONS,
    SQLiteSettingsRepository,
)
from sentinel_analysis.infrastructure.satellite.hybrid_predictor import HybridPassPredictor
from sentinel_analysis.infrastructure.satellite.s1_analyzer import Sentinel1MissionAnalyzer


class DummyImageryProvider:
    def search_acquisitions(self, bbox, start_date, end_date):
        return []


class StubN2YOPredictor:
    def __init__(self, passes_by_satellite=None):
        self.passes_by_satellite = passes_by_satellite or {}
        self.called_satellites = []

    def predict(self, bbox, api_key, days=10, min_elevation=15.0, satellite_id=None, satellite_name=None):
        sat_name = satellite_name or "Sentinel-1A"
        self.called_satellites.append(sat_name)
        return self.passes_by_satellite.get(sat_name, [])


class StubMissionAnalyzer:
    def __init__(self, passes=None):
        self._passes = passes or []
        self.last_enabled_satellites = None

    def predict_from_history(self, bbox, days_ahead=14, limit=100, enabled_satellites=None):
        self.last_enabled_satellites = enabled_satellites
        if enabled_satellites is None:
            return self._passes
        return [p for p in self._passes if (p.get("satellite") or "Sentinel-1A") in enabled_satellites]


class StubAOIRepository:
    def __init__(self, aois):
        self._aois = aois
        self.updated_predictions = {}

    def list(self):
        return self._aois

    def get(self, aoi_id):
        for a in self._aois:
            if a.id == aoi_id:
                return a
        return None

    def update_prediction(self, aoi_id, next_scan, last_checked):
        self.updated_predictions[aoi_id] = (next_scan, last_checked)


class StubPostPassRepo:
    def __init__(self):
        self.registered_jobs = []

    def find_by_aoi_and_pass(self, aoi_id, pass_time):
        for job in self.registered_jobs:
            if getattr(job, "aoi_id", None) == aoi_id and getattr(job, "pass_time", None) == pass_time:
                return job
        return None

    def add(self, job):
        self.registered_jobs.append(job)
        return len(self.registered_jobs)


class TestSatelliteSelectionCatalogAndSettings(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_satellite_settings.db"
        self.repo = SQLiteSettingsRepository(self.db_path)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_catalog_definitions(self):
        self.assertIn("Sentinel-1A", SATELLITE_CATALOG)
        self.assertIn("Sentinel-1B", SATELLITE_CATALOG)
        self.assertIn("Sentinel-1C", SATELLITE_CATALOG)
        self.assertIn("Sentinel-1D", SATELLITE_CATALOG)
        self.assertEqual(SATELLITE_NAME_TO_NORAD["Sentinel-1A"], 39634)
        self.assertEqual(SATELLITE_NAME_TO_NORAD["Sentinel-1C"], 62232)
        self.assertEqual(SATELLITE_NAME_TO_NORAD["Sentinel-1D"], 66315)
        self.assertEqual(DEFAULT_ENABLED_SATELLITES, ["Sentinel-1A", "Sentinel-1C"])

    def test_sqlite_settings_default_seeding(self):
        enabled = self.repo.get("enabled_satellites")
        self.assertIsInstance(enabled, list)
        self.assertIn("Sentinel-1A", enabled)
        self.assertIn("Sentinel-1C", enabled)
        self.assertNotIn("Sentinel-1B", enabled)
        self.assertNotIn("Sentinel-1D", enabled)

    def test_update_settings_validation(self):
        updater = UpdateSettings(self.repo)

        # Valid update including Sentinel-1D
        updater.execute({
            "scheduler": {
                "enabled_satellites": ["Sentinel-1A", "Sentinel-1C", "Sentinel-1D"]
            }
        })
        self.assertEqual(self.repo.get("enabled_satellites"), ["Sentinel-1A", "Sentinel-1C", "Sentinel-1D"])

        # Invalid satellite name should raise ValueError
        with self.assertRaises(ValueError):
            updater.execute({
                "scheduler": {
                    "enabled_satellites": ["Sentinel-1A", "Sentinel-9X"]
                }
            })


class TestMissionAnalyzerSatelliteFiltering(unittest.TestCase):
    def test_synthesize_nominal_passes_filtering(self):
        analyzer = Sentinel1MissionAnalyzer(DummyImageryProvider())
        bbox = BoundingBox(103.8, 1.2, 103.9, 1.3)
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

        # Only Sentinel-1A
        passes_s1a = analyzer.predict_from_history(bbox, days_ahead=7, enabled_satellites=["Sentinel-1A"])
        self.assertTrue(len(passes_s1a) > 0)
        for p in passes_s1a:
            self.assertEqual(p["satellite"], "Sentinel-1A")

        # Only Sentinel-1C
        passes_s1c = analyzer.predict_from_history(bbox, days_ahead=7, enabled_satellites=["Sentinel-1C"])
        self.assertTrue(len(passes_s1c) > 0)
        for p in passes_s1c:
            self.assertEqual(p["satellite"], "Sentinel-1C")

        # Only Sentinel-1D
        passes_s1d = analyzer.predict_from_history(bbox, days_ahead=7, enabled_satellites=["Sentinel-1D"])
        self.assertTrue(len(passes_s1d) > 0)
        for p in passes_s1d:
            self.assertEqual(p["satellite"], "Sentinel-1D")

        # Empty enabled satellites
        passes_none = analyzer.predict_from_history(bbox, days_ahead=7, enabled_satellites=[])
        self.assertEqual(len(passes_none), 0)


class TestHybridPredictorSatelliteSelection(unittest.TestCase):
    def test_hybrid_queries_only_enabled_satellites(self):
        now = datetime.now(timezone.utc)
        n2yo_mock = StubN2YOPredictor({
            "Sentinel-1A": [
                {
                    "time": (now + timedelta(hours=2)).isoformat(),
                    "source": "N2YO",
                    "satellite": "Sentinel-1A",
                    "max_elevation": 45.0,
                }
            ],
            "Sentinel-1C": [
                {
                    "time": (now + timedelta(hours=8)).isoformat(),
                    "source": "N2YO",
                    "satellite": "Sentinel-1C",
                    "max_elevation": 50.0,
                }
            ],
        })
        mission_mock = StubMissionAnalyzer()
        predictor = HybridPassPredictor(n2yo_mock, mission_mock)

        # Predict with only Sentinel-1C enabled
        results = predictor.predict(
            bbox=BoundingBox(103.8, 1.2, 103.9, 1.3),
            api_key="test_key",
            enabled_satellites=["Sentinel-1C"],
        )

        self.assertEqual(n2yo_mock.called_satellites, ["Sentinel-1C"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["satellite"], "Sentinel-1C")


class TestScheduleUseCasesSatelliteSelection(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_usecase_satellites.db"
        self.settings_repo = SQLiteSettingsRepository(self.db_path)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_get_upcoming_scrapes_satellite_filtering(self):
        now = datetime.now(timezone.utc)
        aoi = AreaOfInterest(
            name="Singapore Strait",
            bbox=BoundingBox(103.8, 1.2, 103.9, 1.3),
            id=1,
            auto_capture_enabled=True,
        )
        aoi_repo = StubAOIRepository([aoi])

        class MockPredictor:
            def predict(self, bbox, api_key, enabled_satellites=None):
                return [
                    {
                        "time": (now + timedelta(hours=3)).isoformat(),
                        "satellite": "Sentinel-1A",
                        "source": "COMBINED",
                        "contribution": "both",
                    },
                    {
                        "time": (now + timedelta(hours=9)).isoformat(),
                        "satellite": "Sentinel-1C",
                        "source": "HISTORICAL_MISSION",
                        "contribution": "historical",
                    },
                    {
                        "time": (now + timedelta(hours=15)).isoformat(),
                        "satellite": "Sentinel-1B",
                        "source": "N2YO",
                        "contribution": "n2yo",
                    },
                ]

        use_case = GetUpcomingScrapes(aoi_repo, MockPredictor(), settings_repo=self.settings_repo)

        # Default: Sentinel-1A and Sentinel-1C enabled, Sentinel-1B ignored
        res = use_case.execute(api_key="key", days_ahead=7)
        sat_names = [e["satellite"] for e in res["events"]]
        self.assertIn("Sentinel-1A", sat_names)
        self.assertIn("Sentinel-1C", sat_names)
        self.assertNotIn("Sentinel-1B", sat_names)
        self.assertEqual(res["enabled_satellites"], ["Sentinel-1A", "Sentinel-1C"])

        # Filter specifically by Sentinel-1A
        res_s1a = use_case.execute(api_key="key", days_ahead=7, satellite="Sentinel-1A")
        sat_names_s1a = [e["satellite"] for e in res_s1a["events"]]
        self.assertEqual(sat_names_s1a, ["Sentinel-1A"])

    def test_check_and_schedule_aois_ignores_disabled_satellites(self):
        now = datetime.now(timezone.utc)
        aoi = AreaOfInterest(
            name="Test Port",
            bbox=BoundingBox(103.8, 1.2, 103.9, 1.3),
            id=10,
            auto_capture_enabled=True,
        )
        aoi_repo = StubAOIRepository([aoi])
        post_pass_repo = StubPostPassRepo()

        class MockPredictor:
            def predict(self, bbox, api_key, enabled_satellites=None):
                return [
                    {
                        "time": (now + timedelta(hours=2)).isoformat(),
                        "satellite": "Sentinel-1B",  # Disabled by default
                    },
                    {
                        "time": (now + timedelta(hours=4)).isoformat(),
                        "satellite": "Sentinel-1C",  # Enabled
                    },
                ]

        # Ingest stub
        class MockIngestAIS:
            def __init__(self):
                self.calls = 0

            def execute(self, aoi_id, bbox):
                self.calls += 1

        class MockCreateScan:
            def execute(self, **kwargs):
                pass

        class MockIngestPostPass:
            def execute(self, job_id):
                pass

        ingest_ais = MockIngestAIS()
        scheduler = CheckAndScheduleAOIs(
            aoi_repo,
            MockPredictor(),
            MockCreateScan(),
            ingest_ais,
            post_pass_repo,
            MockIngestPostPass(),
            settings_repo=self.settings_repo,
        )

        scheduler.execute(api_key="test_key")

        # Next scan time on AOI should be Sentinel-1C pass (+4 hours), NOT Sentinel-1B (+2 hours)
        next_scan, _ = aoi_repo.updated_predictions[10]
        expected_time = now + timedelta(hours=4)
        if hasattr(next_scan, "isoformat"):
            self.assertEqual(next_scan, expected_time)
        else:
            self.assertEqual(next_scan, expected_time.isoformat())

        # Post-pass registered job should only be for Sentinel-1C
        self.assertEqual(len(post_pass_repo.registered_jobs), 1)
        job = post_pass_repo.registered_jobs[0]
        sat = getattr(job, "satellite", None) or (job.get("sat_name") if isinstance(job, dict) else None)
        self.assertEqual(sat, "Sentinel-1C")


if __name__ == "__main__":
    unittest.main()
