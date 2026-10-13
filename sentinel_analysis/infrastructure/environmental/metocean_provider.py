"""Metocean condition forecasting infrastructure adapter.

Fetches marine weather, sea-state, and optical cloud forecasts via Open-Meteo APIs
with an in-memory TTL cache and robust deterministic geophysical model fallback
when offline, air-gapped, or network-constrained.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
from typing import Any, Optional

import requests

from sentinel_analysis.application.ports.metocean import MetoceanProvider
from sentinel_analysis.domain.acquisition_suitability import MetoceanCondition
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.environmental.marine_context_provider import MarineContextProvider

logger = logging.getLogger(__name__)


def _estimate_geophysical_cloud_cover(lat: float, lon: float, dt: datetime) -> float:
    """Deterministic climatological cloud cover estimate based on latitude and season.

    Models:
    - Intertropical Convergence Zone (ITCZ, ~0-10 deg): 45-70% cloud cover.
    - Subtropical highs / Horse latitudes (~15-30 deg): 15-30% cloud cover.
    - Temperate mid-latitude storm tracks (~40-60 deg): 50-70% cloud cover.
    - Polar regions (>60 deg): 60-80% cloud cover.
    """
    abs_lat = abs(lat)
    day_of_year = dt.timetuple().tm_yday if hasattr(dt, "timetuple") else 180

    # Base cloud cover by latitude belt
    if abs_lat < 10.0:
        base = 55.0 + 10.0 * math.sin((lat + 5.0) / 10.0 * math.pi)
    elif abs_lat < 30.0:
        base = 22.0 + 8.0 * math.cos(math.radians(lat * 2.0))
    elif abs_lat < 60.0:
        base = 52.0 + 10.0 * math.sin(math.radians((abs_lat - 30.0) / 30.0 * 180.0))
    else:
        base = 65.0 + 10.0 * math.cos(math.radians(lat))

    # Seasonal modulation (summer vs winter storm tracks)
    seasonal = 8.0 * math.sin((day_of_year / 365.25) * 2.0 * math.pi)
    if lat < 0:
        seasonal = -seasonal

    # Local longitudinal micro-variation (pseudo-stochastic but strictly deterministic)
    local_var = 5.0 * math.sin(math.radians(lon * 3.0 + lat * 2.0))

    total = base + seasonal + local_var
    return max(5.0, min(95.0, total))


def _estimate_geophysical_sst(lat: float, dt: datetime) -> float:
    """Deterministic sea surface temperature estimate based on latitude and season."""
    abs_lat = abs(lat)
    day_of_year = dt.timetuple().tm_yday if hasattr(dt, "timetuple") else 180

    if abs_lat < 10.0:
        base_sst = 28.5
    elif abs_lat < 30.0:
        base_sst = 24.0 - (abs_lat - 10.0) * 0.4
    elif abs_lat < 60.0:
        base_sst = 16.0 - (abs_lat - 30.0) * 0.35
    else:
        base_sst = max(-1.5, 5.0 - (abs_lat - 60.0) * 0.3)

    seasonal_amp = 3.5 if abs_lat > 15.0 else 1.0
    season_shift = seasonal_amp * math.cos((day_of_year - 200) / 365.25 * 2.0 * math.pi)
    if lat < 0:
        season_shift = -season_shift

    return round(max(-1.8, min(33.0, base_sst + season_shift)), 1)


class OpenMeteoMetoceanProvider(MetoceanProvider):
    """Metocean provider that combines Open-Meteo marine/weather forecasts with physical fallback."""

    def __init__(
        self,
        http_client: Any = None,
        timeout_seconds: float = 2.0,
        cache_ttl_seconds: int = 3600,
        marine_context: Optional[MarineContextProvider] = None,
    ) -> None:
        self._http = http_client or requests
        self._timeout = timeout_seconds
        self._cache_ttl = cache_ttl_seconds
        self._marine_context = marine_context or MarineContextProvider()
        self._cache: dict[tuple[float, float, str], tuple[float, MetoceanCondition]] = {}

    def get_conditions(
        self,
        bbox: BoundingBox,
        target_time: Optional[datetime] = None,
    ) -> MetoceanCondition:
        """Retrieve metocean conditions (wind, waves, cloud cover, SST) for a bounding box and time."""
        dt = target_time or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        center_lat = round((bbox.min_latitude + bbox.max_latitude) / 2.0, 2)
        center_lon = round((bbox.min_longitude + bbox.max_longitude) / 2.0, 2)
        hour_key = dt.strftime("%Y-%m-%d-%H")
        cache_key = (center_lat, center_lon, hour_key)

        now_epoch = datetime.now(timezone.utc).timestamp()
        if cache_key in self._cache:
            cached_time, condition = self._cache[cache_key]
            if now_epoch - cached_time < self._cache_ttl:
                return condition

        # Attempt remote query
        condition = self._fetch_remote(center_lat, center_lon, dt)
        if condition is None:
            # Deterministic geophysical fallback
            condition = self._compute_geophysical_fallback(center_lat, center_lon, dt)

        self._cache[cache_key] = (now_epoch, condition)
        return condition

    def _fetch_remote(
        self,
        lat: float,
        lon: float,
        dt: datetime,
    ) -> Optional[MetoceanCondition]:
        """Query Open-Meteo Marine and Weather forecast APIs."""
        try:
            # Weather API (wind speed 10m, wind direction 10m, total cloud cover)
            weather_url = "https://api.open-meteo.com/v1/forecast"
            weather_params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "wind_speed_10m,wind_direction_10m,cloud_cover",
                "wind_speed_unit": "ms",
                "timezone": "UTC",
            }
            resp = self._http.get(weather_url, params=weather_params, timeout=self._timeout)
            if resp.status_code != 200:
                return None
            weather_data = resp.json()

            # Marine API (wave height, wave period)
            marine_url = "https://marine-api.open-meteo.com/v1/marine"
            marine_params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "wave_height,wave_period",
                "timezone": "UTC",
            }
            marine_resp = self._http.get(marine_url, params=marine_params, timeout=self._timeout)
            marine_data = marine_resp.json() if marine_resp.status_code == 200 else {}

            # Match target hour
            target_iso_hour = dt.strftime("%Y-%m-%dT%H:00")
            hourly_w = weather_data.get("hourly", {})
            times = hourly_w.get("time", [])

            idx = 0
            if target_iso_hour in times:
                idx = times.index(target_iso_hour)
            elif times:
                idx = min(len(times) - 1, max(0, dt.hour))

            wind_speeds = hourly_w.get("wind_speed_10m", [])
            wind_dirs = hourly_w.get("wind_direction_10m", [])
            clouds = hourly_w.get("cloud_cover", [])

            wind_speed = float(wind_speeds[idx]) if idx < len(wind_speeds) and wind_speeds[idx] is not None else 6.0
            wind_dir = float(wind_dirs[idx]) if idx < len(wind_dirs) and wind_dirs[idx] is not None else 90.0
            cloud_pct = float(clouds[idx]) if idx < len(clouds) and clouds[idx] is not None else 20.0

            # Waves from marine API
            hourly_m = marine_data.get("hourly", {})
            wave_heights = hourly_m.get("wave_height", [])
            wave_periods = hourly_m.get("wave_period", [])

            if idx < len(wave_heights) and wave_heights[idx] is not None:
                wave_h = float(wave_heights[idx])
            else:
                # Pierson-Moskowitz wave estimation from wind speed
                wave_h = min(15.0, max(0.2, 0.0246 * (wind_speed**2)))

            if idx < len(wave_periods) and wave_periods[idx] is not None:
                wave_p = float(wave_periods[idx])
            else:
                wave_p = max(3.0, 3.5 * math.sqrt(wave_h))

            sst = _estimate_geophysical_sst(lat, dt)

            return MetoceanCondition(
                wind_speed_mps=wind_speed,
                wind_direction_deg=wind_dir,
                wave_height_m=wave_h,
                wave_period_s=wave_p,
                cloud_cover_pct=cloud_pct,
                sea_surface_temp_c=sst,
            )
        except Exception as exc:
            logger.debug("Open-Meteo forecast query failed, falling back to geophysical model: %s", exc)
            return None

    def _compute_geophysical_fallback(
        self,
        lat: float,
        lon: float,
        dt: datetime,
    ) -> MetoceanCondition:
        """Physical model fallback when network API is unreachable."""
        wind = self._marine_context.estimate_wind(lat, lon, dt)
        waves = self._marine_context.estimate_waves(wind.speed_mps, lat, lon)
        cloud_pct = _estimate_geophysical_cloud_cover(lat, lon, dt)
        sst = _estimate_geophysical_sst(lat, dt)

        return MetoceanCondition(
            wind_speed_mps=wind.speed_mps,
            wind_direction_deg=wind.direction_deg,
            wave_height_m=waves.significant_wave_height_m,
            wave_period_s=waves.peak_period_s,
            cloud_cover_pct=round(cloud_pct, 1),
            sea_surface_temp_c=sst,
        )
