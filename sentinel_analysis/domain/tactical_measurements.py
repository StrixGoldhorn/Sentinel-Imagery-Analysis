"""Domain models, geodesic formulas, and tactical spatial measurement tools.

Calculates:
1. Geodesic distance (m, km, NM) and forward/reverse bearings (0-360 deg True).
2. Polyline cumulative distance and segment breakdown.
3. Spherical enclosed polygon area (sq m, sq km, sq NM, hectares).
4. Navigational corridor buffer generation (centerline + buffer envelope).
5. Vessel dimension analysis (calibrated waterline length, beam, aspect ratio, class estimation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Optional, Sequence

EARTH_RADIUS_M = 6371000.0
METERS_PER_NAUTICAL_MILE = 1852.0


@dataclass(frozen=True)
class GeodesicPoint:
    """Geographic coordinate point in decimal degrees."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.latitude <= 90.0):
            raise ValueError(f"Latitude must be between -90 and 90, got {self.latitude}")
        if not (-180.0 <= self.longitude <= 180.0):
            raise ValueError(f"Longitude must be between -180 and 180, got {self.longitude}")

    def to_dict(self) -> dict[str, float]:
        return {"latitude": round(self.latitude, 6), "longitude": round(self.longitude, 6)}

    def to_geojson_coords(self) -> list[float]:
        return [round(self.longitude, 6), round(self.latitude, 6)]


@dataclass(frozen=True)
class DistanceBearingResult:
    """Distance and directional bearings between two points."""

    start_point: GeodesicPoint
    end_point: GeodesicPoint
    distance_m: float
    distance_km: float
    distance_nm: float
    initial_bearing_deg: float
    final_bearing_deg: float
    compass_cardinal: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_point": self.start_point.to_dict(),
            "end_point": self.end_point.to_dict(),
            "distance": {
                "meters": round(self.distance_m, 1),
                "kilometers": round(self.distance_km, 3),
                "nautical_miles": round(self.distance_nm, 3),
            },
            "bearing": {
                "initial_bearing_deg": round(self.initial_bearing_deg, 1),
                "final_bearing_deg": round(self.final_bearing_deg, 1),
                "compass_cardinal": self.compass_cardinal,
            },
        }


@dataclass(frozen=True)
class PolylineMeasurementResult:
    """Multi-segment distance tracking along a route or trackline."""

    total_distance_m: float
    total_distance_km: float
    total_distance_nm: float
    segment_count: int
    segments: list[DistanceBearingResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_distance": {
                "meters": round(self.total_distance_m, 1),
                "kilometers": round(self.total_distance_km, 3),
                "nautical_miles": round(self.total_distance_nm, 3),
            },
            "segment_count": self.segment_count,
            "segments": [s.to_dict() for s in self.segments],
        }


@dataclass(frozen=True)
class AreaMeasurementResult:
    """Enclosed spherical surface area of a polygon."""

    vertex_count: int
    area_sq_m: float
    area_sq_km: float
    area_sq_nm: float
    area_hectares: float
    perimeter_m: float
    perimeter_nm: float
    centroid: GeodesicPoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "vertex_count": self.vertex_count,
            "area": {
                "sq_meters": round(self.area_sq_m, 1),
                "sq_kilometers": round(self.area_sq_km, 4),
                "sq_nautical_miles": round(self.area_sq_nm, 4),
                "hectares": round(self.area_hectares, 2),
            },
            "perimeter": {
                "meters": round(self.perimeter_m, 1),
                "nautical_miles": round(self.perimeter_nm, 3),
            },
            "centroid": self.centroid.to_dict(),
        }


@dataclass(frozen=True)
class CorridorMeasurementResult:
    """Tactical navigational corridor buffer envelope along a route."""

    centerline_distance_m: float
    centerline_distance_nm: float
    width_m: float
    width_nm: float
    corridor_area_sq_km: float
    corridor_area_sq_nm: float
    corridor_polygon_geojson: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "centerline_distance": {
                "meters": round(self.centerline_distance_m, 1),
                "nautical_miles": round(self.centerline_distance_nm, 3),
            },
            "width": {
                "meters": round(self.width_m, 1),
                "nautical_miles": round(self.width_nm, 3),
            },
            "corridor_area": {
                "sq_kilometers": round(self.corridor_area_sq_km, 3),
                "sq_nautical_miles": round(self.corridor_area_sq_nm, 3),
            },
            "geometry": self.corridor_polygon_geojson,
        }


@dataclass(frozen=True)
class VesselDimensionResult:
    """Calibrated vessel hull dimension analysis and naval estimation."""

    length_m: float
    beam_m: float
    aspect_ratio: float
    heading_deg: float
    pixel_length: float
    meters_per_pixel: float
    estimated_class: str
    tactical_assessment: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensions": {
                "length_m": round(self.length_m, 1),
                "beam_m": round(self.beam_m, 1),
                "aspect_ratio": round(self.aspect_ratio, 2),
                "heading_deg": round(self.heading_deg, 1),
            },
            "calibration": {
                "pixel_length": round(self.pixel_length, 2),
                "meters_per_pixel": round(self.meters_per_pixel, 2),
            },
            "classification": {
                "estimated_class": self.estimated_class,
                "tactical_assessment": self.tactical_assessment,
            },
        }


# =============================================================================
# GEODESIC & SPATIAL MATH ENGINE
# =============================================================================

def haversine_distance_m(p1: GeodesicPoint, p2: GeodesicPoint) -> float:
    """Calculate great-circle distance between two points in meters using Haversine formula."""
    phi1 = math.radians(p1.latitude)
    phi2 = math.radians(p2.latitude)
    delta_phi = math.radians(p2.latitude - p1.latitude)
    delta_lambda = math.radians(p2.longitude - p1.longitude)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_M * c


def initial_bearing_deg(p1: GeodesicPoint, p2: GeodesicPoint) -> float:
    """Calculate forward initial bearing from p1 to p2 in degrees (0 - 360 True North)."""
    phi1 = math.radians(p1.latitude)
    phi2 = math.radians(p2.latitude)
    delta_lambda = math.radians(p2.longitude - p1.longitude)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return bearing


def compass_cardinal_from_bearing(bearing_deg: float) -> str:
    """Map azimuth in degrees to 16-point nautical compass cardinal."""
    b = (bearing_deg + 360.0) % 360.0
    cardinals = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    ]
    idx = int((b + 11.25) / 22.5) % 16
    return cardinals[idx]


def destination_point(start: GeodesicPoint, distance_m: float, bearing_deg: float) -> GeodesicPoint:
    """Project a point along a geodesic given distance and initial bearing."""
    delta = distance_m / EARTH_RADIUS_M
    theta = math.radians(bearing_deg)
    phi1 = math.radians(start.latitude)
    lambda1 = math.radians(start.longitude)

    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))

    y = math.sin(theta) * math.sin(delta) * math.cos(phi1)
    x = math.cos(delta) - math.sin(phi1) * math.sin(phi2)
    lambda2 = lambda1 + math.atan2(y, x)

    # Normalize longitude to [-180, 180]
    lon_deg = (math.degrees(lambda2) + 540.0) % 360.0 - 180.0
    lat_deg = math.degrees(phi2)
    return GeodesicPoint(lat_deg, lon_deg)


def measure_distance_and_bearing(p1: GeodesicPoint, p2: GeodesicPoint) -> DistanceBearingResult:
    """Compute distance, forward bearing, reverse bearing, and compass heading between two points."""
    dist_m = haversine_distance_m(p1, p2)
    init_b = initial_bearing_deg(p1, p2)
    # Final bearing is reverse of initial bearing from p2 to p1 + 180
    rev_b = (initial_bearing_deg(p2, p1) + 180.0) % 360.0
    cardinal = compass_cardinal_from_bearing(init_b)

    return DistanceBearingResult(
        start_point=p1,
        end_point=p2,
        distance_m=dist_m,
        distance_km=dist_m / 1000.0,
        distance_nm=dist_m / METERS_PER_NAUTICAL_MILE,
        initial_bearing_deg=init_b,
        final_bearing_deg=rev_b,
        compass_cardinal=cardinal,
    )


def measure_polyline_path(points: Sequence[GeodesicPoint]) -> PolylineMeasurementResult:
    """Compute cumulative distance and breakdown of every leg along a polyline track."""
    if len(points) < 2:
        raise ValueError("Polyline requires at least 2 points to measure")

    segments: list[DistanceBearingResult] = []
    total_m = 0.0

    for i in range(len(points) - 1):
        seg = measure_distance_and_bearing(points[i], points[i + 1])
        segments.append(seg)
        total_m += seg.distance_m

    return PolylineMeasurementResult(
        total_distance_m=total_m,
        total_distance_km=total_m / 1000.0,
        total_distance_nm=total_m / METERS_PER_NAUTICAL_MILE,
        segment_count=len(segments),
        segments=segments,
    )


def measure_polygon_area(vertices: Sequence[GeodesicPoint]) -> AreaMeasurementResult:
    """Calculate spherical surface area and perimeter for an arbitrary geodesic polygon."""
    n = len(vertices)
    if n < 3:
        raise ValueError("Polygon measurement requires at least 3 vertices")

    # Ensure list is closed or handled cyclically
    # Compute spherical excess using spherical trapezoids formula
    excess = 0.0
    perimeter_m = 0.0
    sum_lat = 0.0
    sum_lon = 0.0

    for i in range(n):
        p_curr = vertices[i]
        p_next = vertices[(i + 1) % n]
        sum_lat += p_curr.latitude
        sum_lon += p_curr.longitude

        perimeter_m += haversine_distance_m(p_curr, p_next)

        phi1 = math.radians(p_curr.latitude)
        phi2 = math.radians(p_next.latitude)
        d_lambda = math.radians(p_next.longitude - p_curr.longitude)

        # Normalize delta longitude to [-pi, pi] to handle meridian crossing
        d_lambda = (d_lambda + math.pi) % (2.0 * math.pi) - math.pi

        # Trapezoidal spherical strip integral
        excess += d_lambda * (2.0 + math.sin(phi1) + math.sin(phi2))

    area_sq_m = abs(excess) / 2.0 * (EARTH_RADIUS_M ** 2)
    area_sq_km = area_sq_m / 1_000_000.0
    area_sq_nm = area_sq_m / (METERS_PER_NAUTICAL_MILE ** 2)
    area_hectares = area_sq_m / 10_000.0

    centroid = GeodesicPoint(sum_lat / n, sum_lon / n)

    return AreaMeasurementResult(
        vertex_count=n,
        area_sq_m=area_sq_m,
        area_sq_km=area_sq_km,
        area_sq_nm=area_sq_nm,
        area_hectares=area_hectares,
        perimeter_m=perimeter_m,
        perimeter_nm=perimeter_m / METERS_PER_NAUTICAL_MILE,
        centroid=centroid,
    )


def generate_tactical_corridor(
    centerline: Sequence[GeodesicPoint],
    width_m: float,
) -> CorridorMeasurementResult:
    """Generate a tactical navigational corridor buffer around a route centerline."""
    if len(centerline) < 2:
        raise ValueError("Corridor generation requires at least 2 centerline points")
    if width_m <= 0.0:
        raise ValueError("Corridor width must be positive")

    half_w = width_m / 2.0
    left_offsets: list[GeodesicPoint] = []
    right_offsets: list[GeodesicPoint] = []

    polyline_res = measure_polyline_path(centerline)

    for i in range(len(centerline)):
        curr_pt = centerline[i]

        # Determine average forward track heading at this vertex
        if i == 0:
            heading = initial_bearing_deg(centerline[0], centerline[1])
        elif i == len(centerline) - 1:
            heading = initial_bearing_deg(centerline[-2], centerline[-1])
        else:
            b1 = initial_bearing_deg(centerline[i - 1], centerline[i])
            b2 = initial_bearing_deg(centerline[i], centerline[i + 1])
            # Average angle taking care of 0/360 wrap
            rad1 = math.radians(b1)
            rad2 = math.radians(b2)
            avg_x = math.cos(rad1) + math.cos(rad2)
            avg_y = math.sin(rad1) + math.sin(rad2)
            heading = (math.degrees(math.atan2(avg_y, avg_x)) + 360.0) % 360.0

        # Left offset: heading - 90 deg
        left_bearing = (heading - 90.0 + 360.0) % 360.0
        # Right offset: heading + 90 deg
        right_bearing = (heading + 90.0 + 360.0) % 360.0

        left_pt = destination_point(curr_pt, half_w, left_bearing)
        right_pt = destination_point(curr_pt, half_w, right_bearing)

        left_offsets.append(left_pt)
        right_offsets.append(right_pt)

    # Form closed polygon: Left offsets (start to end) -> Right offsets (end to start) -> Close
    corridor_ring = list(left_offsets) + list(reversed(right_offsets))
    # Close ring
    corridor_ring.append(left_offsets[0])

    area_res = measure_polygon_area(corridor_ring[:-1])

    geojson_polygon = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[pt.to_geojson_coords() for pt in corridor_ring]],
        },
        "properties": {
            "centerline_distance_nm": round(polyline_res.total_distance_nm, 3),
            "width_nm": round(width_m / METERS_PER_NAUTICAL_MILE, 3),
            "area_sq_nm": round(area_res.area_sq_nm, 3),
        },
    }

    return CorridorMeasurementResult(
        centerline_distance_m=polyline_res.total_distance_m,
        centerline_distance_nm=polyline_res.total_distance_nm,
        width_m=width_m,
        width_nm=width_m / METERS_PER_NAUTICAL_MILE,
        corridor_area_sq_km=area_res.area_sq_km,
        corridor_area_sq_nm=area_res.area_sq_nm,
        corridor_polygon_geojson=geojson_polygon,
    )


def classify_vessel_length(length_m: float) -> tuple[str, str]:
    """Estimate naval vessel classification based on waterline length."""
    if length_m < 22.0:
        return (
            "Small Craft / Coastal Skiff",
            "Speedboat, RHIB, small fishing skiff, or auxiliary workboat; high agile loitering risk.",
        )
    elif length_m < 55.0:
        return (
            "Coastal Fishing / Fast Patrol Boat",
            "Artisanal trawler, purse seiner, or naval offshore patrol craft (OPV).",
        )
    elif length_m < 110.0:
        return (
            "Commercial Trawler / Offshore Supply / Corvette",
            "Industrial deep-sea fishing vessel, tug, platform supply, or naval light combatant.",
        )
    elif length_m < 175.0:
        return (
            "General Cargo / Handysize Bulk Carrier / Frigate",
            "Standard merchant cargo feeder, small tanker, or guided-missile frigate.",
        )
    elif length_m < 250.0:
        return (
            "Panamax Bulk Carrier / Destroyer / Container Feeder",
            "Medium-to-large merchant transport vessel or naval guided-missile destroyer.",
        )
    elif length_m < 340.0:
        return (
            "Capesize Bulk / Post-Panamax Container / Cruiser",
            "Heavy commercial container carrier, ore carrier, or capital surface combatant.",
        )
    else:
        return (
            "Ultra-Large Carrier (ULCV / VLCC) / Aircraft Carrier",
            "Super-tanker (VLCC/ULCC), 20,000+ TEU mega-container, or naval supercarrier.",
        )


def measure_vessel_dimension_from_pixels(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    meters_per_pixel: float = 10.0,
    beam_width_px: Optional[float] = None,
) -> VesselDimensionResult:
    """Measure vessel waterline length, beam, aspect ratio, and heading from image pixels."""
    dx = x2 - x1
    dy = y2 - y1
    px_len = math.hypot(dx, dy)
    if px_len <= 0.0:
        raise ValueError("Line endpoints must be distinct to measure vessel dimension")

    scale = max(0.01, float(meters_per_pixel))
    length_m = px_len * scale

    # In image pixel coordinates: Y increases downward, X increases rightward
    # 0 deg = North (upward, -dy), 90 deg = East (rightward, +dx)
    heading_deg = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0

    if beam_width_px is not None and beam_width_px > 0.0:
        beam_m = beam_width_px * scale
        aspect_ratio = length_m / max(1.0, beam_m)
    else:
        # Standard merchant/naval length-to-beam ratio default ~6.0
        aspect_ratio = 6.2
        beam_m = length_m / aspect_ratio

    vessel_class, tactical_assessment = classify_vessel_length(length_m)

    return VesselDimensionResult(
        length_m=length_m,
        beam_m=beam_m,
        aspect_ratio=aspect_ratio,
        heading_deg=heading_deg,
        pixel_length=px_len,
        meters_per_pixel=scale,
        estimated_class=vessel_class,
        tactical_assessment=tactical_assessment,
    )
