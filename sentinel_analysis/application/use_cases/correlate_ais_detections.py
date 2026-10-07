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


def point_in_polygon_and_distance(
    lat: float,
    lon: float,
    geo_polygon: list[tuple[float, float]],
) -> tuple[bool, float]:
    """Check if point is inside a polygon and calculate shortest distance in meters.

    Parameters:
        lat: WGS-84 latitude in degrees.
        lon: WGS-84 longitude in degrees.
        geo_polygon: Sequence of (lat, lon) vertices defining the closed boundary.

    Returns:
        tuple[bool, float]: (is_inside, distance_meters) where distance_meters == 0.0 if inside.
    """
    if not geo_polygon or len(geo_polygon) < 3:
        return False, float("inf")

    lat0, lon0 = geo_polygon[0]
    lat_rad = math.radians(lat0)
    cos_lat = max(0.01, math.cos(lat_rad))
    m_per_deg_lat = 111195.0
    m_per_deg_lon = 111195.0 * cos_lat

    poly_xy = [
        ((p_lon - lon0) * m_per_deg_lon, (p_lat - lat0) * m_per_deg_lat)
        for p_lat, p_lon in geo_polygon
    ]
    px = (lon - lon0) * m_per_deg_lon
    py = (lat - lat0) * m_per_deg_lat

    # 1. Point in polygon test via Ray Casting
    inside = False
    n = len(poly_xy)
    for i in range(n):
        x1, y1 = poly_xy[i]
        x2, y2 = poly_xy[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            x_int = (py - y1) * (x2 - x1) / (y2 - y1 + 1e-12) + x1
            if px < x_int:
                inside = not inside

    if inside:
        return True, 0.0

    # 2. Shortest distance to boundary segments
    min_dist_sq = float("inf")
    for i in range(n):
        x1, y1 = poly_xy[i]
        x2, y2 = poly_xy[(i + 1) % n]
        vx = x2 - x1
        vy = y2 - y1
        l2 = vx * vx + vy * vy
        if l2 <= 1e-9:
            d2 = (px - x1) ** 2 + (py - y1) ** 2
        else:
            t = max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / l2))
            proj_x = x1 + t * vx
            proj_y = y1 + t * vy
            d2 = (px - proj_x) ** 2 + (py - proj_y) ** 2
        if d2 < min_dist_sq:
            min_dist_sq = d2

    return False, math.sqrt(max(0.0, min_dist_sq))


def _parse_timestamp(val: Any) -> Optional[datetime]:
    """Parse string or datetime object into a UTC timezone-aware datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            cleaned = val.replace("Z", "+00:00").replace(" ", "T")
            dt = datetime.fromisoformat(cleaned)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def dead_reckon_position(
    lat: float,
    lon: float,
    speed_knots: Optional[float],
    heading_deg: Optional[float],
    time_delta_seconds: float,
    max_propagation_seconds: float = 7200.0,
) -> tuple[float, float, float]:
    """Extrapolate a vessel's geographic position along its SOG and COG/heading vector.

    Parameters:
        lat: Initial WGS-84 latitude in degrees.
        lon: Initial WGS-84 longitude in degrees.
        speed_knots: Speed Over Ground in knots (1 knot = 1852/3600 m/s ~ 0.514444 m/s).
        heading_deg: Heading or Course Over Ground in degrees [0, 360) clockwise from True North.
        time_delta_seconds: Propagation time interval (t_target - t_source).
                            Positive means predicting into the future;
                            negative means back-propagating into the past.
        max_propagation_seconds: Maximum allowable extrapolation window in seconds.

    Returns:
        tuple[float, float, float]: (projected_latitude, projected_longitude, distance_traveled_meters)
    """
    if (
        speed_knots is None
        or heading_deg is None
        or abs(time_delta_seconds) < 1.0
        or abs(time_delta_seconds) > max_propagation_seconds
    ):
        return lat, lon, 0.0

    try:
        speed = float(speed_knots)
        heading = float(heading_deg) % 360.0
    except (TypeError, ValueError):
        return lat, lon, 0.0

    # Sanity checks on physical vessel speeds (0.05 to 70 knots)
    if speed <= 0.05 or speed > 70.0:
        return lat, lon, 0.0

    speed_mps = speed * (1852.0 / 3600.0)
    distance_meters = speed_mps * time_delta_seconds

    theta = math.radians(heading)
    dy = distance_meters * math.cos(theta)  # displacement North (meters)
    dx = distance_meters * math.sin(theta)  # displacement East (meters)

    earth_radius = 6371000.0
    d_lat = math.degrees(dy / earth_radius)

    # Scale longitude displacement by mean latitude cosine
    mean_lat_rad = math.radians(lat + d_lat / 2.0)
    cos_lat = max(0.01, math.cos(mean_lat_rad))
    d_lon = math.degrees(dx / (earth_radius * cos_lat))

    new_lat = max(-90.0, min(90.0, lat + d_lat))
    new_lon = ((lon + d_lon + 180.0) % 360.0) - 180.0

    return new_lat, new_lon, abs(distance_meters)


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

    def get_kinematics_enabled(self) -> bool:
        """Check if AIS kinematic dead-reckoning is enabled, defaulting to True."""
        if self._settings_repo is not None:
            try:
                cv_settings = self._settings_repo.get_section("cv") or {}
                raw = cv_settings.get("ais_dead_reckoning_enabled")
                if raw is not None:
                    return bool(raw)
            except Exception:
                pass
        return True

    def get_max_dead_reckoning_seconds(self) -> float:
        """Fetch maximum dead-reckoning time window, defaulting to 7200s (2 hours)."""
        if self._settings_repo is not None:
            try:
                cv_settings = self._settings_repo.get_section("cv") or {}
                raw = cv_settings.get("ais_max_dead_reckoning_seconds")
                if raw is not None:
                    return max(60.0, float(raw))
            except Exception:
                pass
        return 7200.0

    def execute(
        self,
        detections: list[ShipDetection | dict[str, Any]],
        scan: Scan,
        image_width: int,
        image_height: int,
        *,
        tolerance_meters: Optional[float] = None,
        candidate_vessels: Optional[list[dict[str, Any]]] = None,
        enable_kinematics: Optional[bool] = None,
        max_propagation_seconds: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Perform proximity correlation on detections and return enriched detection dictionaries."""
        if tolerance_meters is None or tolerance_meters < 0.0:
            tolerance_meters = self.get_default_tolerance_meters()

        tolerance_meters = float(tolerance_meters)
        scan_bbox = scan.bbox
        img_w = max(1, int(image_width))
        img_h = max(1, int(image_height))

        if enable_kinematics is None:
            enable_kinematics = self.get_kinematics_enabled()
        if max_propagation_seconds is None or max_propagation_seconds <= 0.0:
            max_propagation_seconds = self.get_max_dead_reckoning_seconds()

        target_time: Optional[datetime] = None
        if scan.acquisition and getattr(scan.acquisition, "acquired_at", None):
            target_time = _parse_timestamp(scan.acquisition.acquired_at)

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

            geo_polygon = None
            if polygon_points and len(polygon_points) >= 3:
                geo_polygon = [
                    (
                        round(scan_bbox.max_latitude - float(pt[1]) * lat_scale, 7),
                        round(scan_bbox.min_longitude + float(pt[0]) * lon_scale, 7),
                    )
                    for pt in polygon_points
                ]

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
                "geo_polygon": geo_polygon,
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

        # 2. Pre-process candidates with kinematic dead-reckoning (if enabled)
        prepared_candidates: list[dict[str, Any]] = []
        for vessel in candidate_vessels:
            v_lat = vessel.get("latitude")
            v_lon = vessel.get("longitude")
            if v_lat is None or v_lon is None:
                continue

            try:
                base_lat = float(v_lat)
                base_lon = float(v_lon)
            except (TypeError, ValueError):
                continue

            eff_lat = base_lat
            eff_lon = base_lon
            delta_seconds = 0.0
            prop_dist = 0.0
            dead_reckoned = False

            if enable_kinematics and target_time is not None:
                v_time = _parse_timestamp(vessel.get("timestamp"))
                if v_time is not None:
                    dt = (target_time - v_time).total_seconds()
                    spd = vessel.get("speed") if vessel.get("speed") is not None else vessel.get("sog")
                    hdg = vessel.get("course") if vessel.get("course") is not None else vessel.get("cog")
                    if hdg is None:
                        hdg = vessel.get("heading")

                    proj_lat, proj_lon, p_dist = dead_reckon_position(
                        base_lat,
                        base_lon,
                        spd,
                        hdg,
                        dt,
                        max_propagation_seconds=max_propagation_seconds,
                    )
                    if p_dist > 0.0:
                        eff_lat = proj_lat
                        eff_lon = proj_lon
                        delta_seconds = dt
                        prop_dist = p_dist
                        dead_reckoned = True

            prepared_candidates.append({
                "vessel": vessel,
                "eff_lat": eff_lat,
                "eff_lon": eff_lon,
                "raw_lat": base_lat,
                "raw_lon": base_lon,
                "dead_reckoned": dead_reckoned,
                "delta_seconds": delta_seconds,
                "prop_dist": prop_dist,
            })

        # 3. Build candidate match pairs: (detection_index, cand_dict, dist_to_box, dist_to_center)
        candidate_matches: list[tuple[int, dict[str, Any], float, float]] = []

        for p_det in projected_detections:
            g_box = p_det["geo_bbox"]
            geo_poly = p_det.get("geo_polygon")
            det_idx = p_det["index"]
            c_lat = p_det["lat"]
            c_lon = p_det["lng"]

            for cand in prepared_candidates:
                if geo_poly:
                    _, dist_to_box = point_in_polygon_and_distance(
                        cand["eff_lat"],
                        cand["eff_lon"],
                        geo_poly,
                    )
                else:
                    dist_to_box = distance_to_bbox_meters(
                        cand["eff_lat"],
                        cand["eff_lon"],
                        g_box["min_lat"],
                        g_box["max_lat"],
                        g_box["min_lon"],
                        g_box["max_lon"],
                    )

                if dist_to_box <= tolerance_meters:
                    dist_to_center = haversine_distance_meters(cand["eff_lat"], cand["eff_lon"], c_lat, c_lon)
                    candidate_matches.append((det_idx, cand, dist_to_box, dist_to_center))

        # 4. Resolve conflicts: Greedy assignment by (dist_to_box, dist_to_center)
        candidate_matches.sort(key=lambda item: (item[2], item[3]))

        matched_detections: set[int] = set()
        matched_vessel_keys: set[str] = set()

        for det_idx, cand, dist_to_box, dist_to_center in candidate_matches:
            if det_idx in matched_detections:
                continue

            vessel = cand["vessel"]
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
                "latitude": round(cand["eff_lat"], 6),
                "longitude": round(cand["eff_lon"], 6),
                "raw_latitude": cand["raw_lat"],
                "raw_longitude": cand["raw_lon"],
                "dead_reckoned": cand["dead_reckoned"],
                "propagation_delta_seconds": round(cand["delta_seconds"], 1) if cand["dead_reckoned"] else 0.0,
                "propagated_distance_meters": round(cand["prop_dist"], 1),
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
        center_lat = (scan.bbox.min_latitude + scan.bbox.max_latitude) / 2.0
        lat_rad = math.radians(center_lat)
        cos_lat = max(0.1, math.cos(lat_rad))
        lat_margin = max(0.01, (tolerance_meters / 111320.0) * 1.5)
        lon_margin = max(0.01, lat_margin / cos_lat)

        expanded_bbox = BoundingBox(
            min_latitude=max(-90.0, scan.bbox.min_latitude - lat_margin),
            max_latitude=min(90.0, scan.bbox.max_latitude + lat_margin),
            min_longitude=max(-180.0, scan.bbox.min_longitude - lon_margin),
            max_longitude=min(180.0, scan.bbox.max_longitude + lon_margin),
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
