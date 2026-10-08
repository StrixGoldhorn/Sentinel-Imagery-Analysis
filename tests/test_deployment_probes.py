"""Tests for production deployment profile, Docker configuration, and health/readiness probes."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.interfaces.web.application import create_app


class TestDeploymentProbes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(__file__).resolve().parent.parent
        self.settings = Settings(
            copernicus_username="test",
            copernicus_password="pwd",
            n2yo_api_key="test_n2yo",
            project_root=self.project_root,
            output_root=f"{self.temp_dir.name}/output",
            cache_root=f"{self.temp_dir.name}/cache",
            database_path=f"{self.temp_dir.name}/test.db",
            debug=False,
        )
        self.container = ApplicationContainer(self.settings)
        self.app = create_app(self.settings, self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self):
        if hasattr(self, "container") and self.container is not None:
            try:
                self.container.shutdown(timeout=0.5)
            except Exception:
                pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_liveness_probe_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("uptime_seconds", data)
        self.assertIn("timestamp", data)
        self.assertEqual(data["version"], "1.0.0")

    def test_liveness_probe_livez_alias(self):
        res = self.client.get("/livez")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "healthy")

    def test_readiness_probe_success(self):
        res = self.client.get("/readyz")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ready")
        self.assertTrue(data["ready"])
        self.assertIn("checks", data)
        self.assertEqual(data["checks"]["database"]["status"], "ok")
        self.assertEqual(data["checks"]["output_storage"]["status"], "ok")
        self.assertEqual(data["checks"]["cache_storage"]["status"], "ok")

    def test_readiness_probe_database_failure(self):
        # Simulate SQLite operational error on connect
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("Simulated DB connection failure")):
            res = self.client.get("/readyz")
            self.assertEqual(res.status_code, 503)
            data = res.get_json()
            self.assertEqual(data["status"], "not_ready")
            self.assertFalse(data["ready"])
            self.assertEqual(data["checks"]["database"]["status"], "error")

    def test_readiness_probe_output_storage_failure(self):
        # Simulate filesystem permission error on touch
        with patch("pathlib.Path.touch", side_effect=PermissionError("Simulated read-only volume")):
            res = self.client.get("/readyz")
            self.assertEqual(res.status_code, 503)
            data = res.get_json()
            self.assertEqual(data["status"], "not_ready")
            self.assertFalse(data["ready"])
            self.assertEqual(data["checks"]["output_storage"]["status"], "error")

    def test_wsgi_module(self):
        import wsgi
        self.assertIsInstance(wsgi.app, Flask)
        self.assertIsNotNone(wsgi.settings)
        self.assertIsNotNone(wsgi.container)

    def test_gunicorn_conf(self):
        conf_path = self.project_root / "gunicorn.conf.py"
        self.assertTrue(conf_path.exists())
        conf_vars = {}
        with open(conf_path, "r", encoding="utf-8") as f:
            exec(f.read(), conf_vars)
        self.assertEqual(conf_vars["worker_class"], "gthread")
        self.assertIn("workers", conf_vars)
        self.assertIn("threads", conf_vars)
        self.assertEqual(conf_vars["timeout"], 120)

    def test_dockerfile_and_compose_structure(self):
        dockerfile = (self.project_root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("AS builder", dockerfile)
        self.assertIn("AS runner", dockerfile)
        self.assertIn("USER sentinel", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/healthz", dockerfile)

        compose = (self.project_root / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("sentinel-analysis", compose)
        self.assertIn("sentinel_data:", compose)
        self.assertIn("sentinel_scans:", compose)
        self.assertIn("sentinel_cache:", compose)
        self.assertIn("/healthz", compose)


if __name__ == "__main__":
    unittest.main()
