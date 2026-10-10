"""Use case to correlate SAR Computer Vision ship detections with AIS vessel data."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.domain.entities import BoundingBox, Scan, ShipDetection
from sentinel_analysis.domain.uncertainty import (
    DimensionUncertainty,
    SpatialUncertainty,
    calculate_association_likelihood,
    calculate_competing_candidate_probabilities,
    calculate_mahalanobis_distance,
    calibrate_dimension_uncertainty,
    calibrate_spatial_uncertainty,
    compute_joint_covariance_ellipse,
    generate_contact_reason_codes,
)


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


def compute_uncertainty_ellipse(
    delta_seconds: float,
    speed_knots: float | None = None,
    heading_deg: float | None = None,
    is_interpolated: bool = False,
    interpolation_ratio: float = 0.5,
) -> dict[str, float]:
    """Calculate 95% confidence positional uncertainty ellipse (semi_major_m, semi_minor_m, orientation_deg).

    Models error propagation for kinematic AIS tracks:
    - Base GNSS positioning error (sigma_pos ~ 7.5m)
    - Speed Over Ground uncertainty (sigma_spd ~ 0.5 kn = 0.257 m/s)
    - Course Over Ground uncertainty (sigma_course ~ 3.0 deg = 0.052 rad)
    - Maneuver dynamic acceleration noise (sigma_acc ~ 0.015 m/s^2)
    """
    sigma_pos = 7.5
    sigma_spd = 0.257
    sigma_course = 0.052
    sigma_acc = 0.015

    speed = max(0.1, float(speed_knots)) if speed_knots is not None else 8.0
    speed_mps = speed * (1852.0 / 3600.0)
    orientation = float(heading_deg) % 360.0 if heading_deg is not None else 0.0

    abs_dt = abs(float(delta_seconds))

    if is_interpolated:
        # Hermite bridge interpolation uncertainty: variance is reduced because both ends are fixed.
        # Max standard deviation is at midpoint u=0.5 (sqrt(u*(1-u)) = 0.5), collapsing to GNSS base at endpoints u=0, 1.
        u = max(0.0, min(1.0, float(interpolation_ratio)))
        eff_dt = abs_dt * math.sqrt(u * (1.0 - u))
    else:
        eff_dt = abs_dt

    sigma_along = math.sqrt(
        sigma_pos**2
        + (sigma_spd * eff_dt) ** 2
        + 0.25 * (sigma_acc * (eff_dt**2)) ** 2
    )
    sigma_cross = math.sqrt(
        sigma_pos**2
        + (speed_mps * sigma_course * eff_dt) ** 2
        + 0.25 * (sigma_acc * (eff_dt**2)) ** 2
    )

    # 95% Confidence interval (chi-squared 2-DOF scale factor ~ 2.447)
    semi_major = max(10.0, 2.447 * max(sigma_along, sigma_cross))
    semi_minor = max(5.0, 2.447 * min(sigma_along, sigma_cross))

    return {
        "semi_major_m": round(semi_major, 1),
        "semi_minor_m": round(semi_minor, 1),
        "orientation_deg": round(orientation, 1),
        "sigma_along": round(sigma_along, 2),
        "sigma_cross": round(sigma_cross, 2),
    }


def is_point_in_ellipse(
    lat: float,
    lon: float,
    center_lat: float,
    center_lon: float,
    semi_major_m: float,
    semi_minor_m: float,
    orientation_deg: float,
) -> tuple[bool, float]:
    """Test if a geographic point falls inside an uncertainty ellipse oriented along orientation_deg.

    Returns:
        tuple[bool, float]: (is_inside, normalized_mahalanobis_distance)
    """
    # Metric offsets (dx = East, dy = North)
    mean_lat_rad = math.radians((lat + center_lat) / 2.0)
    cos_lat = max(0.01, math.cos(mean_lat_rad))
    dy = (lat - center_lat) * 111320.0
    dx = (lon - center_lon) * 111320.0 * cos_lat

    # Ellipse orientation theta is clockwise from True North (dy-axis)
    theta = math.radians(orientation_deg % 360.0)
    # Along-track (parallel to heading) and cross-track (perpendicular)
    along_track = dy * math.cos(theta) + dx * math.sin(theta)
    cross_track = -dy * math.sin(theta) + dx * math.cos(theta)

    a = max(1.0, float(semi_major_m))
    b = max(1.0, float(semi_minor_m))

    d2 = (along_track / a) ** 2 + (cross_track / b) ** 2
    dist = math.sqrt(d2)
    return dist <= 1.0, dist


def interpolate_kinematic_track(
    records: list[dict[str, Any]],
    target_time: datetime,
    max_gap_seconds: float = 7200.0,
) -> dict[str, Any]:
    """Perform non-linear kinematic track interpolation (Cubic Hermite Spline) to target timestamp.

    If target_time falls between two chronological reports, applies a Cubic Hermite Spline using
    instantaneous velocity vectors (SOG, COG), guaranteeing C1 continuity.
    If target_time falls outside the bracket, applies non-linear kinematic extrapolation with
    acceleration and turn rate estimation.
    Computes 95% covariance uncertainty ellipse for the interpolated state.
    """
    if not records:
        raise ValueError("Cannot interpolate empty AIS track")

    # Filter and parse valid positions
    valid_points: list[tuple[datetime, dict[str, Any]]] = []
    for r in records:
        lat = r.get("latitude")
        lon = r.get("longitude")
        ts = _parse_timestamp(r.get("timestamp"))
        if lat is not None and lon is not None and ts is not None:
            try:
                valid_points.append((ts, r))
            except Exception:
                continue

    if not valid_points:
        first = records[0]
        return {
            "latitude": float(first.get("latitude", 0.0)),
            "longitude": float(first.get("longitude", 0.0)),
            "speed": first.get("speed"),
            "heading": first.get("heading"),
            "raw_latitude": float(first.get("latitude", 0.0)),
            "raw_longitude": float(first.get("longitude", 0.0)),
            "dead_reckoned": False,
            "delta_seconds": 0.0,
            "propagated_distance_meters": 0.0,
            "uncertainty_ellipse": compute_uncertainty_ellipse(0.0),
            "method": "static",
            "track_points_count": 1,
            "representative_record": first,
        }

    # Sort chronologically
    valid_points.sort(key=lambda item: item[0])

    # If target time is within 1s of a point, return it directly
    for ts, r in valid_points:
        if abs((target_time - ts).total_seconds()) < 1.0:
            spd = r.get("speed") if r.get("speed") is not None else r.get("sog")
            hdg = r.get("heading") if r.get("heading") is not None else r.get("course")
            return {
                "latitude": float(r["latitude"]),
                "longitude": float(r["longitude"]),
                "speed": float(spd) if spd is not None else None,
                "heading": float(hdg) if hdg is not None else None,
                "raw_latitude": float(r["latitude"]),
                "raw_longitude": float(r["longitude"]),
                "dead_reckoned": False,
                "delta_seconds": 0.0,
                "propagated_distance_meters": 0.0,
                "uncertainty_ellipse": compute_uncertainty_ellipse(0.0, spd, hdg),
                "method": "exact_match",
                "track_points_count": len(valid_points),
                "representative_record": r,
            }

    # Case 1: Interpolation between two points (t0 <= target_time <= t1)
    for i in range(len(valid_points) - 1):
        t0, r0 = valid_points[i]
        t1, r1 = valid_points[i + 1]
        if t0 <= target_time <= t1:
            total_dt = (t1 - t0).total_seconds()
            if total_dt > max_gap_seconds or total_dt <= 1.0:
                break

            u = (target_time - t0).total_seconds() / total_dt

            # Hermite cubic basis
            h00 = 2 * (u**3) - 3 * (u**2) + 1
            h10 = (u**3) - 2 * (u**2) + u
            h01 = -2 * (u**3) + 3 * (u**2)
            h11 = (u**3) - (u**2)

            lat0, lon0 = float(r0["latitude"]), float(r0["longitude"])
            lat1, lon1 = float(r1["latitude"]), float(r1["longitude"])

            mean_lat = math.radians((lat0 + lat1) / 2.0)
            cos_lat = max(0.01, math.cos(mean_lat))
            m_lat = 111320.0
            m_lon = 111320.0 * cos_lat

            # Metric displacement from P0 to P1
            dx = (lon1 - lon0) * m_lon
            dy = (lat1 - lat0) * m_lat

            # SOG & COG at ends
            s0 = float(r0.get("speed") if r0.get("speed") is not None else r0.get("sog") or 0.0)
            s1 = float(r1.get("speed") if r1.get("speed") is not None else r1.get("sog") or 0.0)
            c0 = float(r0.get("heading") if r0.get("heading") is not None else r0.get("course") or 0.0)
            c1 = float(r1.get("heading") if r1.get("heading") is not None else r1.get("course") or 0.0)

            # Velocities in m/s (East, North)
            v0_mps = s0 * (1852.0 / 3600.0)
            v1_mps = s1 * (1852.0 / 3600.0)

            v0x = v0_mps * math.sin(math.radians(c0)) if s0 > 0.1 else dx / total_dt
            v0y = v0_mps * math.cos(math.radians(c0)) if s0 > 0.1 else dy / total_dt
            v1x = v1_mps * math.sin(math.radians(c1)) if s1 > 0.1 else dx / total_dt
            v1y = v1_mps * math.cos(math.radians(c1)) if s1 > 0.1 else dy / total_dt

            # Hermite metric positions
            interp_x = h10 * (total_dt * v0x) + h01 * dx + h11 * (total_dt * v1x)
            interp_y = h10 * (total_dt * v0y) + h01 * dy + h11 * (total_dt * v1y)

            interp_lat = lat0 + interp_y / m_lat
            interp_lon = lon0 + interp_x / m_lon

            # Derivative velocities for instantaneous speed and heading
            dh10 = 3 * (u**2) - 4 * u + 1
            dh01 = -6 * (u**2) + 6 * u
            dh11 = 3 * (u**2) - 2 * u

            inst_vx = (dh10 * (total_dt * v0x) + dh01 * dx + dh11 * (total_dt * v1x)) / total_dt
            inst_vy = (dh10 * (total_dt * v0y) + dh01 * dy + dh11 * (total_dt * v1y)) / total_dt

            inst_spd_mps = math.hypot(inst_vx, inst_vy)
            inst_spd_knots = inst_spd_mps * (3600.0 / 1852.0)
            inst_course = (math.degrees(math.atan2(inst_vx, inst_vy)) + 360.0) % 360.0

            prop_dist = math.hypot(interp_x, interp_y)
            dt_from_t0 = (target_time - t0).total_seconds()

            ellipse = compute_uncertainty_ellipse(
                delta_seconds=total_dt,
                speed_knots=inst_spd_knots,
                heading_deg=inst_course,
                is_interpolated=True,
                interpolation_ratio=u,
            )

            return {
                "latitude": round(interp_lat, 7),
                "longitude": round(interp_lon, 7),
                "speed": round(inst_spd_knots, 1),
                "heading": round(inst_course, 1),
                "raw_latitude": lat0,
                "raw_longitude": lon0,
                "dead_reckoned": True,
                "delta_seconds": round(dt_from_t0, 1),
                "propagated_distance_meters": round(prop_dist, 1),
                "uncertainty_ellipse": ellipse,
                "method": "cubic_hermite_spline",
                "track_points_count": len(valid_points),
                "representative_record": r1 if u >= 0.5 else r0,
            }

    # Case 2: Extrapolation from anchor point (before first point or after last point)
    if target_time > valid_points[-1][0]:
        anchor_ts, anchor_r = valid_points[-1]
        prior_point = valid_points[-2] if len(valid_points) >= 2 else None
    else:
        anchor_ts, anchor_r = valid_points[0]
        prior_point = valid_points[1] if len(valid_points) >= 2 else None

    dt = (target_time - anchor_ts).total_seconds()
    spd = anchor_r.get("speed") if anchor_r.get("speed") is not None else anchor_r.get("sog")
    hdg = anchor_r.get("heading") if anchor_r.get("heading") is not None else anchor_r.get("course")

    method = "dead_reckoning"
    eff_spd = float(spd) if spd is not None else None
    eff_hdg = float(hdg) if hdg is not None else None

    if prior_point is not None and eff_spd is not None and eff_hdg is not None:
        p_ts, p_r = prior_point
        prior_spd = float(p_r.get("speed") if p_r.get("speed") is not None else p_r.get("sog") or eff_spd)
        prior_hdg = float(p_r.get("heading") if p_r.get("heading") is not None else p_r.get("course") or eff_hdg)
        p_dt = (anchor_ts - p_ts).total_seconds()
        if abs(p_dt) > 5.0:
            acc = (eff_spd - prior_spd) / p_dt
            turn = (((eff_hdg - prior_hdg + 180.0) % 360.0) - 180.0) / p_dt
            acc = max(-0.05, min(0.05, acc))
            turn = max(-3.0, min(3.0, turn))
            eff_spd = max(0.0, min(60.0, eff_spd + 0.5 * acc * dt))
            eff_hdg = (eff_hdg + 0.5 * turn * dt) % 360.0
            method = "kinematic_extrapolation"

    base_lat = float(anchor_r["latitude"])
    base_lon = float(anchor_r["longitude"])

    proj_lat, proj_lon, prop_dist = dead_reckon_position(
        base_lat,
        base_lon,
        eff_spd,
        eff_hdg,
        dt,
        max_propagation_seconds=max_gap_seconds,
    )

    ellipse = compute_uncertainty_ellipse(
        delta_seconds=dt,
        speed_knots=eff_spd,
        heading_deg=eff_hdg,
        is_interpolated=False,
    )

    return {
        "latitude": round(proj_lat, 7),
        "longitude": round(proj_lon, 7),
        "speed": round(eff_spd, 1) if eff_spd is not None else None,
        "heading": round(eff_hdg, 1) if eff_hdg is not None else None,
        "raw_latitude": base_lat,
        "raw_longitude": base_lon,
        "dead_reckoned": bool(prop_dist > 0.0),
        "delta_seconds": round(dt, 1),
        "propagated_distance_meters": round(prop_dist, 1),
        "uncertainty_ellipse": ellipse,
        "method": method,
        "track_points_count": len(valid_points),
        "representative_record": anchor_r,
    }


def assess_dark_vessel(

    is_correlated: Any = False,
    length: float | None = None,
    beam: float | None = None,
    confidence: float | None = None,
    solas_threshold: float = 30.0,
    critical_threshold: float = 100.0,
) -> dict[str, Any]:
    """Evaluate whether an uncorrelated detection represents a potential Dark Vessel.

    Incorporates IMO SOLAS Class-A mandatory carriage thresholds (typically >= 30-35m / 300 GT),
    vessel hull aspect ratio (naval architecture plausibility), and detection confidence.
    Supports either passing a detection dictionary/object directly or passing individual attributes.
    """
    if isinstance(is_correlated, dict):
        det_dict = is_correlated
        is_corr = bool(det_dict.get("is_correlated", False))
        length = det_dict.get("length", length)
        beam = det_dict.get("beam", beam)
        confidence = det_dict.get("confidence", confidence)
    elif not isinstance(is_correlated, bool) and hasattr(is_correlated, "is_correlated"):
        is_corr = bool(getattr(is_correlated, "is_correlated", False))
        length = getattr(is_correlated, "length", length)
        beam = getattr(is_correlated, "beam", beam)
        confidence = getattr(is_correlated, "confidence", confidence)
    else:
        is_corr = bool(is_correlated)

    if is_corr:
        return {
            "is_dark_vessel": False,
            "dark_vessel_score": 0.0,
            "dark_vessel_risk": "NOMINAL",
            "estimated_class": "Identified Vessel",
            "dark_vessel_reasons": ["Correlated with active AIS broadcast"],
        }

    conf = max(0.1, min(1.0, float(confidence if confidence is not None else 0.6)))
    est_len = float(length) if length is not None and length > 0 else 15.0
    est_beam = float(beam) if beam is not None and beam > 0 else max(3.0, est_len / 4.5)
    aspect_ratio = est_len / max(1.0, est_beam)

    reasons: list[str] = ["Uncorrelated with active AIS broadcasts"]

    if est_len >= critical_threshold:
        base_score = 0.85
        est_class = "Large Commercial / Cargo / Tanker"
        reasons.append(
            f"Estimated length ({est_len:.0f}m) exceeds {critical_threshold:.0f}m large ship threshold; Class-A AIS legally mandated under SOLAS"
        )
    elif est_len >= solas_threshold:
        base_score = 0.70
        est_class = "Commercial Vessel (SOLAS Mandated)"
        reasons.append(
            f"Estimated length ({est_len:.0f}m) exceeds {solas_threshold:.0f}m mandatory AIS carriage threshold"
        )
    elif est_len >= 18.0:
        base_score = 0.45
        est_class = "Medium Vessel / Trawler"
        reasons.append(
            f"Estimated length ({est_len:.0f}m) indicates medium craft or commercial fishing vessel"
        )
    else:
        base_score = 0.20
        est_class = "Small Craft or Radar Clutter"
        reasons.append(
            f"Estimated length ({est_len:.0f}m) is below mandatory AIS carriage threshold"
        )

    aspect_adj = 0.0
    if 2.5 <= aspect_ratio <= 9.0:
        aspect_adj = 0.08
        reasons.append(f"Hull aspect ratio ({aspect_ratio:.1f}) is characteristic of a naval vessel profile")
    elif aspect_ratio < 1.6:
        aspect_adj = -0.12
        reasons.append(f"Low hull aspect ratio ({aspect_ratio:.1f}) suggests circular radar clutter or buoy")

    score = (base_score * (0.6 + 0.4 * conf)) + aspect_adj
    score = max(0.05, min(0.99, score))

    if score >= 0.75:
        risk_level = "CRITICAL"
    elif score >= 0.55:
        risk_level = "HIGH"
    elif score >= 0.35:
        risk_level = "MEDIUM"
    elif score >= 0.20:
        risk_level = "LOW"
    else:
        risk_level = "NOMINAL"

    is_dark = (score >= 0.50) or (est_len >= solas_threshold and conf >= 0.45)

    return {
        "is_dark_vessel": is_dark,
        "dark_vessel_score": round(score, 2),
        "dark_vessel_risk": risk_level,
        "estimated_class": est_class,
        "dark_vessel_reasons": reasons,
    }


def extract_ghost_vessels(
    prepared_candidates: list[dict[str, Any]],
    matched_vessel_keys: set[str],
    bbox: BoundingBox,
) -> list[dict[str, Any]]:
    """Identify candidate AIS vessels broadcasting inside the SAR footprint with no radar match."""
    ghosts: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for cand in prepared_candidates:
        if "vessel" in cand:
            vessel = cand["vessel"]
            lat = cand.get("eff_lat", vessel.get("latitude"))
            lon = cand.get("eff_lon", vessel.get("longitude"))
            dead_reck = cand.get("dead_reckoned", False)
        else:
            vessel = cand
            lat = vessel.get("latitude")
            lon = vessel.get("longitude")
            dead_reck = vessel.get("dead_reckoned", False)

        if lat is None or lon is None:
            continue

        v_key = str(vessel.get("mmsi") or vessel.get("vessel_id") or id(vessel))
        if v_key in matched_vessel_keys or v_key in seen_keys:
            continue
        if (
            bbox.min_latitude <= lat <= bbox.max_latitude
            and bbox.min_longitude <= lon <= bbox.max_longitude
        ):
            seen_keys.add(v_key)
            ghosts.append({
                "mmsi": vessel.get("mmsi"),
                "vessel_name": vessel.get("vessel_name") or vessel.get("name"),
                "vessel_type": vessel.get("vessel_type") or vessel.get("type"),
                "callsign": vessel.get("callsign"),
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "speed": vessel.get("speed") if vessel.get("speed") is not None else vessel.get("sog"),
                "heading": vessel.get("heading") if vessel.get("heading") is not None else vessel.get("course"),
                "dead_reckoned": dead_reck,
                "anomaly_type": "GHOST_VESSEL",
                "reason": "AIS broadcast present within SAR footprint but no corresponding radar detection found (potential AIS spoofing or phantom vessel)",
            })
    return ghosts


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
        self.last_ghost_vessels: list[dict[str, Any]] = []
        self.last_dark_vessels: list[dict[str, Any]] = []

    def get_dark_vessel_thresholds(self) -> tuple[float, float]:
        """Fetch configured dark vessel length thresholds from settings (SOLAS and Critical)."""
        solas_thresh = 30.0
        crit_thresh = 100.0
        if self._settings_repo is not None:
            try:
                cv_settings = self._settings_repo.get_section("cv") or {}
                s_val = cv_settings.get("dark_vessel_solas_length_threshold")
                c_val = cv_settings.get("dark_vessel_critical_length_threshold")
                if s_val is not None:
                    solas_thresh = max(5.0, float(s_val))
                if c_val is not None:
                    crit_thresh = max(solas_thresh, float(c_val))
            except Exception:
                pass
        return solas_thresh, crit_thresh

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
                wake_detected = det.get("wake_detected")
                wake_heading = det.get("wake_heading")
                wake_speed_knots = det.get("wake_speed_knots")
                wake_confidence = det.get("wake_confidence")
                v_class = det.get("vessel_class")
                class_conf = det.get("classification_confidence")
                det_prov = det.get("provenance")
                opt_status = det.get("optical_status")
                opt_conf = det.get("optical_confirmed")
                opt_conf_score = det.get("optical_confidence")
                temp_change = det.get("temporal_change_type")
                sp_unc = det.get("spatial_uncertainty")
                dim_unc = det.get("dimension_uncertainty")
                assoc_lh = det.get("association_likelihood")
                r_codes = det.get("reason_codes")
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
                wake_detected = getattr(det, "wake_detected", None)
                wake_heading = getattr(det, "wake_heading", None)
                wake_speed_knots = getattr(det, "wake_speed_knots", None)
                wake_confidence = getattr(det, "wake_confidence", None)
                v_class = getattr(det, "vessel_class", None)
                class_conf = getattr(det, "classification_confidence", None)
                det_prov = getattr(det, "provenance", None)
                opt_status = getattr(det, "optical_status", None)
                opt_conf = getattr(det, "optical_confirmed", None)
                opt_conf_score = getattr(det, "optical_confidence", None)
                temp_change = getattr(det, "temporal_change_type", None)
                sp_unc = getattr(det, "spatial_uncertainty", None)
                dim_unc = getattr(det, "dimension_uncertainty", None)
                assoc_lh = getattr(det, "association_likelihood", None)
                r_codes = getattr(det, "reason_codes", None)

            if sp_unc is not None and hasattr(sp_unc, "to_dict"):
                sp_unc_dict = sp_unc.to_dict()
            elif isinstance(sp_unc, dict):
                sp_unc_dict = dict(sp_unc)
            else:
                sp_unc_dict = calibrate_spatial_uncertainty().to_dict()

            if dim_unc is not None and hasattr(dim_unc, "to_dict"):
                dim_unc_dict = dim_unc.to_dict()
            elif isinstance(dim_unc, dict):
                dim_unc_dict = dict(dim_unc)
            else:
                dim_unc_dict = calibrate_dimension_uncertainty(
                    length_m=length, beam_m=beam, heading_deg=angle
                ).to_dict()

            if r_codes is not None:
                r_codes_list = list(r_codes)
            else:
                r_codes_list = generate_contact_reason_codes(
                    is_correlated=False,
                    is_inside_box=False,
                    association_likelihood=0.0,
                    wake_detected=bool(wake_detected),
                    confidence=conf,
                )

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
                "wake_detected": wake_detected,
                "wake_heading": wake_heading,
                "wake_speed_knots": wake_speed_knots,
                "wake_confidence": wake_confidence,
                "is_speed_spoofed": None,
                "is_course_spoofed": None,
                "speed_discrepancy_knots": None,
                "heading_discrepancy_deg": None,
                "correlation_status": "uncorrelated",
                "is_correlated": False,
                "correlated_ais": None,
                "vessel_class": v_class,
                "classification_confidence": class_conf,
                "provenance": det_prov,
                "optical_status": opt_status,
                "optical_confirmed": opt_conf,
                "optical_confidence": opt_conf_score,
                "temporal_change_type": temp_change,
                "spatial_uncertainty": sp_unc_dict,
                "dimension_uncertainty": dim_unc_dict,
                "association_likelihood": assoc_lh,
                "reason_codes": r_codes_list,
            })

        # 2. Pre-process candidates with kinematic trajectory interpolation / dead-reckoning
        vessel_groups: dict[str, list[dict[str, Any]]] = {}
        for vessel in candidate_vessels:
            v_lat = vessel.get("latitude")
            v_lon = vessel.get("longitude")
            if v_lat is None or v_lon is None:
                continue
            v_key = str(vessel.get("mmsi") or vessel.get("vessel_id") or id(vessel))
            vessel_groups.setdefault(v_key, []).append(vessel)

        prepared_candidates: list[dict[str, Any]] = []
        for v_key, records in vessel_groups.items():
            if enable_kinematics and target_time is not None:
                try:
                    interp_res = interpolate_kinematic_track(
                        records,
                        target_time,
                        max_gap_seconds=max_propagation_seconds,
                    )
                    eff_lat = interp_res["latitude"]
                    eff_lon = interp_res["longitude"]
                    raw_lat = interp_res["raw_latitude"]
                    raw_lon = interp_res["raw_longitude"]
                    dead_reckoned = interp_res["dead_reckoned"]
                    delta_seconds = interp_res["delta_seconds"]
                    prop_dist = interp_res["propagated_distance_meters"]
                    ellipse = interp_res.get("uncertainty_ellipse")
                    method = interp_res.get("method", "dead_reckoning")
                    rep_record = interp_res.get("representative_record") or records[-1]
                except Exception:
                    rep_record = records[-1]
                    eff_lat = float(rep_record.get("latitude", 0.0))
                    eff_lon = float(rep_record.get("longitude", 0.0))
                    raw_lat = eff_lat
                    raw_lon = eff_lon
                    dead_reckoned = False
                    delta_seconds = 0.0
                    prop_dist = 0.0
                    ellipse = compute_uncertainty_ellipse(0.0)
                    method = "fallback"
            else:
                rep_record = records[-1]
                try:
                    eff_lat = float(rep_record.get("latitude", 0.0))
                    eff_lon = float(rep_record.get("longitude", 0.0))
                except (TypeError, ValueError):
                    continue
                raw_lat = eff_lat
                raw_lon = eff_lon
                dead_reckoned = False
                delta_seconds = 0.0
                prop_dist = 0.0
                ellipse = compute_uncertainty_ellipse(0.0)
                method = "static"

            prepared_candidates.append({
                "vessel": rep_record,
                "all_records": records,
                "eff_lat": eff_lat,
                "eff_lon": eff_lon,
                "raw_lat": raw_lat,
                "raw_lon": raw_lon,
                "dead_reckoned": dead_reckoned,
                "delta_seconds": delta_seconds,
                "prop_dist": prop_dist,
                "uncertainty_ellipse": ellipse,
                "interpolation_method": method,
            })

        # 3. Build candidate match evaluation: (det_idx, cand, dist_to_box, dist_to_center, m_dist, likelihood, joint_spatial)
        raw_candidate_pairs: list[dict[str, Any]] = []
        det_candidate_map: dict[int, list[dict[str, Any]]] = {d["index"]: [] for d in projected_detections}
        vessel_candidate_map: dict[str, list[dict[str, Any]]] = {}

        for p_det in projected_detections:
            g_box = p_det["geo_bbox"]
            geo_poly = p_det.get("geo_polygon")
            det_idx = p_det["index"]
            c_lat = p_det["lat"]
            c_lon = p_det["lng"]
            radar_sp = p_det.get("spatial_uncertainty") or {}

            for cand in prepared_candidates:
                vessel = cand["vessel"]
                v_key = str(vessel.get("mmsi") or vessel.get("vessel_id") or id(vessel))
                cand_lat = cand["eff_lat"]
                cand_lon = cand["eff_lon"]

                if geo_poly:
                    _, dist_to_box = point_in_polygon_and_distance(
                        cand_lat,
                        cand_lon,
                        geo_poly,
                    )
                else:
                    dist_to_box = distance_to_bbox_meters(
                        cand_lat,
                        cand_lon,
                        g_box["min_lat"],
                        g_box["max_lat"],
                        g_box["min_lon"],
                        g_box["max_lon"],
                    )

                dist_to_center = haversine_distance_meters(cand_lat, cand_lon, c_lat, c_lon)

                # Metric displacement (dx=East, dy=North) from radar centroid to candidate
                mean_lat_rad = math.radians((cand_lat + c_lat) / 2.0)
                cos_lat = max(0.01, math.cos(mean_lat_rad))
                dy_m = (cand_lat - c_lat) * 111320.0
                dx_m = (cand_lon - c_lon) * 111320.0 * cos_lat

                # Joint covariance error ellipse
                ais_ellipse = cand.get("uncertainty_ellipse") or {}
                joint_spatial = compute_joint_covariance_ellipse(radar_sp, ais_ellipse)

                m_dist = calculate_mahalanobis_distance(
                    dx_meters=dx_m,
                    dy_meters=dy_m,
                    semi_major_m=joint_spatial.semi_major_axis_meters,
                    semi_minor_m=joint_spatial.semi_minor_axis_meters,
                    orientation_deg=joint_spatial.orientation_deg,
                )

                inside_ellipse = bool(m_dist <= 1.0)
                is_gated = (dist_to_box <= tolerance_meters) or inside_ellipse or (m_dist <= 2.8)

                if is_gated:
                    # Kinematic and dimensional agreement
                    ais_spd = vessel.get("speed") if vessel.get("speed") is not None else vessel.get("sog")
                    ais_hdg = vessel.get("heading") if vessel.get("heading") is not None else vessel.get("course")
                    if ais_hdg is None:
                        ais_hdg = vessel.get("cog")

                    ais_len = vessel.get("length")
                    if ais_len is None:
                        to_b = vessel.get("to_bow") or vessel.get("dimension_to_bow")
                        to_s = vessel.get("to_stern") or vessel.get("dimension_to_stern")
                        if to_b is not None and to_s is not None:
                            try:
                                ais_len = float(to_b) + float(to_s)
                            except (ValueError, TypeError):
                                pass

                    match_status = "inside_box" if dist_to_box == 0.0 else "outside_box"
                    raw_likelihood = calculate_association_likelihood(
                        dist_to_box_meters=dist_to_box,
                        tolerance_meters=tolerance_meters,
                        is_inside_box=(match_status == "inside_box"),
                        sar_speed_knots=p_det.get("wake_speed_knots"),
                        ais_speed_knots=ais_spd,
                        sar_heading_deg=p_det.get("wake_heading") or p_det.get("angle"),
                        ais_heading_deg=ais_hdg,
                        sar_length_m=p_det.get("length"),
                        ais_length_m=ais_len,
                        mahalanobis_distance=m_dist,
                    )

                    # Track continuity decay over extrapolation interval
                    dt_abs = abs(float(cand.get("delta_seconds", 0.0)))
                    f_continuity = math.exp(-0.5 * (min(dt_abs, 7200.0) / 3600.0) ** 2)
                    joint_likelihood = round(max(0.05, min(0.99, raw_likelihood * (0.85 + 0.15 * f_continuity))), 3)

                    pair_info = {
                        "det_idx": det_idx,
                        "cand": cand,
                        "v_key": v_key,
                        "dist_to_box": dist_to_box,
                        "dist_to_center": dist_to_center,
                        "m_dist": m_dist,
                        "match_status": match_status,
                        "likelihood": joint_likelihood,
                        "joint_spatial": joint_spatial,
                        "ais_speed": ais_spd,
                        "ais_heading": ais_hdg,
                        "ais_length": ais_len,
                    }
                    raw_candidate_pairs.append(pair_info)
                    det_candidate_map[det_idx].append(pair_info)
                    vessel_candidate_map.setdefault(v_key, []).append(pair_info)

        # 4. Competing-candidate probability normalization (Bayesian multi-hypothesis scoring)
        # Compute detection-side competition probabilities
        for det_idx, pairs in det_candidate_map.items():
            if not pairs:
                continue
            lh_map = {f"cand_{i}": p["likelihood"] for i, p in enumerate(pairs)}
            prob_map = calculate_competing_candidate_probabilities(lh_map, null_hypothesis_likelihood=0.08)
            for i, p in enumerate(pairs):
                p["det_prob"] = prob_map.get(f"cand_{i}", p["likelihood"])

        # Compute vessel-side competition probabilities
        for v_key, pairs in vessel_candidate_map.items():
            if not pairs:
                continue
            lh_map = {f"det_{p['det_idx']}": p["likelihood"] for p in pairs}
            prob_map = calculate_competing_candidate_probabilities(lh_map, null_hypothesis_likelihood=0.08)
            for p in pairs:
                p["vessel_prob"] = prob_map.get(f"det_{p['det_idx']}", p["likelihood"])

        # Symmetric competing-candidate association probability
        for p in raw_candidate_pairs:
            d_p = p.get("det_prob", p["likelihood"])
            v_p = p.get("vessel_prob", p["likelihood"])
            p["assoc_prob"] = round(math.sqrt(max(0.001, d_p * v_p)), 3)

        # Attach candidate list to detections for explainability and full analyst observability
        for det_idx, pairs in det_candidate_map.items():
            comp_list = []
            for p in pairs:
                v = p["cand"]["vessel"]
                comp_list.append({
                    "mmsi": v.get("mmsi"),
                    "vessel_name": v.get("vessel_name") or v.get("name"),
                    "distance_to_box_meters": round(p["dist_to_box"], 1),
                    "distance_to_center_meters": round(p["dist_to_center"], 1),
                    "mahalanobis_distance": round(p["m_dist"], 2),
                    "association_likelihood": p["likelihood"],
                    "association_probability": p["assoc_prob"],
                })
            comp_list.sort(key=lambda item: item["association_probability"], reverse=True)
            projected_detections[det_idx]["competing_candidates"] = comp_list

        # 5. Global assignment: Maximize joint probability & likelihood, prioritizing inside_box matches
        raw_candidate_pairs.sort(
            key=lambda item: (
                1 if item["match_status"] == "inside_box" else 0,
                item["assoc_prob"],
                item["likelihood"],
                -item["dist_to_box"],
                -item["dist_to_center"],
            ),
            reverse=True,
        )

        matched_detections: set[int] = set()
        matched_vessel_keys: set[str] = set()

        for item in raw_candidate_pairs:
            det_idx = item["det_idx"]
            v_key = item["v_key"]
            if det_idx in matched_detections or v_key in matched_vessel_keys:
                continue

            cand = item["cand"]
            vessel = cand["vessel"]
            dist_to_box = item["dist_to_box"]
            dist_to_center = item["dist_to_center"]
            match_status = item["match_status"]
            joint_spatial = item["joint_spatial"]
            assoc_likelihood = item["likelihood"]
            assoc_prob = item["assoc_prob"]
            m_dist = item["m_dist"]

            # AIS Speed & Heading Spoofing Cross-Validation
            is_speed_spoofed = None
            is_course_spoofed = None
            speed_discrepancy_knots = None
            heading_discrepancy_deg = None

            wake_spd = projected_detections[det_idx].get("wake_speed_knots")
            wake_hdg = projected_detections[det_idx].get("wake_heading")
            ais_spd = item["ais_speed"]
            ais_hdg = item["ais_heading"]

            if wake_spd is not None and ais_spd is not None:
                try:
                    s_diff = abs(float(wake_spd) - float(ais_spd))
                    speed_discrepancy_knots = round(s_diff, 1)
                    is_speed_spoofed = bool(speed_discrepancy_knots >= 4.0)
                except (TypeError, ValueError):
                    pass

            if wake_hdg is not None and ais_hdg is not None:
                try:
                    h_diff = abs(float(wake_hdg) - float(ais_hdg)) % 360.0
                    heading_discrepancy_deg = round(min(h_diff, 360.0 - h_diff), 1)
                    is_course_spoofed = bool(heading_discrepancy_deg >= 45.0)
                except (TypeError, ValueError):
                    pass

            matched_detections.add(det_idx)
            matched_vessel_keys.add(v_key)

            projected_detections[det_idx]["is_speed_spoofed"] = is_speed_spoofed
            projected_detections[det_idx]["is_course_spoofed"] = is_course_spoofed
            projected_detections[det_idx]["speed_discrepancy_knots"] = speed_discrepancy_knots
            projected_detections[det_idx]["heading_discrepancy_deg"] = heading_discrepancy_deg

            projected_detections[det_idx]["correlation_status"] = match_status
            projected_detections[det_idx]["is_correlated"] = True

            matched_reason_codes = generate_contact_reason_codes(
                is_correlated=True,
                is_inside_box=(match_status == "inside_box"),
                association_likelihood=assoc_likelihood,
                wake_detected=bool(projected_detections[det_idx].get("wake_detected")),
                is_speed_spoofed=bool(is_speed_spoofed),
                is_course_spoofed=bool(is_course_spoofed),
                is_dark_suspect=False,
                confidence=projected_detections[det_idx].get("confidence"),
            )

            projected_detections[det_idx]["spatial_uncertainty"] = joint_spatial.to_dict()
            projected_detections[det_idx]["association_likelihood"] = assoc_likelihood
            projected_detections[det_idx]["association_probability"] = assoc_prob
            projected_detections[det_idx]["reason_codes"] = matched_reason_codes

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
                "mahalanobis_distance": round(m_dist, 2),
                "uncertainty_ellipse": cand.get("uncertainty_ellipse"),
                "interpolation_method": cand.get("interpolation_method"),
                "match_type": match_status,
                "is_speed_spoofed": is_speed_spoofed,
                "is_course_spoofed": is_course_spoofed,
                "speed_discrepancy_knots": speed_discrepancy_knots,
                "heading_discrepancy_deg": heading_discrepancy_deg,
                "spatial_uncertainty": joint_spatial.to_dict(),
                "association_likelihood": assoc_likelihood,
                "association_probability": assoc_prob,
                "competing_candidates_count": len(projected_detections[det_idx].get("competing_candidates", [])),
                "reason_codes": matched_reason_codes,
                "timestamp": (
                    vessel["timestamp"].isoformat()
                    if isinstance(vessel.get("timestamp"), datetime)
                    else vessel.get("timestamp")
                ),
                "source_plugin": vessel.get("source_plugin"),
                "vessel_id": vessel.get("vessel_id") or vessel.get("id"),
            }

        # 5. Assess Dark Vessel risk and IMO SOLAS mandate compliance for all detections
        solas_thresh, crit_thresh = self.get_dark_vessel_thresholds()
        for p_det in projected_detections:
            dv_assessment = assess_dark_vessel(
                is_correlated=p_det["is_correlated"],
                length=p_det.get("length"),
                beam=p_det.get("beam"),
                confidence=p_det.get("confidence"),
                solas_threshold=solas_thresh,
                critical_threshold=crit_thresh,
            )
            p_det.update(dv_assessment)
            if not p_det.get("is_correlated"):
                p_det["association_likelihood"] = 0.0
                p_det["reason_codes"] = generate_contact_reason_codes(
                    is_correlated=False,
                    is_inside_box=False,
                    association_likelihood=0.0,
                    wake_detected=bool(p_det.get("wake_detected")),
                    is_speed_spoofed=False,
                    is_course_spoofed=False,
                    is_dark_suspect=bool(p_det.get("is_dark_vessel")),
                    solas_carriage_expected=bool(p_det.get("length") and float(p_det.get("length")) >= solas_thresh),
                    confidence=p_det.get("confidence"),
                )
            if p_det.get("is_speed_spoofed"):
                p_det.setdefault("dark_vessel_reasons", []).append(
                    f"AIS speed spoofing detected (wake speed {p_det.get('wake_speed_knots')} kn vs AIS {p_det['correlated_ais'].get('speed')} kn)"
                )
            if p_det.get("is_course_spoofed"):
                p_det.setdefault("dark_vessel_reasons", []).append(
                    f"AIS course spoofing detected (wake heading {p_det.get('wake_heading')}° vs AIS {p_det['correlated_ais'].get('heading')}°)"
                )
            if p_det.get("is_speed_spoofed") or p_det.get("is_course_spoofed"):
                p_det["is_spoofed"] = True

        # 6. Extract ghost vessels (AIS broadcasts within scan bbox with no radar match)
        ghosts = extract_ghost_vessels(prepared_candidates, matched_vessel_keys, scan_bbox)
        self.last_ghost_vessels = ghosts
        self.last_dark_vessels = [d for d in projected_detections if d.get("is_dark_vessel")]

        return projected_detections

    def execute_with_intelligence(
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
    ) -> dict[str, Any]:
        """Perform correlation and return structured intelligence payload with dark and ghost vessels."""
        results = self.execute(
            detections,
            scan,
            image_width,
            image_height,
            tolerance_meters=tolerance_meters,
            candidate_vessels=candidate_vessels,
            enable_kinematics=enable_kinematics,
            max_propagation_seconds=max_propagation_seconds,
        )
        inside_count = sum(1 for d in results if d.get("correlation_status") == "inside_box")
        outside_count = sum(1 for d in results if d.get("correlation_status") == "outside_box")
        uncorrelated_count = sum(1 for d in results if d.get("correlation_status") == "uncorrelated")
        correlated_count = inside_count + outside_count

        return {
            "detections": results,
            "ship_count": len(results),
            "correlated_count": correlated_count,
            "inside_box_count": inside_count,
            "outside_box_count": outside_count,
            "uncorrelated_count": uncorrelated_count,
            "dark_vessels": list(self.last_dark_vessels),
            "ghost_vessels": list(self.last_ghost_vessels),
            "dark_vessel_count": len(self.last_dark_vessels),
            "critical_dark_count": sum(1 for d in self.last_dark_vessels if d.get("dark_vessel_risk") == "CRITICAL"),
            "ghost_vessel_count": len(self.last_ghost_vessels),
        }

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
