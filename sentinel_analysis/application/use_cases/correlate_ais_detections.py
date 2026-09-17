"""Use case to correlate SAR Computer Vision ship detections with AIS vessel data."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.domain.entities import BoundingBox, Scan, ShipDetection


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the Great-Circle distance between two points on Earth in meters."""
    radius = 6371000.0  # Earth's radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    )
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return radius * c


def distance_to_bbox_meters(
    lat: float,
    lon: float,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
) -> float:
    """Calculate the shortest distance from a point to an axis-aligned geographic bounding box.

    Returns 0.0 if the point is strictly inside the bounding box.
    """
    if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
        return 0.0

    clamped_lat = max(min_lat, min(max_lat, lat))
    clamped_lon = max(min_lon, min(max_lon, lon))
    return haversine_distance_meters(lat, lon, clamped_lat, clamped_lon)


class CorrelateDetectionsWithAIS:
    """Correlate SAR ship detections against known AIS vessel locations.

    Detections are classified into three operational categories:
    1. 'inside_box': AIS ping is strictly inside the detection bounding box (distance == 0.0m)
    2. 'outside_box': Closest AIS ping is outside the box but within tolerance_meters (0 < distance <= tolerance)
    3. 'uncorrelated': No AIS ping found within the box or buffer region
    """

    def __init__(
        self,
        ais_repository: AISRepository,
        settings_repository: Optional[Any] = None,
    ) -> None:
        self._ais_repo = ais_repository
        self._settings_repo = settings_repository

    def get_default_tolerance_meters(self) -> float:
        """Fetch configured correlation tolerance from settings repository, defaulting to 100.0m."""
        if self._settings_repo is not None:
            try:
                cv_settings = self._settings_repo.get_section("cv") or {}
                raw = cv_settings.get("ais_correlation_distance_meters")
                if raw is not None:
                    return max(0.0, float(raw))
            except Exception:
                pass
        return 100.0

    def execute(
        self,
        detections: list[ShipDetection | dict[str, Any]],
        scan: Scan,
        image_width: int,
        image_height: int,
        *,
        tolerance_meters: Optional[float] = None,
        candidate_vessels: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Perform proximity correlation on detections and return enriched detection dictionaries."""
        if tolerance_meters is None or tolerance_meters < 0.0:
            tolerance_meters = self.get_default_tolerance_meters()

        tolerance_meters = float(tolerance_meters)
        scan_bbox = scan.bbox
        img_w = max(1, int(image_width))
        img_h = max(1, int(image_height))

        # Query candidate AIS records if not explicitly passed
        if candidate_vessels is None:
            candidate_vessels = self._fetch_candidate_vessels(scan, tolerance_meters)

        # 1. Project detection bounding boxes to WGS-84 geographic extents
        projected_detections: list[dict[str, Any]] = []
        for idx, det in enumerate(detections):
            if isinstance(det, dict):
                x = float(det.get("x", 0))
                y = float(det.get("y", 0))
                w = float(det.get("width", 0))
                h = float(det.get("height", 0))
                cx = det.get("center_x")
                cy = det.get("center_y")
                conf = det.get("confidence")
                angle = det.get("angle")
                length = det.get("length")
                beam = det.get("beam")
                polygon_points = det.get("polygon_points")
            else:
                x = float(det.x)
                y = float(det.y)
                w = float(det.width)
                h = float(det.height)
                cx = det.center_x
                cy = det.center_y
                conf = det.confidence
                angle = det.angle
                length = det.length
                beam = det.beam
                polygon_points = getattr(det, "polygon_points", None)

            center_x = float(cx) if cx is not None else (x + w / 2.0)
            center_y = float(cy) if cy is not None else (y + h / 2.0)

            lat_span = scan_bbox.max_latitude - scan_bbox.min_latitude
            lon_span = scan_bbox.max_longitude - scan_bbox.min_longitude

            lat_scale = lat_span / img_h
            lon_scale = lon_span / img_w

            det_min_lat = scan_bbox.max_latitude - (y + h) * lat_scale
            det_max_lat = scan_bbox.max_latitude - y * lat_scale
            det_min_lon = scan_bbox.min_longitude + x * lon_scale
            det_max_lon = scan_bbox.min_longitude + (x + w) * lon_scale

            center_lat = scan_bbox.max_latitude - center_y * lat_scale
            center_lon = scan_bbox.min_longitude + center_x * lon_scale

            projected_detections.append({
                "index": idx,
                "x": int(x),
                "y": int(y),
                "width": int(w),
                "height": int(h),
                "center_x": center_x,
                "center_y": center_y,
                "confidence": conf,
                "angle": angle,
                "length": length,
                "beam": beam,
                "polygon_points": polygon_points,
                "lat": round(center_lat, 5),
                "lng": round(center_lon, 5),
                "geo_bbox": {
                    "min_lat": det_min_lat,
                    "max_lat": det_max_lat,
                    "min_lon": det_min_lon,
                    "max_lon": det_max_lon,
                },
                "correlation_status": "uncorrelated",
                "is_correlated": False,
                "correlated_ais": None,
            })

        # 2. Build candidate match pairs: (detection_index, vessel_dict, dist_to_box, dist_to_center)
        candidate_matches: list[tuple[int, dict[str, Any], float, float]] = []

        for p_det in projected_detections:
            g_box = p_det["geo_bbox"]
            det_idx = p_det["index"]
            c_lat = p_det["lat"]
            c_lon = p_det["lng"]

            for vessel in candidate_vessels:
                v_lat = vessel.get("latitude")
                v_lon = vessel.get("longitude")
                if v_lat is None or v_lon is None:
                    continue

                v_lat = float(v_lat)
                v_lon = float(v_lon)

                dist_to_box = distance_to_bbox_meters(
                    v_lat,
                    v_lon,
                    g_box["min_lat"],
                    g_box["max_lat"],
                    g_box["min_lon"],
                    g_box["max_lon"],
                )

                if dist_to_box <= tolerance_meters:
                    dist_to_center = haversine_distance_meters(v_lat, v_lon, c_lat, c_lon)
                    candidate_matches.append((det_idx, vessel, dist_to_box, dist_to_center))

        # 3. Resolve conflicts: Greedy assignment by (dist_to_box, dist_to_center)
        candidate_matches.sort(key=lambda item: (item[2], item[3]))

        matched_detections: set[int] = set()
        matched_vessel_keys: set[str] = set()

        for det_idx, vessel, dist_to_box, dist_to_center in candidate_matches:
            if det_idx in matched_detections:
                continue

            v_key = str(vessel.get("mmsi") or vessel.get("vessel_id") or id(vessel))
            if v_key in matched_vessel_keys:
                continue

            match_status = "inside_box" if dist_to_box == 0.0 else "outside_box"

            matched_detections.add(det_idx)
            matched_vessel_keys.add(v_key)

            projected_detections[det_idx]["correlation_status"] = match_status
            projected_detections[det_idx]["is_correlated"] = True
            projected_detections[det_idx]["correlated_ais"] = {
                "mmsi": vessel.get("mmsi"),
                "vessel_name": vessel.get("vessel_name") or vessel.get("name"),
                "vessel_type": vessel.get("vessel_type") or vessel.get("type"),
                "imo": vessel.get("imo"),
                "callsign": vessel.get("callsign"),
                "speed": vessel.get("speed"),
                "heading": vessel.get("heading"),
                "latitude": vessel.get("latitude"),
                "longitude": vessel.get("longitude"),
                "distance_to_box_meters": round(dist_to_box, 1),
                "distance_to_center_meters": round(dist_to_center, 1),
                "match_type": match_status,
                "timestamp": (
                    vessel["timestamp"].isoformat()
                    if isinstance(vessel.get("timestamp"), datetime)
                    else vessel.get("timestamp")
                ),
                "source_plugin": vessel.get("source_plugin"),
                "vessel_id": vessel.get("vessel_id") or vessel.get("id"),
            }

        return projected_detections

    def _fetch_candidate_vessels(
        self,
        scan: Scan,
        tolerance_meters: float,
    ) -> list[dict[str, Any]]:
        """Fetch AIS vessel positions within or near the scan bounding box."""
        deg_margin = max(0.005, (tolerance_meters / 111000.0) * 1.5)

        expanded_bbox = BoundingBox(
            min_latitude=scan.bbox.min_latitude - deg_margin,
            max_latitude=scan.bbox.max_latitude + deg_margin,
            min_longitude=scan.bbox.min_longitude - deg_margin,
            max_longitude=scan.bbox.max_longitude + deg_margin,
        )

        time_range = None
        if scan.acquisition and getattr(scan.acquisition, "acquired_at", None):
            acq_time = scan.acquisition.acquired_at
            if isinstance(acq_time, datetime):
                start_dt = acq_time - timedelta(hours=2)
                end_dt = acq_time + timedelta(hours=2)
                time_range = (start_dt, end_dt)

        positions: list[dict[str, Any]] = []
        if time_range:
            try:
                positions = self._ais_repo.get_vessel_positions(
                    bbox=expanded_bbox,
                    time_range=time_range,
                    limit=2000,
                    latest_only=True,
                )
            except Exception:
                positions = []

        if not positions:
            try:
                positions = self._ais_repo.get_vessel_positions(
                    bbox=expanded_bbox,
                    limit=2000,
                    latest_only=True,
                )
            except Exception:
                positions = []

        return positions
