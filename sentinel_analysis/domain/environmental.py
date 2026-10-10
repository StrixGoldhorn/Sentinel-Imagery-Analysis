"""Domain models for Marine Environmental Context and Offshore Infrastructure.

Covers:
- Wind vector (speed, direction, gusts)
- Significant wave height (Hs), peak period, sea state
- Ocean currents (speed, direction)
- Bathymetry (depth, navigation channel, shoal hazards)
- Navigational zones: Shipping lanes (TSS), Designated anchorages, Port boundaries
- Offshore infrastructure: Oil/gas platforms, offshore wind turbines, subsea infrastructure
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence

from sentinel_analysis.domain.exceptions import DomainValidationError


class InfrastructureType(str, Enum):
    OIL_GAS_PLATFORM = "OIL_GAS_PLATFORM"
    OFFSHORE_WIND_TURBINE = "OFFSHORE_WIND_TURBINE"
    SUBSEA_PIPELINE = "SUBSEA_PIPELINE"
    BUOY_OR_NONTARGET_RIG = "BUOY_OR_NONTARGET_RIG"
    AQUACULTURE_FARM = "AQUACULTURE_FARM"


class MaritimeZoneType(str, Enum):
    SHIPPING_LANE = "SHIPPING_LANE"
    ANCHORAGE = "ANCHORAGE"
    PORT_BOUNDARY = "PORT_BOUNDARY"
    RESTRICTED_AREA = "RESTRICTED_AREA"


@dataclass(frozen=True)
class WindVector:
    speed_mps: float
    direction_deg: float  # Meteorological direction from which wind blows (0=N, 90=E)
    gust_mps: Optional[float] = None

    @property
    def speed_knots(self) -> float:
        return round(self.speed_mps * 1.94384, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "speed_mps": round(self.speed_mps, 1),
            "speed_knots": self.speed_knots,
            "direction_deg": round(self.direction_deg, 1),
            "gust_mps": round(self.gust_mps, 1) if self.gust_mps is not None else None,
        }


@dataclass(frozen=True)
class WaveField:
    significant_wave_height_m: float  # Hs in meters
    peak_period_s: float  # seconds
    mean_direction_deg: float  # degrees

    @property
    def sea_state_beaufort(self) -> int:
        hs = self.significant_wave_height_m
        if hs < 0.1:
            return 0
        if hs < 0.2:
            return 1
        if hs < 0.6:
            return 2
        if hs < 1.0:
            return 3
        if hs < 2.0:
            return 4
        if hs < 3.0:
            return 5
        if hs < 4.0:
            return 6
        if hs < 5.5:
            return 7
        if hs < 7.5:
            return 8
        if hs < 10.0:
            return 9
        if hs < 12.5:
            return 10
        if hs < 16.0:
            return 11
        return 12

    @property
    def sea_state_code(self) -> str:
        hs = self.significant_wave_height_m
        if hs < 0.1:
            return "CALM_GLASSY"
        if hs < 0.5:
            return "CALM_RIPPLED"
        if hs < 1.25:
            return "SMOOTH"
        if hs < 2.5:
            return "SLIGHT_TO_MODERATE"
        if hs < 4.0:
            return "ROUGH"
        if hs < 6.0:
            return "VERY_ROUGH"
        if hs < 9.0:
            return "HIGH"
        if hs < 14.0:
            return "VERY_HIGH"
        return "PHENOMENAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "significant_wave_height_m": round(self.significant_wave_height_m, 2),
            "peak_period_s": round(self.peak_period_s, 1),
            "mean_direction_deg": round(self.mean_direction_deg, 1),
            "beaufort_scale": self.sea_state_beaufort,
            "sea_state_code": self.sea_state_code,
        }


@dataclass(frozen=True)
class OceanCurrentVector:
    speed_mps: float
    direction_deg: float  # Oceanographic direction toward which current flows

    @property
    def speed_knots(self) -> float:
        return round(self.speed_mps * 1.94384, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "speed_mps": round(self.speed_mps, 2),
            "speed_knots": self.speed_knots,
            "direction_deg": round(self.direction_deg, 1),
        }


@dataclass(frozen=True)
class BathymetryPoint:
    depth_meters: float  # positive below sea level
    is_navigable_channel: bool = True
    shoal_hazard: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth_meters": round(self.depth_meters, 1),
            "is_navigable_channel": self.is_navigable_channel,
            "shoal_hazard": self.shoal_hazard,
        }


@dataclass(frozen=True)
class OffshoreInfrastructure:
    feature_id: str
    feature_type: InfrastructureType
    name: str
    latitude: float
    longitude: float
    radius_meters: float = 150.0
    status: str = "ACTIVE"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type.value,
            "name": self.name,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "radius_meters": self.radius_meters,
            "status": self.status,
            "description": self.description,
        }


@dataclass(frozen=True)
class NavigationalZone:
    zone_id: str
    zone_type: MaritimeZoneType
    name: str
    polygon: tuple[tuple[float, float], ...]  # (lat, lon) coordinates
    speed_limit_knots: Optional[float] = None
    port_unlocode: Optional[str] = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "zone_type": self.zone_type.value,
            "name": self.name,
            "polygon": list(self.polygon),
            "speed_limit_knots": self.speed_limit_knots,
            "port_unlocode": self.port_unlocode,
            "description": self.description,
        }


@dataclass(frozen=True)
class MarineEnvironmentContext:
    wind: WindVector
    waves: WaveField
    currents: OceanCurrentVector
    bathymetry_summary: dict[str, Any]
    active_zones: list[NavigationalZone] = field(default_factory=list)
    nearby_infrastructure: list[OffshoreInfrastructure] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat() if hasattr(self.timestamp, "isoformat") else str(self.timestamp),
            "wind": self.wind.to_dict(),
            "waves": self.waves.to_dict(),
            "currents": self.currents.to_dict(),
            "bathymetry": self.bathymetry_summary,
            "zones_count": len(self.active_zones),
            "infrastructure_count": len(self.nearby_infrastructure),
            "zones": [z.to_dict() for z in self.active_zones],
            "infrastructure": [inf.to_dict() for inf in self.nearby_infrastructure],
        }
