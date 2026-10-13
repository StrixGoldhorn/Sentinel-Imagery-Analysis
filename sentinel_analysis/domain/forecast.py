"""Domain models and value objects for acquisition cost and storage forecasting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sentinel_analysis.domain.exceptions import DomainValidationError


class BudgetHealthStatus(str, Enum):
    """Budget health categorization for an AOI collection policy."""

    SUFFICIENT = "SUFFICIENT"
    WARNING = "WARNING"
    EXHAUSTED = "EXHAUSTED"
    UNCONSTRAINED = "UNCONSTRAINED"


class StorageHealthStatus(str, Enum):
    """Storage capacity health status relative to system storage quota."""

    HEALTHY = "HEALTHY"
    WARNING_IMMINENT = "WARNING_IMMINENT"
    CRITICAL_EXCEEDED = "CRITICAL_EXCEEDED"


@dataclass(frozen=True)
class ProviderCostStorageProfile:
    """Benchmark costs and file sizes for a satellite provider."""

    provider_id: str
    name: str
    modality: str  # "SAR" or "OPTICAL"
    cost_per_scene_usd: float = 0.0
    avg_scene_size_gb: float = 1.0
    is_commercial: bool = False

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_id.strip():
            raise DomainValidationError("Provider ID must not be empty")
        if self.cost_per_scene_usd < 0:
            raise DomainValidationError("Cost per scene must be non-negative")
        if self.avg_scene_size_gb <= 0:
            raise DomainValidationError("Average scene size must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "modality": self.modality,
            "cost_per_scene_usd": self.cost_per_scene_usd,
            "avg_scene_size_gb": self.avg_scene_size_gb,
            "is_commercial": self.is_commercial,
        }


# Industry benchmark profiles for Earth Observation data streams
DEFAULT_PROVIDER_PROFILES: dict[str, ProviderCostStorageProfile] = {
    "copernicus": ProviderCostStorageProfile(
        provider_id="copernicus",
        name="Copernicus Sentinel-1 SAR",
        modality="SAR",
        cost_per_scene_usd=0.0,
        avg_scene_size_gb=1.4,
        is_commercial=False,
    ),
    "asf": ProviderCostStorageProfile(
        provider_id="asf",
        name="ASF DAAC Sentinel-1 SAR",
        modality="SAR",
        cost_per_scene_usd=0.0,
        avg_scene_size_gb=1.4,
        is_commercial=False,
    ),
    "sentinel-2": ProviderCostStorageProfile(
        provider_id="sentinel-2",
        name="Copernicus Sentinel-2 Optical",
        modality="OPTICAL",
        cost_per_scene_usd=0.0,
        avg_scene_size_gb=0.8,
        is_commercial=False,
    ),
    "umbra": ProviderCostStorageProfile(
        provider_id="umbra",
        name="Umbra High-Res Commercial SAR",
        modality="SAR",
        cost_per_scene_usd=350.0,
        avg_scene_size_gb=0.45,
        is_commercial=True,
    ),
    "planet": ProviderCostStorageProfile(
        provider_id="planet",
        name="PlanetScope Commercial Optical",
        modality="OPTICAL",
        cost_per_scene_usd=95.0,
        avg_scene_size_gb=0.35,
        is_commercial=True,
    ),
    "capella": ProviderCostStorageProfile(
        provider_id="capella",
        name="Capella Space Commercial SAR",
        modality="SAR",
        cost_per_scene_usd=450.0,
        avg_scene_size_gb=0.55,
        is_commercial=True,
    ),
}


@dataclass(frozen=True)
class AoiAcquisitionForecast:
    """Projected satellite tasking passes, commercial cost, and storage footprint for an AOI."""

    aoi_id: int
    aoi_name: str
    priority: str
    horizon_days: int
    desired_revisit_rate_hours: float
    total_projected_passes: int
    open_access_passes: int
    commercial_passes: int
    projected_cost_usd: float
    daily_cost_burn_usd: float
    daily_storage_ingest_gb: float
    projected_storage_gb: float
    steady_state_storage_gb: float
    retention_policy_days: Optional[int]
    remaining_budget_usd: float
    days_until_budget_exhaustion: Optional[float]
    budget_status: BudgetHealthStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "aoi_id": self.aoi_id,
            "aoi_name": self.aoi_name,
            "priority": self.priority,
            "horizon_days": self.horizon_days,
            "desired_revisit_rate_hours": round(self.desired_revisit_rate_hours, 1),
            "total_projected_passes": self.total_projected_passes,
            "open_access_passes": self.open_access_passes,
            "commercial_passes": self.commercial_passes,
            "projected_cost_usd": round(self.projected_cost_usd, 2),
            "daily_cost_burn_usd": round(self.daily_cost_burn_usd, 2),
            "daily_storage_ingest_gb": round(self.daily_storage_ingest_gb, 3),
            "projected_storage_gb": round(self.projected_storage_gb, 2),
            "steady_state_storage_gb": round(self.steady_state_storage_gb, 2),
            "retention_policy_days": self.retention_policy_days,
            "remaining_budget_usd": round(self.remaining_budget_usd, 2),
            "days_until_budget_exhaustion": (
                round(self.days_until_budget_exhaustion, 1)
                if self.days_until_budget_exhaustion is not None
                else None
            ),
            "budget_status": self.budget_status.value,
        }


@dataclass(frozen=True)
class SystemForecastSummary:
    """System-wide satellite collection expenditure, storage volume, and runway forecast."""

    horizon_days: int
    generated_at: datetime
    total_aois: int
    total_projected_passes: int
    total_open_passes: int
    total_commercial_passes: int
    total_projected_cost_usd: float
    total_daily_cost_burn_usd: float
    total_daily_storage_ingest_gb: float
    total_projected_storage_gb: float
    current_storage_used_gb: float
    storage_quota_gb: float
    days_until_storage_quota: Optional[float]
    storage_status: StorageHealthStatus
    aoi_forecasts: list[AoiAcquisitionForecast] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_days": self.horizon_days,
            "generated_at": self.generated_at.isoformat(),
            "total_aois": self.total_aois,
            "total_projected_passes": self.total_projected_passes,
            "total_open_passes": self.total_open_passes,
            "total_commercial_passes": self.total_commercial_passes,
            "total_projected_cost_usd": round(self.total_projected_cost_usd, 2),
            "total_daily_cost_burn_usd": round(self.total_daily_cost_burn_usd, 2),
            "total_daily_storage_ingest_gb": round(self.total_daily_storage_ingest_gb, 3),
            "total_projected_storage_gb": round(self.total_projected_storage_gb, 2),
            "current_storage_used_gb": round(self.current_storage_used_gb, 2),
            "storage_quota_gb": round(self.storage_quota_gb, 2),
            "days_until_storage_quota": (
                round(self.days_until_storage_quota, 1)
                if self.days_until_storage_quota is not None
                else None
            ),
            "storage_status": self.storage_status.value,
            "aoi_forecasts": [f.to_dict() for f in self.aoi_forecasts],
            "recommendations": self.recommendations,
        }
