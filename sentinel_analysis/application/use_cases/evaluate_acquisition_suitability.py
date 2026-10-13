"""Use case to evaluate acquisition suitability for a satellite pass opportunity."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Optional

from sentinel_analysis.application.ports.metocean import MetoceanProvider
from sentinel_analysis.domain.acquisition_suitability import (
    MetoceanCondition,
    ModalityType,
    SuitabilityAssessment,
    score_acquisition_opportunity,
)
from sentinel_analysis.domain.entities import BoundingBox


def _calculate_is_daylight(lat: float, lon: float, dt: datetime) -> bool:
    """Determine if solar elevation is above astronomical twilight (> 0 degrees)."""
    day_of_year = dt.timetuple().tm_yday if hasattr(dt, "timetuple") else 180
    # Solar declination delta
    delta = 23.45 * math.sin(math.radians(360.0 / 365.25 * (day_of_year - 81)))
    # Equation of time / hour angle
    utc_hours = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    solar_time = (utc_hours + lon / 15.0) % 24.0
    hour_angle = (solar_time - 12.0) * 15.0

    lat_rad = math.radians(lat)
    dec_rad = math.radians(delta)
    ha_rad = math.radians(hour_angle)

    sin_elev = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    return sin_elev > 0.0


class EvaluateAcquisitionSuitability:
    """Evaluates multi-factor acquisition suitability using physical models and metocean forecasts."""

    def __init__(self, metocean_provider: MetoceanProvider) -> None:
        self._metocean_provider = metocean_provider

    def execute(
        self,
        bbox: BoundingBox,
        resolution_m: float,
        aoi_coverage_pct: float,
        incidence_angle_deg: float,
        orbit_direction: str,
        modality: ModalityType | str = ModalityType.SAR,
        pass_time: Optional[datetime] = None,
        is_daylight: Optional[bool] = None,
        custom_metocean: Optional[MetoceanCondition] = None,
    ) -> SuitabilityAssessment:
        dt = pass_time or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        mod = modality if isinstance(modality, ModalityType) else ModalityType(str(modality).upper())

        # Obtain metocean conditions
        if custom_metocean is not None:
            metocean = custom_metocean
        else:
            metocean = self._metocean_provider.get_conditions(bbox, dt)

        # Determine solar elevation if not specified
        if is_daylight is None:
            center_lat = (bbox.min_latitude + bbox.max_latitude) / 2.0
            center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0
            daylight_flag = _calculate_is_daylight(center_lat, center_lon, dt)
        else:
            daylight_flag = bool(is_daylight)

        return score_acquisition_opportunity(
            resolution_m=resolution_m,
            aoi_coverage_pct=aoi_coverage_pct,
            incidence_angle_deg=incidence_angle_deg,
            orbit_direction=orbit_direction,
            modality=mod,
            metocean=metocean,
            is_daylight=daylight_flag,
            aoi_bbox=bbox,
        )
