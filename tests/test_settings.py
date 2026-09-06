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
        app = create_app(settings=settings, container=container)
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

    def test_api_get_settings(self):
        resp = self.client.get("/api/settings?definitions=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("cv", data["settings"])
        self.assertEqual(data["settings"]["cv"]["coastal_buffer_pixels"]["value"], 81)

    def test_api_update_settings(self):
        payload = {
            "cv": {"coastal_buffer_pixels": 110}
        }
        resp = self.client.post("/api/settings", json=payload)
        self.assertEqual(resp.status_code, 200)

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


if __name__ == "__main__":
    unittest.main()
