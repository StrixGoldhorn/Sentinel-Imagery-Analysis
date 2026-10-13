"""Tests for satellite provider health monitoring and quota dashboard."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sentinel_analysis.application.use_cases.get_provider_health import (
    CheckProviderHealth,
    GetProviderHealthDashboard,
    UpdateProviderQuotaUseCase,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.provider_health import (
    ProviderHealthRecord,
    ProviderQuotaRecord,
    ProviderStatus,
)
from sentinel_analysis.infrastructure.persistence.sqlite_provider_health import (
    SQLiteProviderHealthRepository,
)
from sentinel_analysis.interfaces.web.application import create_app


class TestProviderDashboardDomainAndRepo(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_providers.db"
        self.repo = SQLiteProviderHealthRepository(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_default_seeding(self) -> None:
        health_list = self.repo.list_health()
        quota_list = self.repo.list_quotas()

        self.assertGreaterEqual(len(health_list), 5)
        self.assertGreaterEqual(len(quota_list), 5)

        provider_ids = {h.provider_id for h in health_list}
        self.assertIn("copernicus", provider_ids)
        self.assertIn("asf", provider_ids)
        self.assertIn("umbra", provider_ids)
        self.assertIn("planet", provider_ids)
        self.assertIn("n2yo", provider_ids)

    def test_record_health_event_and_ema_latency(self) -> None:
        # Initial copernicus record
        rec = self.repo.get_health("copernicus")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, ProviderStatus.HEALTHY)

        # Record consecutive errors to drive status to DOWN
        for _ in range(4):
            rec = self.repo.record_health_event("copernicus", success=False, latency_ms=450.0, error_message="HTTP 503")
        self.assertEqual(rec.status, ProviderStatus.DOWN)
        self.assertEqual(rec.consecutive_failures, 4)

        # Record successful fast request
        rec = self.repo.record_health_event("copernicus", success=True, latency_ms=50.0)
        self.assertEqual(rec.consecutive_failures, 0)
        # Latency should move towards 50.0
        self.assertLess(rec.latency_ms, 450.0)

    def test_quota_tracking_and_updates(self) -> None:
        q = self.repo.get_quota("umbra")
        self.assertIsNotNone(q)
        initial_used = q.daily_requests_used

        # Record usage
        updated = self.repo.record_quota_usage("umbra", request_count=5)
        self.assertEqual(updated.daily_requests_used, initial_used + 5)
        self.assertEqual(updated.monthly_requests_used, 5)

        # Update limits and reset
        mod = self.repo.update_quota_parameters(
            "umbra",
            daily_request_limit=500,
            credit_balance_usd=12000.0,
            reset_daily_requests=True,
        )
        self.assertEqual(mod.daily_request_limit, 500)
        self.assertEqual(mod.credit_balance_usd, 12000.0)
        self.assertEqual(mod.daily_requests_used, 0)


class TestProviderDashboardUseCases(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_uc.db"
        self.repo = SQLiteProviderHealthRepository(self.db_path)
        self.get_dashboard = GetProviderHealthDashboard(self.repo)
        self.check_health = CheckProviderHealth(self.repo)
        self.update_quota = UpdateProviderQuotaUseCase(self.repo)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dashboard_summary_aggregation(self) -> None:
        dashboard = self.get_dashboard.execute()
        self.assertEqual(dashboard.total_providers, 5)
        self.assertEqual(dashboard.healthy_count, 5)
        self.assertEqual(dashboard.overall_status, ProviderStatus.HEALTHY)

        # Force one provider down
        self.check_health.execute("asf", force_status="DOWN")
        dashboard = self.get_dashboard.execute()
        self.assertEqual(dashboard.down_count, 1)
        self.assertEqual(dashboard.overall_status, ProviderStatus.DEGRADED)

    def test_check_health_and_quota_update(self) -> None:
        rec = self.check_health.execute("planet", force_status="HEALTHY", latency_override_ms=45.2)
        self.assertEqual(rec.status, ProviderStatus.HEALTHY)
        self.assertAlmostEqual(rec.latency_ms, 45.2, places=1)

        q = self.update_quota.execute("planet", daily_request_limit=3000, credit_balance_usd=850.0)
        self.assertEqual(q.daily_request_limit, 3000)
        self.assertEqual(q.credit_balance_usd, 850.0)


class TestProviderDashboardWebEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_web.db"
        out_path = Path(self.temp_dir.name) / "scans"
        out_path.mkdir(parents=True, exist_ok=True)

        project_root = Path(__file__).resolve().parent.parent
        settings = Settings(
            project_root=project_root,
            database_path=db_path,
            output_root=out_path,
            cache_root=Path(self.temp_dir.name) / "cache",
            copernicus_username="test_user",
            copernicus_password="test_password",
            n2yo_api_key="test_key",
        )
        self.container = ApplicationContainer(settings)
        self.app = create_app(settings=settings, container=self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_api_providers_health(self) -> None:
        resp = self.client.get("/api/providers/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("total_providers", data)
        self.assertIn("healthy_count", data)
        self.assertIn("providers", data)
        self.assertGreaterEqual(len(data["providers"]), 5)

    def test_api_providers_quotas(self) -> None:
        resp = self.client.get("/api/providers/quotas")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 5)

    def test_api_probe_and_quota_update(self) -> None:
        # Probe
        resp = self.client.post("/api/providers/copernicus/check", json={"force_status": "HEALTHY", "latency_ms": 75.0})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["record"]["status"], "HEALTHY")

        # Quota update
        resp2 = self.client.post("/api/providers/copernicus/quota", json={"daily_request_limit": 5000, "reset_daily_requests": True})
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertEqual(data2["status"], "success")
        self.assertEqual(data2["quota"]["daily_request_limit"], 5000)

    def test_providers_html_dashboard_page(self) -> None:
        resp = self.client.get("/providers")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Satellite Provider Health", html)
        self.assertIn("Copernicus CDSE", html)
        self.assertIn("ASF DAAC", html)


if __name__ == "__main__":
    unittest.main()
