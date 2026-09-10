"""Unit and integration tests for Settings management."""

import json
import tempfile
import unittest
from pathlib import Path

from sentinel_analysis.application.use_cases.manage_settings import (
    GetSettings,
    ResetSettings,
    UpdateSettings,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.infrastructure.persistence.sqlite_settings import (
    DEFAULT_SETTINGS_DEFINITIONS,
    SQLiteSettingsRepository,
)
from sentinel_analysis.interfaces.web.application import create_app


class TestSQLiteSettingsRepository(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_settings.db"
        self.repo = SQLiteSettingsRepository(self.db_path)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_default_seeding(self):
        # CV defaults
        coastal_buffer = self.repo.get("coastal_buffer_pixels")
        self.assertEqual(coastal_buffer, 81)

        threshold = self.repo.get("threshold")
        self.assertEqual(threshold, 40)

        # Section retrieval
        cv_section = self.repo.get_section("cv")
        self.assertIn("coastal_buffer_pixels", cv_section)
        self.assertIn("threshold", cv_section)
        self.assertEqual(cv_section["coastal_buffer_pixels"], 81)

    def test_update_and_get(self):
        self.repo.set("cv", "coastal_buffer_pixels", 120)
        self.assertEqual(self.repo.get("coastal_buffer_pixels"), 120)

        # Bulk update
        self.repo.update_bulk({
            "cv": {"coastal_buffer_pixels": 45, "threshold": 55},
            "imagery": {"resolution_meters": 15.0},
        })
        self.assertEqual(self.repo.get("coastal_buffer_pixels"), 45)
        self.assertEqual(self.repo.get("threshold"), 55)
        self.assertEqual(self.repo.get("resolution_meters"), 15.0)

    def test_definitions(self):
        defs = self.repo.get_all_definitions()
        self.assertIn("cv", defs)
        self.assertIn("coastal_buffer_pixels", defs["cv"])
        self.assertEqual(defs["cv"]["coastal_buffer_pixels"]["value"], 81)
        self.assertEqual(defs["cv"]["coastal_buffer_pixels"]["type"], "integer")
        self.assertNotIn("n2yo_api_key", defs.get("scheduler", {}))
        self.assertNotIn("system", defs)
        self.assertTrue(defs["map_ui"]["ais_overlay_default_enabled"]["value"])
        self.assertFalse(defs["map_ui"]["nautical_chart_default_enabled"]["value"])
        self.assertIn("notifications", defs)
        self.assertEqual(defs["notifications"]["duration_seconds"]["value"], 3.0)
        self.assertEqual(defs["scheduler"]["post_pass_worker_count"]["value"], 8)
        self.assertEqual(defs["scheduler"]["post_pass_max_wait_hours"]["value"], 24.0)

    def test_environment_owned_values_are_not_stored_or_returned(self):
        self.repo.set("scheduler", "n2yo_api_key", "should_not_persist")
        self.repo.update_bulk({
            "system": {"port": 9999, "database_path": "other.db"},
            "scheduler": {"n2yo_api_key": "should_not_persist"},
        })

        self.assertIsNone(self.repo.get("n2yo_api_key"))
        self.assertIsNone(self.repo.get("port"))
        self.assertNotIn("system", self.repo.get_all())
        self.assertNotIn("n2yo_api_key", self.repo.get_section("scheduler"))

    def test_copernicus_credentials_excluded_from_repository(self):
        # Credentials must not be present in definitions
        defs = self.repo.get_all_definitions()
        self.assertNotIn("copernicus_username", defs.get("imagery", {}))
        self.assertNotIn("copernicus_password", defs.get("imagery", {}))

        # Credentials must not be present in imagery section
        imagery_sec = self.repo.get_section("imagery")
        self.assertNotIn("copernicus_username", imagery_sec)
        self.assertNotIn("copernicus_password", imagery_sec)

        # Credentials query returns None
        self.assertIsNone(self.repo.get("copernicus_username"))
        self.assertIsNone(self.repo.get("copernicus_password"))

        # Bulk update ignores credentials
        self.repo.update_bulk({
            "imagery": {
                "copernicus_username": "should_be_ignored",
                "copernicus_password": "should_be_ignored",
                "search_window_days": 14,
            }
        })
        self.assertIsNone(self.repo.get("copernicus_username"))
        self.assertIsNone(self.repo.get("copernicus_password"))
        self.assertEqual(self.repo.get("search_window_days"), 14)

    def test_reset_section(self):
        self.repo.set("cv", "coastal_buffer_pixels", 200)
        self.assertEqual(self.repo.get("coastal_buffer_pixels"), 200)

        self.repo.reset_section("cv")
        self.assertEqual(self.repo.get("coastal_buffer_pixels"), 81)


class TestSettingsUseCases(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_usecases.db"
        self.repo = SQLiteSettingsRepository(self.db_path)
        self.get_settings = GetSettings(self.repo)
        self.update_settings = UpdateSettings(self.repo)
        self.reset_settings = ResetSettings(self.repo)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_get_settings(self):
        all_settings = self.get_settings.execute()
        self.assertIn("cv", all_settings)
        self.assertIn("coastal_buffer_pixels", all_settings["cv"])

    def test_update_valid_settings(self):
        self.update_settings.execute({
            "cv": {"coastal_buffer_pixels": 95, "threshold": 60}
        })
        cv = self.get_settings.execute("cv")
        self.assertEqual(cv["coastal_buffer_pixels"], 95)
        self.assertEqual(cv["threshold"], 60)

    def test_update_invalid_coastal_buffer(self):
        with self.assertRaises(ValueError):
            self.update_settings.execute({
                "cv": {"coastal_buffer_pixels": -10}
            })

    def test_update_invalid_threshold(self):
        with self.assertRaises(ValueError):
            self.update_settings.execute({
                "cv": {"threshold": 300}
            })

    def test_update_imagery_ignores_credentials(self):
        self.update_settings.execute({
            "imagery": {
                "copernicus_username": "ignored_user",
                "copernicus_password": "ignored_password",
                "search_window_days": 10,
            }
        })
        imagery = self.get_settings.execute("imagery")
        self.assertNotIn("copernicus_username", imagery)
        self.assertNotIn("copernicus_password", imagery)
        self.assertEqual(imagery["search_window_days"], 10)

    def test_validation_ranges(self):
        # Invalid search window
        with self.assertRaises(ValueError):
            self.update_settings.execute({"imagery": {"search_window_days": 0}})

        # Invalid zoom
        with self.assertRaises(ValueError):
            self.update_settings.execute({"map_ui": {"default_zoom": 25}})

        # Invalid opacity
        with self.assertRaises(ValueError):
            self.update_settings.execute({"map_ui": {"sar_opacity": 1.5}})

        # Invalid filter type
        with self.assertRaises(ValueError):
            self.update_settings.execute({"cv": {"filter_type": "invalid_filter"}})

        with self.assertRaises(ValueError):
            self.update_settings.execute({"notifications": {"duration_seconds": 0}})

        with self.assertRaises(ValueError):
            self.update_settings.execute({"scheduler": {"post_pass_max_wait_hours": 0}})

        with self.assertRaises(ValueError):
            self.update_settings.execute({"scheduler": {"post_pass_worker_count": 0}})

        self.update_settings.execute({
            "notifications": {"enabled": False, "show_error": True, "duration_seconds": 5}
        })
        notifications = self.get_settings.execute("notifications")
        self.assertFalse(notifications["enabled"])
        self.assertEqual(notifications["duration_seconds"], 5.0)

        self.update_settings.execute({"scheduler": {"post_pass_max_wait_hours": 48}})
        self.assertEqual(self.get_settings.execute("scheduler")["post_pass_max_wait_hours"], 48.0)
        self.update_settings.execute({"scheduler": {"post_pass_worker_count": 4}})
        self.assertEqual(self.get_settings.execute("scheduler")["post_pass_worker_count"], 4)


class TestSettingsWebAPI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp_dir.name) / "test_web.db"
        settings = Settings(
            copernicus_username="test_user",
            copernicus_password="test_pass",
            n2yo_api_key="test_n2yo",
            project_root=Path(__file__).resolve().parent.parent,
            database_path=db_path,
            output_root=Path(self.tmp_dir.name) / "output",
            cache_root=Path(self.tmp_dir.name) / "cache",
        )
        container = ApplicationContainer(settings)
        app = create_app(settings=settings, container=container, start_background_workers=False)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_settings_page_html(self):
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Global Platform Settings", resp.data)
        self.assertIn(b"Coastal Noise Buffer", resp.data)
        # Verify credentials inputs are NOT rendered
        self.assertNotIn(b"input_imagery_copernicus_username", resp.data)
        self.assertNotIn(b"input_imagery_copernicus_password", resp.data)
        self.assertIn(b"Managed via .env", resp.data)

    def test_api_get_settings(self):
        resp = self.client.get("/api/settings?definitions=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("cv", data["settings"])
        self.assertEqual(data["settings"]["cv"]["coastal_buffer_pixels"]["value"], 81)
        self.assertNotIn("copernicus_username", data["settings"].get("imagery", {}))
        self.assertNotIn("copernicus_password", data["settings"].get("imagery", {}))
        self.assertNotIn("n2yo_api_key", data["settings"].get("scheduler", {}))
        self.assertNotIn("system", data["settings"])
        self.assertTrue(data["runtime"]["environment_owned"])

    def test_api_update_settings(self):
        payload = {
            "cv": {"coastal_buffer_pixels": 110}
        }
        resp = self.client.post("/api/settings", json=payload)
        self.assertEqual(resp.status_code, 200)
        result = resp.get_json()
        self.assertIn(result["apply_status"], ("APPLIED", "PARTIAL"))
        self.assertIn("applied", result)
        self.assertIn("apply_errors", result)
        self.assertEqual(result["requires_restart"], [])

        # Check updated
        resp = self.client.get("/api/settings?definitions=false")
        data = resp.get_json()
        self.assertEqual(data["settings"]["cv"]["coastal_buffer_pixels"], 110)

    def test_api_reset_settings(self):
        # Update first
        self.client.post("/api/settings", json={"cv": {"coastal_buffer_pixels": 150}})

        # Reset section
        resp = self.client.post("/api/settings/reset", json={"section": "cv"})
        self.assertEqual(resp.status_code, 200)

        # Verify back to 81
        resp = self.client.get("/api/settings?definitions=false")
        data = resp.get_json()
        self.assertEqual(data["settings"]["cv"]["coastal_buffer_pixels"], 81)

    def test_api_generate_dem_endpoint_missing_scan(self):
        resp = self.client.post("/api/scan/nonexistent_scan/generate_dem")
        self.assertEqual(resp.status_code, 404)


class TestCoastalBufferCV(unittest.TestCase):
    def test_detector_coastal_buffer_validation(self):
        from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
        with self.assertRaises(ValueError):
            ClassicalShipDetector(coastal_buffer_pixels=-1)
        with self.assertRaises(ValueError):
            ClassicalShipDetector(morph_close_kernel=-5)

    def test_detector_with_settings_repo(self):
        from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector

        class FakeSettingsRepo:
            def __init__(self, settings_dict):
                self._dict = settings_dict
            def get(self, key, default=None):
                return self._dict.get(key, default)

        repo = FakeSettingsRepo({"minimum_area": 80.0, "maximum_area": 4000.0})
        detector = ClassicalShipDetector(settings_repo=repo)
        self.assertIsNotNone(detector._settings_repo)

    def test_detect_ships_use_case_coastal_buffer(self):
        from sentinel_analysis.application.use_cases.detect_ships import DetectShips
        from sentinel_analysis.domain.entities import ShipDetection
        from sentinel_analysis.application.ports.detection import DetectionResult

        class MockDetector:
            def __init__(self):
                self.received_buffer = None
            def detect(self, image_path, dem_path=None, threshold=40, coastal_buffer=None):
                self.received_buffer = coastal_buffer
                return DetectionResult([], 100, 100)

        mock = MockDetector()
        use_case = DetectShips(mock)
        
        # Valid execution with buffer
        res = use_case.execute(Path("dummy_sar.png"), coastal_buffer=55)
        self.assertEqual(mock.received_buffer, 55)

        # Invalid buffer
        with self.assertRaises(ValueError):
            use_case.execute(Path("dummy_sar.png"), coastal_buffer=-10)


if __name__ == "__main__":
    unittest.main()
