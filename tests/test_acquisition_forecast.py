"""Tests for satellite acquisition cost and storage capacity forecasting engine."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from sentinel_analysis.application.use_cases.forecast_acquisition_storage import (
    DEFAULT_REVISIT_BY_PRIORITY,
    ForecastAcquisitionAndStorage,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.domain.forecast import (
    DEFAULT_PROVIDER_PROFILES,
    AoiAcquisitionForecast,
    BudgetHealthStatus,
    ProviderCostStorageProfile,
    StorageHealthStatus,
    SystemForecastSummary,
)
from sentinel_analysis.infrastructure.persistence.sqlite_aois import (
    SQLiteAreaOfInterestRepository,
)
from sentinel_analysis.interfaces.web.application import create_app


class FakeStorageQuotaUseCase:
    def __init__(self, used_bytes: int, quota_bytes: int) -> None:
        self.used_bytes = used_bytes
        self.quota_bytes = quota_bytes

    def execute(self):
        class QuotaReport:
            def __init__(self, u: int, q: int):
                self.total_bytes_used = u
                self.quota_bytes = q
        return QuotaReport(self.used_bytes, self.quota_bytes)


class TestForecastDomainModels(unittest.TestCase):
    def test_provider_profile_validation(self) -> None:
        # Valid profile
        profile = ProviderCostStorageProfile(
            provider_id="test-sar",
            name="Test SAR Satellite",
            modality="SAR",
            cost_per_scene_usd=120.0,
            avg_scene_size_gb=0.8,
            is_commercial=True,
        )
        self.assertEqual(profile.provider_id, "test-sar")
        self.assertEqual(profile.cost_per_scene_usd, 120.0)
        self.assertTrue(profile.is_commercial)

        # Empty provider ID
        with self.assertRaises(DomainValidationError):
            ProviderCostStorageProfile(
                provider_id="",
                name="Invalid",
                modality="SAR",
                cost_per_scene_usd=50.0,
                avg_scene_size_gb=1.0,
            )

        # Negative cost
        with self.assertRaises(DomainValidationError):
            ProviderCostStorageProfile(
                provider_id="test",
                name="Invalid",
                modality="SAR",
                cost_per_scene_usd=-10.0,
                avg_scene_size_gb=1.0,
            )

        # Zero or negative size
        with self.assertRaises(DomainValidationError):
            ProviderCostStorageProfile(
                provider_id="test",
                name="Invalid",
                modality="SAR",
                cost_per_scene_usd=0.0,
                avg_scene_size_gb=0.0,
            )

    def test_default_provider_profiles_seeded(self) -> None:
        self.assertIn("copernicus", DEFAULT_PROVIDER_PROFILES)
        self.assertIn("asf", DEFAULT_PROVIDER_PROFILES)
        self.assertIn("sentinel-2", DEFAULT_PROVIDER_PROFILES)
        self.assertIn("umbra", DEFAULT_PROVIDER_PROFILES)
        self.assertIn("planet", DEFAULT_PROVIDER_PROFILES)
        self.assertIn("capella", DEFAULT_PROVIDER_PROFILES)

        # Copernicus is open-access (free)
        self.assertEqual(DEFAULT_PROVIDER_PROFILES["copernicus"].cost_per_scene_usd, 0.0)
        self.assertFalse(DEFAULT_PROVIDER_PROFILES["copernicus"].is_commercial)

        # Umbra is commercial
        self.assertGreater(DEFAULT_PROVIDER_PROFILES["umbra"].cost_per_scene_usd, 0.0)
        self.assertTrue(DEFAULT_PROVIDER_PROFILES["umbra"].is_commercial)

    def test_aoi_forecast_to_dict(self) -> None:
        fc = AoiAcquisitionForecast(
            aoi_id=1,
            aoi_name="Taiwan Strait",
            priority="CRITICAL",
            horizon_days=30,
            desired_revisit_rate_hours=12.0,
            total_projected_passes=60,
            open_access_passes=15,
            commercial_passes=45,
            projected_cost_usd=15750.0,
            daily_cost_burn_usd=525.0,
            daily_storage_ingest_gb=1.375,
            projected_storage_gb=41.25,
            steady_state_storage_gb=41.25,
            retention_policy_days=30,
            remaining_budget_usd=10000.0,
            days_until_budget_exhaustion=19.04,
            budget_status=BudgetHealthStatus.WARNING,
        )
        d = fc.to_dict()
        self.assertEqual(d["aoi_name"], "Taiwan Strait")
        self.assertEqual(d["budget_status"], "WARNING")
        self.assertEqual(d["commercial_passes"], 45)
        self.assertEqual(d["projected_cost_usd"], 15750.0)


class TestForecastAcquisitionAndStorageUseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_forecast.db"
        self.aoi_repo = SQLiteAreaOfInterestRepository(self.db_path)
        self.bbox = BoundingBox(120.0, 22.0, 122.0, 24.0)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_single_aoi_standard_open_access_medium_priority(self) -> None:
        aoi = AreaOfInterest(
            id=1,
            name="South China Sea Patrol",
            bbox=self.bbox,
            priority="MEDIUM",
            collection_budget_usd=0.0,
            desired_revisit_rate_hours=48.0,
            retention_policy_days=60,
        )
        self.aoi_repo.add(aoi)

        use_case = ForecastAcquisitionAndStorage(aoi_repository=self.aoi_repo)
        fc = use_case.forecast_aoi(aoi, horizon_days=30)

        self.assertEqual(fc.aoi_name, "South China Sea Patrol")
        self.assertEqual(fc.priority, "MEDIUM")
        self.assertEqual(fc.desired_revisit_rate_hours, 48.0)
        # 24 / 48 = 0.5 passes/day * 30 days = 15 passes
        self.assertEqual(fc.total_projected_passes, 15)
        self.assertEqual(fc.open_access_passes, 15)
        self.assertEqual(fc.commercial_passes, 0)
        self.assertEqual(fc.projected_cost_usd, 0.0)
        self.assertEqual(fc.budget_status, BudgetHealthStatus.UNCONSTRAINED)
        # 15 passes * 1.4 GB = 21 GB
        self.assertAlmostEqual(fc.projected_storage_gb, 21.0, places=1)
        # Steady state storage = (0.5 * 1.4) * 60 days = 42.0 GB
        self.assertAlmostEqual(fc.steady_state_storage_gb, 42.0, places=1)

    def test_high_priority_commercial_allocation_and_budget_warning(self) -> None:
        # High cadence: 12h revisit = 2 passes/day
        # Over 30 days: total = 60 passes.
        # Copernicus covers 0.5 passes/day (15 open passes), Umbra covers 1.5 passes/day (45 commercial passes).
        # Cost = 45 * $350 = $15,750. Daily burn = 1.5 * $350 = $525/day.
        # Budget = $5,000 -> Runway = 5000 / 525 = 9.5 days < 30 days horizon -> WARNING status.
        aoi = AreaOfInterest(
            id=2,
            name="Strait of Malacca Rapid Tasking",
            bbox=self.bbox,
            priority="CRITICAL",
            collection_budget_usd=5000.0,
            budget_spent_usd=0.0,
            desired_revisit_rate_hours=12.0,
            retention_policy_days=30,
        )
        self.aoi_repo.add(aoi)

        use_case = ForecastAcquisitionAndStorage(aoi_repository=self.aoi_repo)
        fc = use_case.forecast_aoi(aoi, horizon_days=30)

        self.assertEqual(fc.total_projected_passes, 60)
        self.assertEqual(fc.open_access_passes, 15)
        self.assertEqual(fc.commercial_passes, 45)
        self.assertEqual(fc.projected_cost_usd, 15750.0)
        self.assertEqual(fc.daily_cost_burn_usd, 525.0)
        self.assertEqual(fc.budget_status, BudgetHealthStatus.WARNING)
        self.assertIsNotNone(fc.days_until_budget_exhaustion)
        self.assertAlmostEqual(fc.days_until_budget_exhaustion, 5000.0 / 525.0, places=1)

    def test_exhausted_budget_status(self) -> None:
        aoi = AreaOfInterest(
            id=3,
            name="Exhausted Budget AOI",
            bbox=self.bbox,
            priority="HIGH",
            collection_budget_usd=2000.0,
            budget_spent_usd=2000.0,
            desired_revisit_rate_hours=12.0,
        )
        self.aoi_repo.add(aoi)

        use_case = ForecastAcquisitionAndStorage(aoi_repository=self.aoi_repo)
        fc = use_case.forecast_aoi(aoi, horizon_days=30)

        self.assertEqual(fc.remaining_budget_usd, 0.0)
        self.assertEqual(fc.budget_status, BudgetHealthStatus.EXHAUSTED)
        self.assertEqual(fc.days_until_budget_exhaustion, 0.0)

    def test_invalid_horizon_days_raises_validation_error(self) -> None:
        aoi = AreaOfInterest(
            id=1,
            name="Test AOI",
            bbox=self.bbox,
        )
        use_case = ForecastAcquisitionAndStorage(aoi_repository=self.aoi_repo)
        with self.assertRaises(DomainValidationError):
            use_case.forecast_aoi(aoi, horizon_days=0)
        with self.assertRaises(DomainValidationError):
            use_case.forecast_aoi(aoi, horizon_days=-5)

    def test_system_summary_aggregation_and_storage_quota_runway(self) -> None:
        aoi1 = AreaOfInterest(
            id=1,
            name="AOI 1",
            bbox=self.bbox,
            priority="LOW",
            collection_budget_usd=0.0,
            desired_revisit_rate_hours=96.0,  # 0.25 passes/day
            retention_policy_days=30,
        )
        aoi2 = AreaOfInterest(
            id=2,
            name="AOI 2",
            bbox=self.bbox,
            priority="CRITICAL",
            collection_budget_usd=1000.0,
            desired_revisit_rate_hours=12.0,  # 2.0 passes/day (0.5 open, 1.5 comm)
            retention_policy_days=None,  # indefinite
        )
        self.aoi_repo.add(aoi1)
        self.aoi_repo.add(aoi2)

        # Quota: 100 GB total, 80 GB used -> 20 GB remaining
        fake_quota = FakeStorageQuotaUseCase(
            used_bytes=80 * (1024 ** 3),
            quota_bytes=100 * (1024 ** 3),
        )

        use_case = ForecastAcquisitionAndStorage(
            aoi_repository=self.aoi_repo,
            get_storage_quota=fake_quota,
        )
        summary = use_case.execute(horizon_days=30)

        self.assertEqual(summary.total_aois, 2)
        self.assertEqual(summary.horizon_days, 30)
        self.assertGreater(summary.total_projected_passes, 0)
        self.assertGreater(summary.total_commercial_passes, 0)
        self.assertGreater(summary.total_projected_cost_usd, 0.0)
        self.assertAlmostEqual(summary.storage_quota_gb, 100.0, places=1)
        self.assertAlmostEqual(summary.current_storage_used_gb, 80.0, places=1)
        self.assertIsNotNone(summary.days_until_storage_quota)

        # Recommendations should highlight budget warning for AOI 2, indefinite retention, and quota warning
        self.assertGreater(len(summary.recommendations), 0)
        rec_text = " ".join(summary.recommendations)
        self.assertIn("AOI 2", rec_text)
        self.assertIn("indefinite retention", rec_text)


class TestForecastWebEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "web_test.db"
        output_dir = Path(self.temp_dir.name) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        project_root = Path(__file__).resolve().parent.parent
        settings = Settings(
            project_root=project_root,
            database_path=db_path,
            output_root=output_dir,
            cache_root=output_dir / "cache",
            copernicus_username="test_user",
            copernicus_password="test_password",
            n2yo_api_key="test_key",
            storage_quota_bytes=500 * 1024 * 1024 * 1024,
        )
        self.container = ApplicationContainer(settings)

        # Seed an AOI
        bbox = BoundingBox(10.0, 20.0, 11.0, 21.0)
        aoi = AreaOfInterest(
            name="Test Surveillance Sector",
            bbox=bbox,
            priority="HIGH",
            collection_budget_usd=3000.0,
            desired_revisit_rate_hours=24.0,
            retention_policy_days=45,
        )
        self.container.aoi_repository.add(aoi)

        self.app = create_app(settings=settings, container=self.container, start_background_workers=False)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_forecast_api_success(self) -> None:
        res = self.client.get("/api/forecast/acquisition-storage?horizon_days=60")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("forecast", data)
        fc = data["forecast"]
        self.assertEqual(fc["horizon_days"], 60)
        self.assertEqual(fc["total_aois"], 1)
        self.assertGreater(fc["total_projected_passes"], 0)
        self.assertIn("aoi_forecasts", fc)
        self.assertEqual(len(fc["aoi_forecasts"]), 1)
        self.assertEqual(fc["aoi_forecasts"][0]["aoi_name"], "Test Surveillance Sector")

    def test_providers_dashboard_renders_forecast_card(self) -> None:
        res = self.client.get("/providers")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Acquisition Cost & Storage Forecast", html)
        self.assertIn("Test Surveillance Sector", html)
        self.assertIn("Projected Passes", html)
        self.assertIn("Commercial Cost", html)
        self.assertIn("Storage Quota Runway", html)


if __name__ == "__main__":
    unittest.main()
