"""Domain models and algorithms for AOI historical imagery backfill and baseline modeling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Optional

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.exceptions import DomainValidationError


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two geographic coordinates in meters (WGS-84)."""
    r = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


@dataclass(frozen=True)
class PersistentScatterer:
    """A stationary maritime scatterer (e.g., rig, buoy, turbine, platform) observed across multiple passes."""

    id: str
    latitude: float
    longitude: float
    persistence_pct: float
    observation_count: int
    total_passes: int
    category: str = "INFRASTRUCTURE"  # INFRASTRUCTURE, OIL_RIG, WIND_TURBINE, BUOY, PIER, ANCHORED
    average_rcs_db: Optional[float] = None
    average_length_m: Optional[float] = None
    average_beam_m: Optional[float] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (-90.0 <= self.latitude <= 90.0):
            raise DomainValidationError(f"Invalid latitude: {self.latitude}")
        if not (-180.0 <= self.longitude <= 180.0):
            raise DomainValidationError(f"Invalid longitude: {self.longitude}")
        if not (0.0 <= self.persistence_pct <= 1.0):
            raise DomainValidationError(f"Persistence percentage must be between 0 and 1: {self.persistence_pct}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "persistence_pct": round(self.persistence_pct, 4),
            "observation_count": self.observation_count,
            "total_passes": self.total_passes,
            "category": self.category,
            "average_rcs_db": round(self.average_rcs_db, 2) if self.average_rcs_db is not None else None,
            "average_length_m": round(self.average_length_m, 1) if self.average_length_m is not None else None,
            "average_beam_m": round(self.average_beam_m, 1) if self.average_beam_m is not None else None,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ClutterBaseline:
    """Statistical sea clutter backscatter baseline for SAR CFAR adaptation over the AOI."""

    mean_clutter_db: float
    std_clutter_db: float
    p95_clutter_db: float
    recommended_threshold_db: float
    sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_clutter_db": round(self.mean_clutter_db, 2),
            "std_clutter_db": round(self.std_clutter_db, 2),
            "p95_clutter_db": round(self.p95_clutter_db, 2),
            "recommended_threshold_db": round(self.recommended_threshold_db, 2),
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True)
class AOIBaseline:
    """Comprehensive historical baseline model built from multi-temporal satellite passes over an AOI."""

    aoi_id: int
    aoi_name: str
    bbox: BoundingBox
    created_at: datetime
    start_date: datetime
    end_date: datetime
    total_passes: int
    persistent_scatterers: tuple[PersistentScatterer, ...]
    clutter_baseline: ClutterBaseline
    vessel_density_summary: dict[str, Any]
    historical_pass_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "aoi_id": self.aoi_id,
            "aoi_name": self.aoi_name,
            "bbox": [self.bbox.min_longitude, self.bbox.min_latitude, self.bbox.max_longitude, self.bbox.max_latitude],
            "created_at": self.created_at.isoformat(),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_passes": self.total_passes,
            "persistent_scatterers_count": len(self.persistent_scatterers),
            "persistent_scatterers": [ps.to_dict() for ps in self.persistent_scatterers],
            "clutter_baseline": self.clutter_baseline.to_dict(),
            "vessel_density_summary": self.vessel_density_summary,
            "historical_pass_ids": list(self.historical_pass_ids),
        }
