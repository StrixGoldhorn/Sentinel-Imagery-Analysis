"""Use case to aggregate historical AIS traffic corridors and dark vessel clusters into density heatmaps."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.domain.entities import (
    BoundingBox,
    TrafficHeatmapPoint,
    TrafficHeatmapReport,
)


class GenerateHistoricalTrafficHeatmap:
    """Aggregates AIS vessel tracks and historical dark vessel detections into spatial density grids."""

    def __init__(
        self,
        ais_repository: Optional[Any] = None,
        scan_repository: Optional[Any] = None,
    ) -> None:
        self.ais_repository = ais_repository
        self.scan_repository = scan_repository

    def execute(
        self,
        bbox: Optional[BoundingBox] = None,
        *,
        time_range: Optional[tuple[Optional[datetime], Optional[datetime]]] = None,
        include_ais: bool = True,
        include_dark_vessels: bool = True,
        cell_size_degrees: float = 0.02,
        ais_limit: int = 50000,
    ) -> dict[str, Any]:
        """Generate traffic density heatmaps for AIS corridors and dark vessel clusters."""
        if cell_size_degrees <= 0:
            cell_size_degrees = 0.02
        cell_size_degrees = min(1.0, max(0.005, float(cell_size_degrees)))

        ais_coords: list[tuple[float, float]] = []
        if include_ais and self.ais_repository is not None:
            ais_coords = self._fetch_ais_coords(bbox, time_range, ais_limit)

        dark_vessels: list[dict[str, Any]] = []
        if include_dark_vessels and self.scan_repository is not None:
            dark_vessels = self._fetch_dark_vessels(bbox)

        # 1. Spatial binning for AIS
        ais_grid: dict[tuple[float, float], int] = {}
        for lat, lon in ais_coords:
            bin_lat = round(round(lat / cell_size_degrees) * cell_size_degrees, 6)
            bin_lon = round(round(lon / cell_size_degrees) * cell_size_degrees, 6)
            ais_grid[(bin_lat, bin_lon)] = ais_grid.get((bin_lat, bin_lon), 0) + 1

        # 2. Spatial binning for Dark Vessels
        dark_grid: dict[tuple[float, float], list[dict[str, Any]]] = {}
        for dv in dark_vessels:
            d_lat = dv["lat"]
            d_lon = dv["lon"]
            bin_lat = round(round(d_lat / cell_size_degrees) * cell_size_degrees, 6)
            bin_lon = round(round(d_lon / cell_size_degrees) * cell_size_degrees, 6)
            if (bin_lat, bin_lon) not in dark_grid:
                dark_grid[(bin_lat, bin_lon)] = []
            dark_grid[(bin_lat, bin_lon)].append(dv)

        max_ais_count = max(ais_grid.values()) if ais_grid else 1
        max_dark_count = max(len(v) for v in dark_grid.values()) if dark_grid else 1

        points: list[TrafficHeatmapPoint] = []
        leaflet_heat_points: list[list[float]] = []
        ais_density_points: list[list[float]] = []
        geojson_features: list[dict[str, Any]] = []

        # Process AIS bins
        for (b_lat, b_lon), count in ais_grid.items():
            # Logarithmic scaling to highlight both high corridors and low-traffic routes
            intensity = round(
                math.log1p(count) / math.log1p(max_ais_count) if max_ais_count > 1 else 1.0,
                4,
            )
            intensity = max(0.05, min(1.0, intensity))
            pt = TrafficHeatmapPoint(
                latitude=b_lat,
                longitude=b_lon,
                intensity=intensity,
                category="ais",
                count=count,
            )
            points.append(pt)
            ais_density_points.append([b_lat, b_lon, intensity])
            leaflet_heat_points.append([b_lat, b_lon, intensity])

            geojson_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [b_lon, b_lat],
                },
                "properties": {
                    "category": "ais",
                    "count": count,
                    "intensity": intensity,
                },
            })

        # Process Dark Vessel bins
        dark_vessel_clusters: list[dict[str, Any]] = []
        for (b_lat, b_lon), dv_list in dark_grid.items():
            count = len(dv_list)
            intensity = round(
                math.log1p(count) / math.log1p(max_dark_count) if max_dark_count > 1 else 1.0,
                4,
            )
            intensity = max(0.1, min(1.0, intensity))
            total_length = sum(float(v.get("length") or 30.0) for v in dv_list)
            avg_length = round(total_length / count, 1)

            pt = TrafficHeatmapPoint(
                latitude=b_lat,
                longitude=b_lon,
                intensity=intensity,
                category="dark_vessel",
                count=count,
            )
            points.append(pt)
            leaflet_heat_points.append([b_lat, b_lon, round(intensity * 1.5, 4)])

            cluster_item = {
                "latitude": b_lat,
                "longitude": b_lon,
                "count": count,
                "intensity": intensity,
                "avg_length_meters": avg_length,
                "vessels": [
                    {
                        "scan_folder": v.get("scan_folder"),
                        "length": v.get("length"),
                        "confidence": v.get("confidence"),
                        "timestamp": v.get("timestamp"),
                    }
                    for v in dv_list[:10]
                ],
            }
            dark_vessel_clusters.append(cluster_item)

            geojson_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [b_lon, b_lat],
                },
                "properties": {
                    "category": "dark_vessel",
                    "count": count,
                    "intensity": intensity,
                    "avg_length_meters": avg_length,
                    "risk_level": "CRITICAL" if count >= 3 or avg_length >= 100 else "WARNING",
                },
            })

        report = TrafficHeatmapReport(
            total_ais_points=len(ais_coords),
            total_dark_vessels=len(dark_vessels),
            total_cells=len(points),
            points=points,
            bbox=bbox,
            generated_at=datetime.now(timezone.utc),
        )

        return {
            "status": "success",
            "summary": {
                "total_ais_raw_points": len(ais_coords),
                "total_dark_vessels_detected": len(dark_vessels),
                "total_density_cells": len(points),
                "ais_cells": len(ais_grid),
                "dark_vessel_cells": len(dark_grid),
                "cell_size_degrees": cell_size_degrees,
                "generated_at": report.generated_at.isoformat() if report.generated_at else None,
            },
            "leaflet_heat_points": leaflet_heat_points,
            "ais_density_points": ais_density_points,
            "dark_vessel_clusters": dark_vessel_clusters,
            "geojson": {
                "type": "FeatureCollection",
                "features": geojson_features,
            },
        }

    def _fetch_ais_coords(
        self,
        bbox: Optional[BoundingBox],
        time_range: Optional[tuple[Optional[datetime], Optional[datetime]]],
        limit: int,
    ) -> list[tuple[float, float]]:
        if hasattr(self.ais_repository, "get_density_coordinates"):
            return self.ais_repository.get_density_coordinates(
                bbox=bbox, time_range=time_range, limit=limit
            )
        if hasattr(self.ais_repository, "get_vessel_positions"):
            records = self.ais_repository.get_vessel_positions(
                bbox=bbox, time_range=time_range, limit=limit, latest_only=False
            )
            coords = []
            for r in records:
                lat = r.get("latitude")
                lon = r.get("longitude")
                if lat is not None and lon is not None:
                    coords.append((float(lat), float(lon)))
            return coords
        return []

    def _fetch_dark_vessels(self, bbox: Optional[BoundingBox]) -> list[dict[str, Any]]:
        dark_vessels: list[dict[str, Any]] = []
        if not hasattr(self.scan_repository, "list"):
            return dark_vessels

        scans = self.scan_repository.list()
        for scan in scans:
            folder_name = getattr(scan, "folder_name", None) or getattr(scan, "id", None)
            if not folder_name:
                continue

            workspace_dir = None
            if hasattr(self.scan_repository, "prepare"):
                try:
                    workspace_dir = Path(self.scan_repository.prepare(folder_name))
                except Exception:
                    pass
            elif hasattr(self.scan_repository, "output_root"):
                workspace_dir = Path(self.scan_repository.output_root) / folder_name

            if workspace_dir is None or not workspace_dir.exists():
                continue

            cv_path = workspace_dir / "cv_results.json"
            if not cv_path.is_file():
                continue

            try:
                with open(cv_path, "r", encoding="utf-8") as f:
                    cv_data = json.load(f)
            except Exception:
                continue

            detections = cv_data.get("detections", [])
            scan_bbox_dict = cv_data.get("bbox") or getattr(scan, "bbox", None)
            scan_bbox = None
            if isinstance(scan_bbox_dict, dict):
                try:
                    scan_bbox = BoundingBox(
                        float(scan_bbox_dict.get("min_lon", scan_bbox_dict.get("min_longitude"))),
                        float(scan_bbox_dict.get("min_lat", scan_bbox_dict.get("min_latitude"))),
                        float(scan_bbox_dict.get("max_lon", scan_bbox_dict.get("max_longitude"))),
                        float(scan_bbox_dict.get("max_lat", scan_bbox_dict.get("max_latitude"))),
                    )
                except Exception:
                    scan_bbox = None
            elif isinstance(scan_bbox_dict, BoundingBox):
                scan_bbox = scan_bbox_dict

            for det in detections:
                is_dark = bool(det.get("is_dark_vessel", not det.get("is_correlated", False)))
                if not is_dark:
                    continue

                lat = det.get("lat") or det.get("latitude")
                lon = det.get("lng") or det.get("lon") or det.get("longitude")

                if (lat is None or lon is None) and scan_bbox is not None:
                    # Project from pixel coordinates
                    cx = det.get("center_x") or (det.get("x", 0) + det.get("width", 0) / 2.0)
                    cy = det.get("center_y") or (det.get("y", 0) + det.get("height", 0) / 2.0)
                    img_w = float(cv_data.get("image_width", 1000) or 1000)
                    img_h = float(cv_data.get("image_height", 1000) or 1000)
                    lat = scan_bbox.max_latitude - float(cy) * (scan_bbox.max_latitude - scan_bbox.min_latitude) / img_h
                    lon = scan_bbox.min_longitude + float(cx) * (scan_bbox.max_longitude - scan_bbox.min_longitude) / img_w

                if lat is None or lon is None:
                    continue

                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                except (TypeError, ValueError):
                    continue

                if bbox is not None:
                    if not (bbox.min_latitude <= lat_f <= bbox.max_latitude and bbox.min_longitude <= lon_f <= bbox.max_longitude):
                        continue

                dark_vessels.append({
                    "lat": lat_f,
                    "lon": lon_f,
                    "scan_folder": folder_name,
                    "length": det.get("length"),
                    "confidence": det.get("confidence"),
                    "timestamp": cv_data.get("timestamp"),
                })

        return dark_vessels
