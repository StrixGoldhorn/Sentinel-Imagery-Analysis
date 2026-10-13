"""Use case for satellite acquisition cost and storage capacity forecasting."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.domain.entities import AreaOfInterest
from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.domain.forecast import (
    DEFAULT_PROVIDER_PROFILES,
    AoiAcquisitionForecast,
    BudgetHealthStatus,
    ProviderCostStorageProfile,
    StorageHealthStatus,
    SystemForecastSummary,
)

logger = logging.getLogger(__name__)

DEFAULT_REVISIT_BY_PRIORITY: dict[str, float] = {
    "CRITICAL": 12.0,
    "HIGH": 24.0,
    "MEDIUM": 48.0,
    "LOW": 96.0,
}


class ForecastAcquisitionAndStorage:
    """Forecasts satellite acquisition expenditure, data ingestion, and storage retention equilibria."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        storage_manager: Optional[Any] = None,
        get_storage_quota: Optional[Any] = None,
        default_storage_quota_bytes: int = 500 * 1024 * 1024 * 1024,  # 500 GB default
        provider_profiles: Optional[dict[str, ProviderCostStorageProfile]] = None,
    ) -> None:
        self._aoi_repo = aoi_repository
        self._storage_manager = storage_manager
        self._get_storage_quota = get_storage_quota
        self._default_quota_bytes = default_storage_quota_bytes
        self._profiles = dict(DEFAULT_PROVIDER_PROFILES)
        if provider_profiles:
            self._profiles.update(provider_profiles)

    def forecast_aoi(self, aoi: AreaOfInterest, horizon_days: int = 30) -> AoiAcquisitionForecast:
        """Calculate acquisition pass distribution, financial cost, and storage footprint for an AOI."""
        if horizon_days <= 0:
            raise DomainValidationError("Forecast horizon days must be a positive integer")

        priority = (aoi.priority or "MEDIUM").upper()
        # 1. Determine cadence and daily pass rate
        revisit_hours = (
            aoi.desired_revisit_rate_hours
            if aoi.desired_revisit_rate_hours and aoi.desired_revisit_rate_hours > 0
            else DEFAULT_REVISIT_BY_PRIORITY.get(priority, 48.0)
        )
        passes_per_day = 24.0 / revisit_hours
        total_projected_passes = max(1, int(round(passes_per_day * horizon_days)))

        # 2. Split between Open (Sentinel-1) and Commercial (e.g. Umbra/Capella)
        # Natural baseline Copernicus Sentinel-1 orbital pass frequency is ~0.5 passes/day (every 48 hrs)
        natural_open_passes_per_day = 0.5

        if priority in ("CRITICAL", "HIGH") and passes_per_day > natural_open_passes_per_day:
            open_passes_per_day = natural_open_passes_per_day
            commercial_passes_per_day = passes_per_day - natural_open_passes_per_day
        elif priority in ("CRITICAL", "HIGH") and aoi.collection_budget_usd > 0.0:
            # High priority with explicit collection budget: allocate 30% commercial rapid spotlight tasking
            commercial_passes_per_day = passes_per_day * 0.35
            open_passes_per_day = passes_per_day - commercial_passes_per_day
        else:
            open_passes_per_day = passes_per_day
            commercial_passes_per_day = 0.0

        commercial_passes = int(round(commercial_passes_per_day * horizon_days))
        open_passes = max(0, total_projected_passes - commercial_passes)

        # 3. Calculate Financial Cost
        commercial_scene_cost = self._profiles.get("umbra", DEFAULT_PROVIDER_PROFILES["umbra"]).cost_per_scene_usd
        daily_cost_burn_usd = commercial_passes_per_day * commercial_scene_cost
        projected_cost_usd = commercial_passes * commercial_scene_cost

        # 4. Budget Status & Exhaustion Runway
        remaining_budget = aoi.remaining_budget_usd

        if aoi.collection_budget_usd <= 0.0:
            budget_status = BudgetHealthStatus.UNCONSTRAINED
            days_until_exhaustion = None
        elif remaining_budget <= 0.0 and daily_cost_burn_usd > 0.0:
            budget_status = BudgetHealthStatus.EXHAUSTED
            days_until_exhaustion = 0.0
        elif daily_cost_burn_usd > 0.0:
            runway_days = remaining_budget / daily_cost_burn_usd
            days_until_exhaustion = runway_days
            budget_status = (
                BudgetHealthStatus.WARNING if runway_days < horizon_days else BudgetHealthStatus.SUFFICIENT
            )
        else:
            budget_status = BudgetHealthStatus.SUFFICIENT
            days_until_exhaustion = None

        # 5. Storage Footprint Calculations
        open_scene_gb = self._profiles.get("copernicus", DEFAULT_PROVIDER_PROFILES["copernicus"]).avg_scene_size_gb
        comm_scene_gb = self._profiles.get("umbra", DEFAULT_PROVIDER_PROFILES["umbra"]).avg_scene_size_gb

        daily_storage_ingest_gb = (open_passes_per_day * open_scene_gb) + (commercial_passes_per_day * comm_scene_gb)
        projected_storage_gb = (open_passes * open_scene_gb) + (commercial_passes * comm_scene_gb)

        # Steady-state retention equilibrium
        retention_days = aoi.retention_policy_days if aoi.retention_policy_days else 90
        steady_state_storage_gb = daily_storage_ingest_gb * retention_days

        return AoiAcquisitionForecast(
            aoi_id=aoi.id or 0,
            aoi_name=aoi.name,
            priority=priority,
            horizon_days=horizon_days,
            desired_revisit_rate_hours=revisit_hours,
            total_projected_passes=total_projected_passes,
            open_access_passes=open_passes,
            commercial_passes=commercial_passes,
            projected_cost_usd=projected_cost_usd,
            daily_cost_burn_usd=daily_cost_burn_usd,
            daily_storage_ingest_gb=daily_storage_ingest_gb,
            projected_storage_gb=projected_storage_gb,
            steady_state_storage_gb=steady_state_storage_gb,
            retention_policy_days=aoi.retention_policy_days,
            remaining_budget_usd=remaining_budget,
            days_until_budget_exhaustion=days_until_exhaustion,
            budget_status=budget_status,
        )

    def execute(self, horizon_days: int = 30, aoi_id: Optional[int] = None) -> SystemForecastSummary:
        """Generate comprehensive system-wide satellite acquisition cost and storage runway forecast."""
        horizon = max(1, min(int(horizon_days), 365))
        now = datetime.now(timezone.utc)

        # Retrieve target AOIs
        if aoi_id is not None and aoi_id > 0:
            aoi = self._aoi_repo.get(aoi_id)
            aois = [aoi] if aoi is not None else []
        else:
            aois = self._aoi_repo.list()

        # Compute per-AOI forecasts
        aoi_forecasts = [self.forecast_aoi(a, horizon_days=horizon) for a in aois]

        # Aggregate totals
        total_projected_passes = sum(f.total_projected_passes for f in aoi_forecasts)
        total_open_passes = sum(f.open_access_passes for f in aoi_forecasts)
        total_commercial_passes = sum(f.commercial_passes for f in aoi_forecasts)
        total_projected_cost_usd = sum(f.projected_cost_usd for f in aoi_forecasts)
        total_daily_cost_burn_usd = sum(f.daily_cost_burn_usd for f in aoi_forecasts)
        total_daily_storage_ingest_gb = sum(f.daily_storage_ingest_gb for f in aoi_forecasts)
        total_projected_storage_gb = sum(f.projected_storage_gb for f in aoi_forecasts)

        # Current storage metrics
        current_used_gb = 0.0
        quota_gb = self._default_quota_bytes / (1024.0 ** 3)

        if self._get_storage_quota is not None:
            try:
                report = self._get_storage_quota.execute()
                current_used_gb = report.total_bytes_used / (1024.0 ** 3)
                if report.quota_bytes > 0:
                    quota_gb = report.quota_bytes / (1024.0 ** 3)
            except Exception as exc:
                logger.warning("Could not obtain live storage quota report: %s", exc)
        elif self._storage_manager is not None:
            try:
                report = self._storage_manager.get_quota_report(self._default_quota_bytes)
                current_used_gb = report.total_bytes_used / (1024.0 ** 3)
                quota_gb = report.quota_bytes / (1024.0 ** 3)
            except Exception as exc:
                logger.warning("Could not calculate storage manager report: %s", exc)

        # Days until storage quota
        days_until_storage_quota: Optional[float] = None
        if quota_gb > 0 and total_daily_storage_ingest_gb > 0:
            remaining_storage_gb = max(0.0, quota_gb - current_used_gb)
            days_until_storage_quota = remaining_storage_gb / total_daily_storage_ingest_gb

        # Storage health status
        if current_used_gb >= quota_gb:
            storage_status = StorageHealthStatus.CRITICAL_EXCEEDED
        elif days_until_storage_quota is not None and days_until_storage_quota < 14.0:
            storage_status = StorageHealthStatus.WARNING_IMMINENT
        else:
            storage_status = StorageHealthStatus.HEALTHY

        # Smart Operational Recommendations
        recommendations: list[str] = []

        # Check for AOIs nearing budget depletion
        for f in aoi_forecasts:
            if f.budget_status == BudgetHealthStatus.EXHAUSTED:
                recommendations.append(
                    f"AOI '{f.aoi_name}' has exhausted its collection budget ($0.00 remaining). Commercial tasking is paused."
                )
            elif f.budget_status == BudgetHealthStatus.WARNING and f.days_until_budget_exhaustion is not None:
                recommendations.append(
                    f"AOI '{f.aoi_name}' ({f.priority}) collection budget will be exhausted in {f.days_until_budget_exhaustion:.1f} days at current ${f.daily_cost_burn_usd:.2f}/day burn."
                )

        # Storage recommendations
        if storage_status == StorageHealthStatus.CRITICAL_EXCEEDED:
            recommendations.append(
                f"Storage quota ({quota_gb:.1f} GB) is exceeded (currently {current_used_gb:.1f} GB used). Immediate scan pruning or quota expansion required."
            )
        elif storage_status == StorageHealthStatus.WARNING_IMMINENT and days_until_storage_quota is not None:
            recommendations.append(
                f"Storage quota will be reached in {days_until_storage_quota:.1f} days at current {total_daily_storage_ingest_gb:.2f} GB/day ingestion rate. Consider enabling tighter retention policies."
            )

        # Retention policy recommendations
        unretained_count = sum(1 for f in aoi_forecasts if f.retention_policy_days is None)
        if unretained_count > 0:
            recommendations.append(
                f"{unretained_count} AOI(s) have indefinite retention. Enforcing 30-day retention policies would save approximately {(total_daily_storage_ingest_gb * 60.0):.1f} GB over a 90-day horizon."
            )

        if not recommendations:
            recommendations.append(
                f"All {len(aoi_forecasts)} AOIs are operating within budget and storage capacity thresholds over the {horizon}-day forecast horizon."
            )

        return SystemForecastSummary(
            horizon_days=horizon,
            generated_at=now,
            total_aois=len(aoi_forecasts),
            total_projected_passes=total_projected_passes,
            total_open_passes=total_open_passes,
            total_commercial_passes=total_commercial_passes,
            total_projected_cost_usd=total_projected_cost_usd,
            total_daily_cost_burn_usd=total_daily_cost_burn_usd,
            total_daily_storage_ingest_gb=total_daily_storage_ingest_gb,
            total_projected_storage_gb=total_projected_storage_gb,
            current_storage_used_gb=current_used_gb,
            storage_quota_gb=quota_gb,
            days_until_storage_quota=days_until_storage_quota,
            storage_status=storage_status,
            aoi_forecasts=aoi_forecasts,
            recommendations=recommendations,
        )
