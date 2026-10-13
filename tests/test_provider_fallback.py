"""Tests for automatic provider fallback and failover chains."""

from __future__ import annotations

from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from sentinel_analysis.application.use_cases.acquire_with_fallback import (
    AcquireWithProviderFallback,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.domain.provider_fallback import (
    AllProvidersExhaustedError,
    FallbackReason,
)
from sentinel_analysis.domain.provider_health import (
    ProviderHealthRecord,
    ProviderQuotaRecord,
    ProviderStatus,
)
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.infrastructure.persistence.sqlite_provider_health import (
    SQLiteProviderHealthRepository,
)
from sentinel_analysis.interfaces.web.application import create_app


class TestProviderFallbackEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_fallback.db"
        self.health_repo = SQLiteProviderHealthRepository(self.db_path)
        self.aoi_repo = SQLiteAreaOfInterestRepository(self.db_path)
        self.fallback = AcquireWithProviderFallback(
            health_repository=self.health_repo,
            aoi_repository=self.aoi_repo,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _set_provider_status(self, provider_id: str, status: ProviderStatus, message: str = "Outage") -> None:
        rec = self.health_repo.get_health(provider_id)
        assert rec is not None
        updated = ProviderHealthRecord(
            provider_id=rec.provider_id,
            name=rec.name,
            status=status,
            latency_ms=rec.latency_ms,
            error_rate_pct=rec.error_rate_pct,
            consecutive_failures=rec.consecutive_failures,
            status_message=message,
            endpoint_url=rec.endpoint_url,
        )
        self.health_repo.save_health(updated)

    def test_default_chains_configured(self) -> None:
        chains = self.fallback.get_all_chains()
        self.assertIn("SAR", chains)
        self.assertIn("OPTICAL", chains)
        self.assertIn("PASS_PREDICTION", chains)
        self.assertEqual(chains["SAR"], ["copernicus", "asf", "umbra"])
        self.assertEqual(chains["OPTICAL"], ["copernicus", "planet"])

    def test_primary_provider_success_no_failover(self) -> None:
        result, resl = self.fallback.execute_dispatch(modality="SAR")
        self.assertEqual(resl.primary_provider_id, "copernicus")
        self.assertEqual(resl.selected_provider_id, "copernicus")
        self.assertFalse(resl.fallback_occurred)
        self.assertEqual(len(resl.attempts), 1)
        self.assertTrue(resl.attempts[0].success)

        # Check quota was consumed
        quota = self.health_repo.get_quota("copernicus")
        self.assertIsNotNone(quota)
        self.assertGreater(quota.daily_requests_used, 0)

    def test_primary_unhealthy_fails_over_to_secondary(self) -> None:
        # Mark copernicus as DOWN
        self._set_provider_status("copernicus", ProviderStatus.DOWN, "Scheduled maintenance outage")

        result, resl = self.fallback.execute_dispatch(modality="SAR")
        self.assertEqual(resl.primary_provider_id, "copernicus")
        self.assertEqual(resl.selected_provider_id, "asf")
        self.assertTrue(resl.fallback_occurred)
        self.assertEqual(len(resl.attempts), 2)

        # Attempt 1 failed due to UNHEALTHY_STATUS
        self.assertFalse(resl.attempts[0].success)
        self.assertEqual(resl.attempts[0].provider_id, "copernicus")
        self.assertEqual(resl.attempts[0].reason, FallbackReason.UNHEALTHY_STATUS)

        # Attempt 2 succeeded with asf
        self.assertTrue(resl.attempts[1].success)
        self.assertEqual(resl.attempts[1].provider_id, "asf")

    def test_primary_quota_exhausted_fails_over_to_secondary(self) -> None:
        # Exhaust copernicus daily quota
        cop_quota = self.health_repo.get_quota("copernicus")
        self.assertIsNotNone(cop_quota)
        exhausted = ProviderQuotaRecord(
            provider_id=cop_quota.provider_id,
            daily_request_limit=cop_quota.daily_request_limit,
            daily_requests_used=cop_quota.daily_request_limit,
            monthly_request_limit=cop_quota.monthly_request_limit,
            monthly_requests_used=cop_quota.monthly_requests_used,
            rate_limit_per_minute=cop_quota.rate_limit_per_minute,
            credit_balance_usd=cop_quota.credit_balance_usd,
        )
        self.health_repo.save_quota(exhausted)

        result, resl = self.fallback.execute_dispatch(modality="SAR")
        self.assertEqual(resl.selected_provider_id, "asf")
        self.assertTrue(resl.fallback_occurred)
        self.assertEqual(resl.attempts[0].reason, FallbackReason.QUOTA_EXHAUSTED)
        self.assertTrue(resl.attempts[1].success)

    def test_commercial_policy_disallowed_blocks_umbra(self) -> None:
        # Copernicus down, ASF down
        self._set_provider_status("copernicus", ProviderStatus.DOWN)
        self._set_provider_status("asf", ProviderStatus.DOWN)

        # Disallow commercial
        with self.assertRaises(AllProvidersExhaustedError) as ctx:
            self.fallback.execute_dispatch(modality="SAR", allow_commercial=False)

        resl = ctx.exception.resolution
        self.assertIsNotNone(resl)
        self.assertEqual(len(resl.attempts), 3)
        self.assertEqual(resl.attempts[2].provider_id, "umbra")
        self.assertEqual(resl.attempts[2].reason, FallbackReason.INSUFFICIENT_FUNDS)

    def test_aoi_budget_exhaustion_blocks_commercial_fallback(self) -> None:
        # Seed AOI with exhausted budget
        aoi = AreaOfInterest(
            name="Strait of Hormuz Patrol",
            bbox=BoundingBox(56.0, 26.0, 56.5, 26.5),
            collection_budget_usd=100.0,
            budget_spent_usd=100.0,
        )
        aoi_id = self.aoi_repo.add(aoi)

        # Mark copernicus & asf down
        self._set_provider_status("copernicus", ProviderStatus.DOWN)
        self._set_provider_status("asf", ProviderStatus.DOWN)

        with self.assertRaises(AllProvidersExhaustedError) as ctx:
            self.fallback.execute_dispatch(modality="SAR", aoi_id=aoi_id, allow_commercial=True)

        resl = ctx.exception.resolution
        self.assertIsNotNone(resl)
        self.assertEqual(resl.attempts[2].provider_id, "umbra")
        self.assertEqual(resl.attempts[2].reason, FallbackReason.INSUFFICIENT_FUNDS)
        self.assertIn("budget exhausted", resl.attempts[2].error_message)

    def test_runtime_dispatcher_exception_failover(self) -> None:
        def failing_copernicus(**kwargs):
            raise ConnectionResetError("Remote Copernicus API closed connection abruptly")

        def working_asf(**kwargs):
            return {"source": "asf", "data": "synthetic_aperture_radar_ok"}

        self.fallback.register_dispatcher("copernicus", failing_copernicus)
        self.fallback.register_dispatcher("asf", working_asf)

        result, resl = self.fallback.execute_dispatch(modality="SAR")
        self.assertEqual(result["source"], "asf")
        self.assertEqual(resl.selected_provider_id, "asf")
        self.assertTrue(resl.fallback_occurred)
        self.assertEqual(resl.attempts[0].reason, FallbackReason.TIMEOUT_OR_NETWORK_ERROR)
        self.assertIn("Remote Copernicus API", resl.attempts[0].error_message)

        # Health repo recorded the failure
        health = self.health_repo.get_health("copernicus")
        self.assertGreater(health.consecutive_failures, 0)
        self.assertIsNotNone(health.last_failure_at)

    def test_preferred_provider_order_preference(self) -> None:
        result, resl = self.fallback.execute_dispatch(modality="SAR", preferred_provider="asf")
        self.assertEqual(resl.primary_provider_id, "asf")
        self.assertEqual(resl.selected_provider_id, "asf")
        self.assertFalse(resl.fallback_occurred)


class TestProviderFallbackWebAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_fallback_web.db"
        project_root = Path(__file__).resolve().parent.parent
        settings = Settings(
            project_root=project_root,
            database_path=self.db_path,
            output_root=Path(self.temp_dir.name) / "output",
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

    def _set_provider_status(self, provider_id: str, status: ProviderStatus) -> None:
        rec = self.container.provider_health_repository.get_health(provider_id)
        assert rec is not None
        updated = ProviderHealthRecord(
            provider_id=rec.provider_id,
            name=rec.name,
            status=status,
            latency_ms=rec.latency_ms,
            error_rate_pct=rec.error_rate_pct,
            consecutive_failures=rec.consecutive_failures,
            status_message="Down",
            endpoint_url=rec.endpoint_url,
        )
        self.container.provider_health_repository.save_health(updated)

    def test_get_fallback_chains_api(self) -> None:
        resp = self.client.get("/api/providers/fallback-chains")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("SAR", data["chains"])
        self.assertEqual(data["chains"]["SAR"], ["copernicus", "asf", "umbra"])

    def test_dispatch_with_fallback_nominal_api(self) -> None:
        resp = self.client.post(
            "/api/providers/dispatch-with-fallback",
            json={
                "modality": "SAR",
                "preferred_provider": "copernicus",
                "allow_commercial": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["resolution"]["selected_provider_id"], "copernicus")

    def test_dispatch_with_fallback_all_exhausted_api(self) -> None:
        # Mark all SAR providers down
        self._set_provider_status("copernicus", ProviderStatus.DOWN)
        self._set_provider_status("asf", ProviderStatus.DOWN)
        self._set_provider_status("umbra", ProviderStatus.DOWN)

        resp = self.client.post(
            "/api/providers/dispatch-with-fallback",
            json={"modality": "SAR"},
        )
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertEqual(data["status"], "error")
        self.assertTrue(data["resolution"]["metadata"].get("exhausted"))


if __name__ == "__main__":
    unittest.main()
