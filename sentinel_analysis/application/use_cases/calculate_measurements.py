"""Application use case for calculating tactical geospatial and vessel measurements."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Sequence

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.tactical_measurements import (
    AreaMeasurementResult,
    CorridorMeasurementResult,
    DistanceBearingResult,
    GeodesicPoint,
    PolylineMeasurementResult,
    VesselDimensionResult,
    generate_tactical_corridor,
    measure_distance_and_bearing,
    measure_polygon_area,
    measure_polyline_path,
    measure_vessel_dimension_from_pixels,
    METERS_PER_NAUTICAL_MILE,
)

logger = logging.getLogger(__name__)


def _parse_geodesic_points(raw_points: Sequence[Any]) -> list[GeodesicPoint]:
    """Parse a flexible list of coordinates into domain GeodesicPoint value objects."""
    points: list[GeodesicPoint] = []
    for item in raw_points:
        if isinstance(item, GeodesicPoint):
            points.append(item)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            # Check if format is [lon, lat] (GeoJSON standard) or [lat, lon]
            # If item contains GeoJSON [lon, lat]
            # Standard convention in our web API: if given as [lat, lon] or explicitly named
            p0, p1 = float(item[0]), float(item[1])
            # If p0 is within [-90, 90] and p1 is within [-180, 180], assume (lat, lon)
            points.append(GeodesicPoint(latitude=p0, longitude=p1))
        elif isinstance(item, dict):
            lat = float(item.get("lat", item.get("latitude", 0.0)))
            lon = float(item.get("lon", item.get("lng", item.get("longitude", 0.0))))
            points.append(GeodesicPoint(latitude=lat, longitude=lon))
        else:
            raise ValueError(f"Unable to parse coordinate point from: {item}")
    return points


class CalculateTacticalMeasurements:
    """Orchestrates geospatial and tactical SAR measurements."""

    def __init__(self, scan_repository: Optional[ScanRepository] = None) -> None:
        self._scan_repo = scan_repository

    def measure_distance_bearing(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> DistanceBearingResult:
        """Calculate geodesic distance and True North bearings between two points."""
        p1 = GeodesicPoint(latitude=float(lat1), longitude=float(lon1))
        p2 = GeodesicPoint(latitude=float(lat2), longitude=float(lon2))
        return measure_distance_and_bearing(p1, p2)

    def measure_polyline(self, points: Sequence[Any]) -> PolylineMeasurementResult:
        """Measure total distance and per-segment azimuths along a polyline track."""
        geo_points = _parse_geodesic_points(points)
        return measure_polyline_path(geo_points)

    def measure_area(self, vertices: Sequence[Any]) -> AreaMeasurementResult:
        """Calculate spherical polygon enclosed area, perimeter, and centroid."""
        geo_points = _parse_geodesic_points(vertices)
        return measure_polygon_area(geo_points)

    def generate_corridor(
        self,
        centerline: Sequence[Any],
        width: float,
        unit: str = "NM",
    ) -> CorridorMeasurementResult:
        """Generate a tactical navigational corridor buffer around a route."""
        geo_points = _parse_geodesic_points(centerline)
        norm_unit = (unit or "NM").upper().strip()

        if norm_unit == "NM":
            width_m = float(width) * METERS_PER_NAUTICAL_MILE
        elif norm_unit == "KM":
            width_m = float(width) * 1000.0
        else:
            width_m = float(width)

        return generate_tactical_corridor(geo_points, width_m)

    def measure_vessel(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        scan_id: Optional[str] = None,
        meters_per_pixel: Optional[float] = None,
        beam_width_px: Optional[float] = None,
    ) -> VesselDimensionResult:
        """Measure vessel waterline length, beam, aspect ratio, and heading on SAR imagery."""
        scale = 10.0  # Sentinel-1 default 10m/px
        if meters_per_pixel is not None and meters_per_pixel > 0:
            scale = float(meters_per_pixel)
        elif scan_id and self._scan_repo:
            scan = self._scan_repo.get(scan_id)
            if scan and scan.metadata.get("settings", {}).get("resolution_m"):
                try:
                    scale = float(scan.metadata["settings"]["resolution_m"])
                except (ValueError, TypeError):
                    pass

        return measure_vessel_dimension_from_pixels(
            x1=float(x1),
            y1=float(y1),
            x2=float(x2),
            y2=float(y2),
            meters_per_pixel=scale,
            beam_width_px=float(beam_width_px) if beam_width_px is not None else None,
        )

    def execute(self, tool_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """Unified tactical measurement execution gateway."""
        tool = (tool_type or "").lower().strip()

        if tool in ("distance", "bearing", "distance_bearing"):
            lat1 = params.get("lat1", params.get("latitude1"))
            lon1 = params.get("lon1", params.get("longitude1"))
            lat2 = params.get("lat2", params.get("latitude2"))
            lon2 = params.get("lon2", params.get("longitude2"))
            if any(v is None for v in (lat1, lon1, lat2, lon2)):
                raise ValueError("Distance measurement requires lat1, lon1, lat2, lon2")
            res = self.measure_distance_bearing(lat1, lon1, lat2, lon2)
            return {"tool": "distance_bearing", "success": True, "result": res.to_dict()}

        elif tool in ("polyline", "trackline", "path"):
            points = params.get("points") or params.get("coordinates") or []
            res_poly = self.measure_polyline(points)
            return {"tool": "polyline", "success": True, "result": res_poly.to_dict()}

        elif tool in ("area", "polygon"):
            vertices = params.get("vertices") or params.get("points") or params.get("coordinates") or []
            res_area = self.measure_area(vertices)
            return {"tool": "area", "success": True, "result": res_area.to_dict()}

        elif tool in ("corridor", "buffer"):
            centerline = params.get("centerline") or params.get("points") or []
            width = float(params.get("width", 2.0))
            unit = str(params.get("unit", "NM"))
            res_corridor = self.generate_corridor(centerline, width=width, unit=unit)
            return {"tool": "corridor", "success": True, "result": res_corridor.to_dict()}

        elif tool in ("vessel_dimension", "vessel", "ship_dimension"):
            x1 = params.get("x1")
            y1 = params.get("y1")
            x2 = params.get("x2")
            y2 = params.get("y2")
            if any(v is None for v in (x1, y1, x2, y2)):
                raise ValueError("Vessel dimension measurement requires x1, y1, x2, y2")
            scan_id = params.get("scan_id")
            m_per_px = params.get("meters_per_pixel")
            beam_px = params.get("beam_width_px")
            res_vessel = self.measure_vessel(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                scan_id=scan_id,
                meters_per_pixel=m_per_px,
                beam_width_px=beam_px,
            )
            return {"tool": "vessel_dimension", "success": True, "result": res_vessel.to_dict()}

        else:
            valid_tools = "distance, bearing, polyline, area, corridor, vessel_dimension"
            raise ValueError(f"Unknown measurement tool '{tool_type}'. Valid tools: {valid_tools}")
