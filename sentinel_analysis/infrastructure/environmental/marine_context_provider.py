"""Marine environmental context provider and nautical geospatial catalog.

Provides:
- Metocean layers (wind, waves, currents)
- Bathymetry depth models
- Spatial catalogs of shipping lanes (TSS), designated anchorages, and port boundaries
- Offshore infrastructure catalog (platforms, wind turbines) with SAR false-alarm suppression
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.environmental import (
    BathymetryPoint,
    InfrastructureType,
    MarineEnvironmentContext,
    MaritimeZoneType,
    NavigationalZone,
    OceanCurrentVector,
    OffshoreInfrastructure,
    WaveField,
    WindVector,
)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * (math.sin(dl / 2.0) ** 2)
    a = min(1.0, max(0.0, a))
    return r * (2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a)))


def _point_in_poly(lat: float, lon: float, poly: Sequence[tuple[float, float]]) -> bool:
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        lati, loni = poly[i]
        latj, lonj = poly[j]
        if (lati > lat) != (latj > lat):
            denom = latj - lati
            if denom != 0.0:
                x_int = loni + (lat - lati) * (lonj - loni) / denom
                if lon < x_int:
                    inside = not inside
        j = i
    return inside


# =============================================================================
# Nautical Catalog: Shipping Lanes, Anchorages, Port Boundaries
# =============================================================================

DEFAULT_NAVIGATIONAL_ZONES: list[NavigationalZone] = [
    # Shipping Lanes (TSS)
    NavigationalZone(
        zone_id="TSS_SINGAPORE_STRAIT",
        zone_type=MaritimeZoneType.SHIPPING_LANE,
        name="Singapore Strait TSS",
        polygon=(
            (1.05, 103.45),
            (1.32, 103.65),
            (1.38, 104.35),
            (1.15, 104.40),
            (1.08, 103.90),
            (1.00, 103.50),
        ),
        speed_limit_knots=12.0,
        description="High-density international shipping lane connecting Indian Ocean and South China Sea.",
    ),
    NavigationalZone(
        zone_id="TSS_DOVER_STRAIT",
        zone_type=MaritimeZoneType.SHIPPING_LANE,
        name="Dover Strait TSS",
        polygon=(
            (51.20, 1.30),
            (51.40, 1.85),
            (50.95, 1.70),
            (50.85, 1.20),
        ),
        speed_limit_knots=15.0,
        description="Mandatory IMO traffic separation scheme in the English Channel.",
    ),
    NavigationalZone(
        zone_id="TSS_GIBRALTAR",
        zone_type=MaritimeZoneType.SHIPPING_LANE,
        name="Strait of Gibraltar TSS",
        polygon=(
            (36.08, -5.95),
            (36.12, -5.35),
            (35.88, -5.30),
            (35.84, -5.90),
        ),
        description="Corridor connecting Mediterranean Sea and Atlantic Ocean.",
    ),
    # Anchorages
    NavigationalZone(
        zone_id="ANCH_SINGAPORE_EAST",
        zone_type=MaritimeZoneType.ANCHORAGE,
        name="Singapore Eastern Anchorage (AEW/AEP)",
        polygon=(
            (1.23, 103.88),
            (1.28, 103.95),
            (1.24, 104.01),
            (1.19, 103.94),
        ),
        description="Designated waiting and bunkering anchorage off Changi and East Coast.",
    ),
    NavigationalZone(
        zone_id="ANCH_SINGAPORE_WEST",
        zone_type=MaritimeZoneType.ANCHORAGE,
        name="Singapore Western Anchorage (AWB)",
        polygon=(
            (1.20, 103.65),
            (1.25, 103.73),
            (1.21, 103.75),
            (1.17, 103.68),
        ),
        description="Jurong Island and Tuas commercial anchorage.",
    ),
    NavigationalZone(
        zone_id="ANCH_ROTTERDAM_MAAS",
        zone_type=MaritimeZoneType.ANCHORAGE,
        name="Rotterdam Maascenter Anchorage 4 & 5",
        polygon=(
            (52.02, 3.80),
            (52.08, 4.00),
            (52.01, 4.05),
            (51.96, 3.85),
        ),
        description="North Sea approach anchorage for Port of Rotterdam.",
    ),
    # Port Boundaries
    NavigationalZone(
        zone_id="PORT_SINGAPORE",
        zone_type=MaritimeZoneType.PORT_BOUNDARY,
        name="Port of Singapore Authority Limit",
        polygon=(
            (1.15, 103.60),
            (1.32, 103.62),
            (1.33, 104.05),
            (1.18, 104.08),
        ),
        port_unlocode="SGSIN",
        description="Harbor and port jurisdiction of the Maritime and Port Authority of Singapore.",
    ),
    NavigationalZone(
        zone_id="PORT_ROTTERDAM",
        zone_type=MaritimeZoneType.PORT_BOUNDARY,
        name="Port of Rotterdam Limit",
        polygon=(
            (51.85, 4.00),
            (52.00, 4.05),
            (51.98, 4.50),
            (51.84, 4.45),
        ),
        port_unlocode="NLRTM",
        description="Major European hub port limit encompassing Europoort and Maasvlakte.",
    ),
]


# =============================================================================
# Catalog of Fixed Offshore Infrastructure (Platforms & Wind Farms)
# =============================================================================

DEFAULT_OFFSHORE_INFRASTRUCTURE: list[OffshoreInfrastructure] = [
    # North Sea Oil & Gas Platforms
    OffshoreInfrastructure(
        feature_id="INFRA_BRENT_BRAVO",
        feature_type=InfrastructureType.OIL_GAS_PLATFORM,
        name="Brent Bravo Production Platform",
        latitude=61.054,
        longitude=1.705,
        radius_meters=300.0,
        description="Fixed North Sea oil & gas gravity base platform.",
    ),
    OffshoreInfrastructure(
        feature_id="INFRA_EKOFISK_COMPLEX",
        feature_type=InfrastructureType.OIL_GAS_PLATFORM,
        name="Ekofisk 2/4 Production Hub",
        latitude=56.546,
        longitude=3.212,
        radius_meters=400.0,
        description="Norwegian continental shelf hub facility.",
    ),
    # Gulf of Mexico Deepwater Platforms
    OffshoreInfrastructure(
        feature_id="INFRA_THUNDER_HORSE",
        feature_type=InfrastructureType.OIL_GAS_PLATFORM,
        name="Thunder Horse Semi-Submersible Platform",
        latitude=28.191,
        longitude=-88.496,
        radius_meters=350.0,
        description="Deepwater Gulf of Mexico PDQ (Mississippi Canyon 778).",
    ),
    OffshoreInfrastructure(
        feature_id="INFRA_OLYMPUS_MARS",
        feature_type=InfrastructureType.OIL_GAS_PLATFORM,
        name="Olympus (Mars B) Tension-Leg Platform",
        latitude=28.163,
        longitude=-89.215,
        radius_meters=350.0,
        description="Deepwater tension-leg production facility in Gulf of Mexico.",
    ),
    # East Mediterranean
    OffshoreInfrastructure(
        feature_id="INFRA_LEVIATHAN",
        feature_type=InfrastructureType.OIL_GAS_PLATFORM,
        name="Leviathan Gas Production Platform",
        latitude=32.610,
        longitude=34.810,
        radius_meters=300.0,
        description="Fixed offshore gas processing platform in the Levant basin.",
    ),
    # Offshore Wind Turbines / Substations
    OffshoreInfrastructure(
        feature_id="INFRA_HORNS_REV_SUB",
        feature_type=InfrastructureType.OFFSHORE_WIND_TURBINE,
        name="Horns Rev Offshore Wind Substation Alpha",
        latitude=55.518,
        longitude=7.868,
        radius_meters=250.0,
        description="North Sea offshore wind high-voltage substation.",
    ),
    OffshoreInfrastructure(
        feature_id="INFRA_LONDON_ARRAY",
        feature_type=InfrastructureType.OFFSHORE_WIND_TURBINE,
        name="London Array Wind Hub",
        latitude=51.625,
        longitude=1.503,
        radius_meters=250.0,
        description="Outer Thames Estuary offshore wind turbine array.",
    ),
    # Singapore & Sunda Shelf Offshore Terminals / Rigs
    OffshoreInfrastructure(
        feature_id="INFRA_SINGAPORE_SBM",
        feature_type=InfrastructureType.BUOY_OR_NONTARGET_RIG,
        name="Singapore Single Buoy Mooring (SBM 1)",
        latitude=1.215,
        longitude=103.785,
        radius_meters=200.0,
        description="Offshore VLCC crude discharge buoy terminal.",
    ),
]


class MarineContextProvider:
    """Service providing metocean conditions, bathymetry, navigational zones, and infrastructure tagging."""

    def __init__(
        self,
        zones: Optional[list[NavigationalZone]] = None,
        infrastructure: Optional[list[OffshoreInfrastructure]] = None,
    ) -> None:
        self._zones = list(zones if zones is not None else DEFAULT_NAVIGATIONAL_ZONES)
        self._infrastructure = list(
            infrastructure if infrastructure is not None else DEFAULT_OFFSHORE_INFRASTRUCTURE
        )

    def get_context_for_bbox(
        self,
        bbox: BoundingBox,
        timestamp: Optional[datetime] = None,
    ) -> MarineEnvironmentContext:
        """Compute marine environmental context over an AOI bounding box."""
        ts = timestamp or datetime.now(timezone.utc)
        center_lat = (bbox.min_latitude + bbox.max_latitude) / 2.0
        center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0

        wind = self.estimate_wind(center_lat, center_lon, ts)
        waves = self.estimate_waves(wind.speed_mps, center_lat, center_lon)
        currents = self.estimate_currents(center_lat, center_lon, ts)
        bathymetry = self.estimate_bathymetry_summary(bbox)

        # Filter zones that intersect the bounding box
        active_zones = [
            z for z in self._zones
            if any(
                bbox.min_latitude <= p[0] <= bbox.max_latitude
                and bbox.min_longitude <= p[1] <= bbox.max_longitude
                for p in z.polygon
            )
            or _point_in_poly(center_lat, center_lon, z.polygon)
        ]

        # Filter infrastructure within or near bbox (margin 0.1 deg)
        nearby_infra = [
            inf for inf in self._infrastructure
            if (bbox.min_latitude - 0.1) <= inf.latitude <= (bbox.max_latitude + 0.1)
            and (bbox.min_longitude - 0.1) <= inf.longitude <= (bbox.max_longitude + 0.1)
        ]

        return MarineEnvironmentContext(
            wind=wind,
            waves=waves,
            currents=currents,
            bathymetry_summary=bathymetry,
            active_zones=active_zones,
            nearby_infrastructure=nearby_infra,
            timestamp=ts,
        )

    def estimate_wind(self, lat: float, lon: float, timestamp: datetime) -> WindVector:
        """Estimate representative wind vector (meteorological speed and direction)."""
        # Trade winds & Westerlies base model modulated by latitude and diurnal cycle
        hour = timestamp.hour if hasattr(timestamp, "hour") else 12
        diurnal = 1.0 + 0.15 * math.sin((hour / 24.0) * 2 * math.pi)

        # Latitude belt wind speed
        abs_lat = abs(lat)
        if abs_lat < 10.0:  # Doldrums / ITCZ
            base_mps = 4.5 * diurnal
            dir_deg = 60.0 if lat >= 0 else 120.0
        elif abs_lat < 30.0:  # Trade winds
            base_mps = 7.5 * diurnal
            dir_deg = 45.0 if lat >= 0 else 135.0
        elif abs_lat < 60.0:  # Roaring 40s / Westerlies
            base_mps = 11.0 * diurnal
            dir_deg = 260.0
        else:  # Polar easterlies
            base_mps = 8.0 * diurnal
            dir_deg = 80.0

        gust = base_mps * 1.35
        return WindVector(speed_mps=base_mps, direction_deg=dir_deg, gust_mps=gust)

    def estimate_waves(self, wind_speed_mps: float, lat: float, lon: float) -> WaveField:
        """Estimate significant wave height Hs and sea state using Pierson-Moskowitz empirical model."""
        # Hs approx = 0.0246 * U_10^2 for fully developed sea
        hs = min(15.0, max(0.2, 0.0246 * (wind_speed_mps ** 2)))
        period = max(3.0, 3.5 * math.sqrt(hs))
        # Wave direction aligns primarily with wind
        abs_lat = abs(lat)
        mean_dir = 260.0 if 30.0 <= abs_lat <= 60.0 else 65.0

        return WaveField(significant_wave_height_m=hs, peak_period_s=period, mean_direction_deg=mean_dir)

    def estimate_currents(self, lat: float, lon: float, timestamp: datetime) -> OceanCurrentVector:
        """Estimate ocean current speed and direction (e.g. boundary currents, tidal drift)."""
        abs_lat = abs(lat)
        if abs_lat < 15.0:
            speed = 0.65  # Strong equatorial / strait currents
            direction = 240.0
        elif 25.0 <= abs_lat <= 45.0:
            speed = 0.85  # Western boundary currents (Gulf Stream / Kuroshio)
            direction = 40.0
        else:
            speed = 0.35
            direction = 90.0

        return OceanCurrentVector(speed_mps=speed, direction_deg=direction)

    def estimate_bathymetry_at(self, lat: float, lon: float) -> BathymetryPoint:
        """Estimate bathymetric depth for a coordinate."""
        # Simple regional depth estimator:
        # Near Singapore/Malacca Strait: shallow (15-50m)
        # Dover / North Sea: 25-100m
        # Open deep ocean: 1000m - 4500m
        if 1.0 <= lat <= 1.5 and 103.5 <= lon <= 104.5:
            depth = 28.5
            is_channel = True
            shoal = False
        elif 50.5 <= lat <= 51.5 and 1.0 <= lon <= 2.0:
            depth = 34.0
            is_channel = True
            shoal = False
        elif 35.5 <= lat <= 36.5 and -6.5 <= lon <= -5.0:
            depth = 360.0
            is_channel = True
            shoal = False
        else:
            depth = 1250.0
            is_channel = False
            shoal = False

        return BathymetryPoint(depth_meters=depth, is_navigable_channel=is_channel, shoal_hazard=shoal)

    def estimate_bathymetry_summary(self, bbox: BoundingBox) -> dict[str, Any]:
        center_lat = (bbox.min_latitude + bbox.max_latitude) / 2.0
        center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0
        p = self.estimate_bathymetry_at(center_lat, center_lon)
        return {
            "mean_depth_meters": p.depth_meters,
            "min_depth_meters": max(5.0, p.depth_meters * 0.7),
            "max_depth_meters": p.depth_meters * 1.3,
            "navigable_corridor": p.is_navigable_channel,
            "shoal_hazard": p.shoal_hazard,
        }

    def tag_detections_with_environmental_context(
        self,
        detections: list[dict[str, Any]],
        bbox: Optional[BoundingBox] = None,
        timestamp: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """Enrich contact detections with environmental layers and suppress offshore platform false alarms."""
        ts = timestamp or datetime.now(timezone.utc)
        tagged = []

        for d in detections:
            d_copy = dict(d)
            lat = d_copy.get("latitude")
            lon = d_copy.get("longitude")

            if lat is None or lon is None:
                tagged.append(d_copy)
                continue

            lat_f, lon_f = float(lat), float(lon)

            # 1. Metocean & Bathymetry
            bathy = self.estimate_bathymetry_at(lat_f, lon_f)
            d_copy["bathymetry_depth_meters"] = bathy.depth_meters
            d_copy["is_navigable_channel"] = bathy.is_navigable_channel

            # 2. Zone intersections (Shipping Lanes, Anchorages, Port Boundaries)
            in_shipping = False
            lane_name = None
            in_anchorage = False
            anch_name = None
            in_port = False
            port_name = None

            for z in self._zones:
                if _point_in_poly(lat_f, lon_f, z.polygon):
                    if z.zone_type == MaritimeZoneType.SHIPPING_LANE:
                        in_shipping = True
                        lane_name = z.name
                    elif z.zone_type == MaritimeZoneType.ANCHORAGE:
                        in_anchorage = True
                        anch_name = z.name
                    elif z.zone_type == MaritimeZoneType.PORT_BOUNDARY:
                        in_port = True
                        port_name = z.name

            d_copy["is_in_shipping_lane"] = in_shipping
            d_copy["shipping_lane_name"] = lane_name
            d_copy["is_in_anchorage"] = in_anchorage
            d_copy["anchorage_name"] = anch_name
            d_copy["is_in_port"] = in_port
            d_copy["port_name"] = port_name

            # 3. Fixed Offshore Infrastructure Matching (False-Alarm Suppression)
            is_infra = False
            matched_infra = None
            for inf in self._infrastructure:
                dist_m = _haversine_m(lat_f, lon_f, inf.latitude, inf.longitude)
                if dist_m <= max(inf.radius_meters, 250.0):
                    is_infra = True
                    matched_infra = {
                        "feature_id": inf.feature_id,
                        "feature_type": inf.feature_type.value,
                        "name": inf.name,
                        "distance_meters": round(dist_m, 1),
                    }
                    break

            d_copy["is_offshore_infrastructure"] = is_infra
            d_copy["matched_infrastructure"] = matched_infra

            # If contact coincides with fixed offshore platform, adjust classification
            if is_infra:
                d_copy["detection_category"] = "OFFSHORE_INFRASTRUCTURE"
                d_copy["is_dark_vessel"] = False  # Fixed platform is not an evasive vessel

            tagged.append(d_copy)

        return tagged
