"""Use case to evaluate and compare satellite providers before dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Optional, Sequence

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.metocean import MetoceanProvider
from sentinel_analysis.domain.acquisition_suitability import MetoceanCondition
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.provider_catalog import ProviderCatalog, ProviderComparisonEntry


def _calculate_is_daylight(lat: float, lon: float, dt: datetime) -> bool:
    """Determine if solar elevation is above horizon (> 0 degrees)."""
    day_of_year = dt.timetuple().tm_yday if hasattr(dt, "timetuple") else 180
    delta = 23.45 * math.sin(math.radians(360.0 / 365.25 * (day_of_year - 81)))
    utc_hours = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    solar_time = (utc_hours + lon / 15.0) % 24.0
    hour_angle = (solar_time - 12.0) * 15.0

    lat_rad = math.radians(lat)
    dec_rad = math.radians(delta)
    ha_rad = math.radians(hour_angle)

    sin_elev = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    return sin_elev > 0.0


class CompareSatelliteProviders:
    """Performs side-by-side trade-off comparison across Sentinel-1, Sentinel-2, Umbra, and other providers."""

    def __init__(
        self,
        metocean_provider: MetoceanProvider,
        aoi_repository: Optional[AreaOfInterestRepository] = None,
    ) -> None:
        self._metocean_provider = metocean_provider
        self._aoi_repository = aoi_repository

    def execute(
        self,
        aoi_id: Optional[int] = None,
        bbox: Optional[Sequence[float]] = None,
        target_vessel_size_m: float = 30.0,
        target_time: Optional[datetime] = None,
        is_daylight: Optional[bool] = None,
        filter_providers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        dt = target_time or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        target_bbox: Optional[BoundingBox] = None
        aoi_name = "Custom AOI"

        if aoi_id is not None and self._aoi_repository is not None:
            aoi = self._aoi_repository.get(aoi_id)
            if aoi is not None:
                target_bbox = aoi.bbox
                aoi_name = aoi.name
        elif bbox is not None and len(bbox) == 4:
            target_bbox = BoundingBox(bbox[0], bbox[1], bbox[2], bbox[3])

        if target_bbox is None:
            # Fallback to standard Singapore Strait coordinates
            target_bbox = BoundingBox(103.5, 1.1, 104.2, 1.6)

        center_lat = (target_bbox.min_latitude + target_bbox.max_latitude) / 2.0
        center_lon = (target_bbox.min_longitude + target_bbox.max_longitude) / 2.0

        if is_daylight is None:
            daylight_flag = _calculate_is_daylight(center_lat, center_lon, dt)
        else:
            daylight_flag = bool(is_daylight)

        metocean = self._metocean_provider.get_conditions(target_bbox, dt)

        comparison_entries: list[ProviderComparisonEntry] = ProviderCatalog.compare_providers(
            metocean=metocean,
            aoi_bbox=target_bbox,
            is_daylight=daylight_flag,
            target_vessel_size_m=target_vessel_size_m,
        )

        if filter_providers:
            allowed = {p.strip().lower() for p in filter_providers}
            comparison_entries = [e for e in comparison_entries if e.provider.provider_id.lower() in allowed]

        # Serialized entries
        serialized_entries = [e.to_dict() for e in comparison_entries]
        top_provider = comparison_entries[0].provider.provider_id if comparison_entries else "sentinel-1"

        summary = (
            f"Evaluated {len(comparison_entries)} providers for '{aoi_name}'. "
            f"Top match: {top_provider.upper()} ({comparison_entries[0].suitability_grade} "
            f"- {comparison_entries[0].estimated_suitability_score:.1f}/100). "
            f"Conditions: Wind {metocean.wind_speed_mps:.1f} m/s, Waves {metocean.wave_height_m:.1f}m, "
            f"Clouds {metocean.cloud_cover_pct:.0f}%, {'Daylight' if daylight_flag else 'Night'}."
        )

        return {
            "aoi_id": aoi_id,
            "aoi_name": aoi_name,
            "bbox": target_bbox.as_list(),
            "target_time": dt.isoformat(),
            "is_daylight": daylight_flag,
            "metocean": metocean.to_dict(),
            "target_vessel_size_m": target_vessel_size_m,
            "top_recommended_provider": top_provider,
            "summary": summary,
            "comparisons": serialized_entries,
        }
