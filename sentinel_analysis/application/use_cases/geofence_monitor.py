"""Use case and spatial evaluation engine for EEZ and Marine Protected Area (MPA) geofencing."""

from __future__ import annotations

import logging
import math
from typing import Any, Optional, Sequence

from sentinel_analysis.domain.entities import BoundingBox, GeofenceBreach, GeofenceZone

logger = logging.getLogger(__name__)


def point_in_polygon(lat: float, lon: float, polygon: Sequence[tuple[float, float]]) -> bool:
    """Ray-casting algorithm to determine if a WGS-84 coordinate (lat, lon) lies inside a polygon.

    The polygon is specified as a sequence of (lat, lon) vertices.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]

        # Check if the horizontal ray crosses the edge
        if ((lat_i > lat) != (lat_j > lat)):
            denom = lat_j - lat_i
            if denom != 0.0:
                x_intersect = lon_i + (lat - lat_i) * (lon_j - lon_i) / denom
                if lon < x_intersect:
                    inside = not inside
        j = i

    return inside


def polygon_bounding_box(polygon: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return (min_lat, min_lon, max_lat, max_lon) for quick rejection."""
    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]
    return min(lats), min(lons), max(lats), max(lons)


# =============================================================================
# Pre-Configured High-Value Operational Maritime Zones
# =============================================================================
DEFAULT_GEOFENCE_ZONES: list[GeofenceZone] = [
    # 1. Pelagos Sanctuary for Mediterranean Marine Mammals (Liguria / Monaco / Corsica)
    GeofenceZone(
        zone_id="MPA_PELAGOS_SANCTUARY",
        name="Pelagos Marine Mammal Sanctuary",
        zone_type="MPA",
        polygon=(
            (43.90, 7.20),
            (44.40, 9.00),
            (43.80, 10.30),
            (41.40, 9.80),
            (41.20, 8.20),
            (43.00, 6.00),
            (43.70, 7.00),
        ),
        description="Specially Protected Area of Mediterranean Importance (SPAMI) with strict ecological protections.",
        restrictions=("NO_DARK_VESSEL", "NO_FISHING", "MANDATORY_AIS"),
    ),
    # 2. Strait of Gibraltar Traffic Separation Scheme (TSS)
    GeofenceZone(
        zone_id="TSS_STRAIT_OF_GIBRALTAR",
        name="Strait of Gibraltar TSS",
        zone_type="TRAFFIC_SEPARATION_SCHEME",
        polygon=(
            (36.05, -5.95),
            (36.10, -5.35),
            (35.90, -5.30),
            (35.85, -5.90),
        ),
        description="Mandatory international maritime routeing corridor under IMO Rule 10.",
        restrictions=("NO_DARK_VESSEL", "NO_ANCHORING", "MANDATORY_AIS"),
    ),
    # 3. Singapore Strait Traffic Separation Scheme
    GeofenceZone(
        zone_id="TSS_SINGAPORE_STRAIT",
        name="Singapore Strait TSS",
        zone_type="TRAFFIC_SEPARATION_SCHEME",
        polygon=(
            (1.10, 103.50),
            (1.25, 103.95),
            (1.35, 104.30),
            (1.20, 104.35),
            (1.15, 103.90),
            (1.05, 103.55),
        ),
        description="High-density navigational corridor connecting Malacca Strait and South China Sea.",
        restrictions=("NO_DARK_VESSEL", "NO_ANCHORING", "MANDATORY_AIS"),
    ),
    # 4. Dover Strait Traffic Separation Scheme (English Channel)
    GeofenceZone(
        zone_id="TSS_DOVER_STRAIT",
        name="Dover Strait TSS",
        zone_type="TRAFFIC_SEPARATION_SCHEME",
        polygon=(
            (51.20, 1.30),
            (51.35, 1.80),
            (50.95, 1.70),
            (50.85, 1.20),
        ),
        description="IMO Traffic Separation Scheme through the Dover Strait.",
        restrictions=("NO_DARK_VESSEL", "NO_ANCHORING", "MANDATORY_AIS"),
    ),
    # 5. Galapagos Marine Reserve (Pacific)
    GeofenceZone(
        zone_id="MPA_GALAPAGOS_RESERVE",
        name="Galapagos Marine Reserve",
        zone_type="MPA",
        polygon=(
            (1.80, -92.00),
            (1.80, -89.00),
            (-1.50, -89.00),
            (-1.50, -92.00),
        ),
        description="UNESCO World Heritage marine sanctuary with complete industrial fishing ban.",
        restrictions=("NO_DARK_VESSEL", "NO_FISHING", "MANDATORY_AIS"),
    ),
]


class GeofenceMonitor:
    """Evaluates vessel detections against Marine Protected Areas (MPAs), EEZs, and TSS zones."""

    def __init__(self, custom_zones: Optional[Sequence[GeofenceZone]] = None) -> None:
        self._zones: list[GeofenceZone] = list(DEFAULT_GEOFENCE_ZONES)
        if custom_zones:
            for z in custom_zones:
                self.register_zone(z)
        self._bbox_cache: dict[str, tuple[float, float, float, float]] = {}
        self._refresh_bbox_cache()

    def _refresh_bbox_cache(self) -> None:
        self._bbox_cache = {z.zone_id: polygon_bounding_box(z.polygon) for z in self._zones}

    def register_zone(self, zone: GeofenceZone) -> None:
        """Add or update a geofence zone."""
        # Replace if existing
        self._zones = [z for z in self._zones if z.zone_id != zone.zone_id]
        self._zones.append(zone)
        self._bbox_cache[zone.zone_id] = polygon_bounding_box(zone.polygon)

    def list_zones(self) -> list[GeofenceZone]:
        """Return all active geofence zones."""
        return list(self._zones)

    def zones_for_bbox(self, bbox: BoundingBox) -> list[GeofenceZone]:
        """Return zones that potentially intersect the given bounding box."""
        intersecting = []
        for z in self._zones:
            min_lat, min_lon, max_lat, max_lon = self._bbox_cache[z.zone_id]
            if (
                min_lat <= bbox.max_latitude
                and max_lat >= bbox.min_latitude
                and min_lon <= bbox.max_longitude
                and max_lon >= bbox.min_longitude
            ):
                intersecting.append(z)
        return intersecting

    def evaluate_target(
        self,
        target: dict[str, Any],
        vessel_idx: int = 0,
        candidate_zones: Optional[Sequence[GeofenceZone]] = None,
    ) -> list[GeofenceBreach]:
        """Evaluate a single target against candidate or all geofence zones."""
        lat = target.get("latitude")
        if lat is None:
            lat = target.get("lat")

        lon = target.get("longitude")
        if lon is None:
            lon = target.get("lon")
        if lon is None:
            lon = target.get("lng")

        if lat is None or lon is None:
            return []

        try:
            t_lat = float(lat)
            t_lon = float(lon)
        except (TypeError, ValueError):
            return []

        zones_to_test = candidate_zones if candidate_zones is not None else self._zones
        is_dark = bool(target.get("is_dark_vessel", not target.get("is_correlated", False)))
        if "is_dark" in target:
            is_dark = bool(target["is_dark"])

        v_class = str(target.get("vessel_class") or target.get("vessel_type") or "").strip()
        speed_raw = target.get("speed_knots")
        if speed_raw is None:
            speed_raw = target.get("speed")
        try:
            speed = float(speed_raw) if speed_raw is not None else 0.0
        except (TypeError, ValueError):
            speed = 0.0
        v_ident = (
            target.get("vessel_name")
            or (f"MMSI:{target.get('mmsi')}" if target.get("mmsi") else None)
            or (f"DARK_VESSEL_#{vessel_idx}" if is_dark else f"TARGET_#{vessel_idx}")
        )

        breaches: list[GeofenceBreach] = []

        for zone in zones_to_test:
            min_lat, min_lon, max_lat, max_lon = self._bbox_cache.get(
                zone.zone_id, polygon_bounding_box(zone.polygon)
            )
            # Quick bounding box rejection
            if not (min_lat <= t_lat <= max_lat and min_lon <= t_lon <= max_lon):
                continue

            # Detailed ray-casting
            if not point_in_polygon(t_lat, t_lon, zone.polygon):
                continue

            # Target is inside the zone — evaluate compliance rules
            restrictions = set(zone.restrictions)

            # Rule 1: Non-reporting / Dark vessel in protected or monitored area
            if is_dark and ("NO_DARK_VESSEL" in restrictions or "MANDATORY_AIS" in restrictions):
                severity = "CRITICAL" if zone.zone_type == "MPA" else "HIGH"
                violation = (
                    "UNAUTHORIZED_DARK_ENTRY"
                    if zone.zone_type in ("MPA", "EEZ")
                    else "DARK_VESSEL_IN_TSS"
                )
                narrative = (
                    f"{severity}: Dark non-reporting vessel {v_ident} detected inside {zone.name} "
                    f"({zone.zone_type}) without mandatory AIS broadcast."
                )
                breaches.append(
                    GeofenceBreach(
                        zone_id=zone.zone_id,
                        zone_name=zone.name,
                        zone_type=zone.zone_type,
                        vessel_index=vessel_idx,
                        vessel_identifier=v_ident,
                        is_dark=is_dark,
                        lat=t_lat,
                        lon=t_lon,
                        violation_type=violation,
                        severity=severity,
                        narrative=narrative,
                        vessel_class=v_class or None,
                        speed_knots=speed,
                    )
                )

            # Rule 2: Fishing in Marine Protected Area (IUU Fishing)
            if (
                v_class.lower() == "fishing"
                and ("NO_FISHING" in restrictions or zone.zone_type == "MPA")
            ):
                severity = "CRITICAL"
                violation = "ILLEGAL_FISHING_IN_MPA"
                narrative = (
                    f"CRITICAL: Fishing vessel {v_ident} operating inside {zone.name} "
                    f"in violation of marine sanctuary fishing prohibitions."
                )
                breaches.append(
                    GeofenceBreach(
                        zone_id=zone.zone_id,
                        zone_name=zone.name,
                        zone_type=zone.zone_type,
                        vessel_index=vessel_idx,
                        vessel_identifier=v_ident,
                        is_dark=is_dark,
                        lat=t_lat,
                        lon=t_lon,
                        violation_type=violation,
                        severity=severity,
                        narrative=narrative,
                        vessel_class=v_class or None,
                        speed_knots=speed,
                    )
                )

            # Rule 3: Loitering / stationary obstruction in Traffic Separation Scheme
            if (
                speed <= 1.5
                and zone.zone_type == "TRAFFIC_SEPARATION_SCHEME"
                and "NO_ANCHORING" in restrictions
            ):
                severity = "WARNING"
                violation = "TSS_LANE_OBSTRUCTION"
                narrative = (
                    f"WARNING: Vessel {v_ident} stationary or anchored ({speed:.1f} kn) "
                    f"in international Traffic Separation Scheme corridor ({zone.name})."
                )
                breaches.append(
                    GeofenceBreach(
                        zone_id=zone.zone_id,
                        zone_name=zone.name,
                        zone_type=zone.zone_type,
                        vessel_index=vessel_idx,
                        vessel_identifier=v_ident,
                        is_dark=is_dark,
                        lat=t_lat,
                        lon=t_lon,
                        violation_type=violation,
                        severity=severity,
                        narrative=narrative,
                        vessel_class=v_class or None,
                        speed_knots=speed,
                    )
                )

        return breaches

    def evaluate_detections(
        self,
        detections: list[dict[str, Any]],
        scan_bbox: Optional[BoundingBox] = None,
    ) -> dict[str, Any]:
        """Evaluate a full list of scan detections, annotating each in-place and returning summary."""
        candidate_zones = (
            self.zones_for_bbox(scan_bbox) if scan_bbox is not None else self._zones
        )

        all_breaches: list[GeofenceBreach] = []
        zones_intersected: set[str] = set()

        for idx, det in enumerate(detections):
            breaches = self.evaluate_target(det, vessel_idx=det.get("index", idx), candidate_zones=candidate_zones)
            det["geofence_breaches"] = [
                {
                    "zone_id": b.zone_id,
                    "zone_name": b.zone_name,
                    "zone_type": b.zone_type,
                    "violation_type": b.violation_type,
                    "severity": b.severity,
                    "narrative": b.narrative,
                }
                for b in breaches
            ]
            det["in_protected_area"] = any(b.zone_type == "MPA" for b in breaches)
            det["protected_areas"] = list({b.zone_name for b in breaches})

            for b in breaches:
                all_breaches.append(b)
                zones_intersected.add(b.zone_id)

        critical_count = sum(1 for b in all_breaches if b.severity == "CRITICAL")
        high_count = sum(1 for b in all_breaches if b.severity == "HIGH")
        warning_count = sum(1 for b in all_breaches if b.severity == "WARNING")

        # Generate GeoJSON features for zones & breaches
        geojson_features: list[dict[str, Any]] = []

        # Render zones that intersect the scan area
        for zone in candidate_zones:
            coords = [[lon, lat] for lat, lon in zone.polygon]
            # Ensure closed ring
            if coords and coords[0] != coords[-1]:
                coords.append(coords[0])
            geojson_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords],
                },
                "properties": {
                    "feature_type": "GEOFENCE_ZONE",
                    "zone_id": zone.zone_id,
                    "name": zone.name,
                    "zone_type": zone.zone_type,
                    "description": zone.description,
                    "restrictions": list(zone.restrictions),
                },
            })

        # Render breach points
        for b in all_breaches:
            geojson_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [b.lon, b.lat],
                },
                "properties": {
                    "feature_type": "GEOFENCE_BREACH",
                    "zone_id": b.zone_id,
                    "zone_name": b.zone_name,
                    "zone_type": b.zone_type,
                    "vessel_index": b.vessel_index,
                    "vessel_identifier": b.vessel_identifier,
                    "violation_type": b.violation_type,
                    "severity": b.severity,
                    "narrative": b.narrative,
                },
            })

        return {
            "status": "success",
            "total_breaches": len(all_breaches),
            "critical_breaches": critical_count,
            "high_breaches": high_count,
            "warning_breaches": warning_count,
            "zones_evaluated": len(candidate_zones),
            "zones_breached": list(zones_intersected),
            "breaches": [
                {
                    "zone_id": b.zone_id,
                    "zone_name": b.zone_name,
                    "zone_type": b.zone_type,
                    "vessel_index": b.vessel_index,
                    "vessel_identifier": b.vessel_identifier,
                    "is_dark": b.is_dark,
                    "lat": b.lat,
                    "lon": b.lon,
                    "violation_type": b.violation_type,
                    "severity": b.severity,
                    "narrative": b.narrative,
                    "vessel_class": b.vessel_class,
                    "speed_knots": b.speed_knots,
                }
                for b in all_breaches
            ],
            "geojson": {
                "type": "FeatureCollection",
                "features": geojson_features,
            },
        }
