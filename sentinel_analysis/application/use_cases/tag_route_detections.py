"""Use case and geodetic algorithms to tag SAR detections to traced and predicted vessel routes."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Sequence

from sentinel_analysis.application.use_cases.correlate_ais_detections import (
    dead_reckon_position,
    haversine_distance_meters,
    interpolate_kinematic_track,
)


def point_to_line_segment_distance(
    lat: float,
    lon: float,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> tuple[float, float, float, float]:
    """Calculate the shortest geodetic distance in meters from a point to a line segment.

    Parameters:
        lat, lon: Query point coordinates.
        lat1, lon1: Segment start vertex.
        lat2, lon2: Segment end vertex.

    Returns:
        tuple[float, float, float, float]:
            (min_distance_meters, closest_latitude, closest_longitude, projection_t)
            where projection_t is clamped to [0.0, 1.0].
    """
    mid_lat = (lat1 + lat2) / 2.0
    lat_rad = math.radians(mid_lat)
    cos_lat = max(0.01, math.cos(lat_rad))
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * cos_lat

    x2 = (lon2 - lon1) * m_per_deg_lon
    y2 = (lat2 - lat1) * m_per_deg_lat

    xp = (lon - lon1) * m_per_deg_lon
    yp = (lat - lat1) * m_per_deg_lat

    seg_len_sq = x2 * x2 + y2 * y2
    if seg_len_sq < 1e-6:
        dist = math.sqrt(xp * xp + yp * yp)
        return dist, lat1, lon1, 0.0

    t = (xp * x2 + yp * y2) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))

    closest_x = t_clamped * x2
    closest_y = t_clamped * y2

    dx = xp - closest_x
    dy = yp - closest_y
    dist_meters = math.sqrt(dx * dx + dy * dy)

    closest_lat = lat1 + (closest_y / m_per_deg_lat)
    closest_lon = lon1 + (closest_x / m_per_deg_lon)

    return dist_meters, closest_lat, closest_lon, t_clamped


def point_to_route_distance(
    lat: float,
    lon: float,
    route_points: Sequence[tuple[float, float] | dict[str, Any]],
) -> tuple[float, float, float, int, float]:
    """Calculate the minimum geodetic distance from a point to a polyline route.

    Parameters:
        lat, lon: Query point coordinates.
        route_points: Sequence of (lat, lon) pairs or dicts with 'latitude' and 'longitude'.

    Returns:
        tuple[float, float, float, int, float]:
            (min_distance_meters, closest_lat, closest_lon, segment_index, t)
    """
    coords: list[tuple[float, float]] = []
    for p in route_points:
        if isinstance(p, dict):
            p_lat = p.get("latitude") if p.get("latitude") is not None else p.get("lat")
            p_lon = p.get("longitude") if p.get("longitude") is not None else p.get("lon")
            if p_lat is not None and p_lon is not None:
                coords.append((float(p_lat), float(p_lon)))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            coords.append((float(p[0]), float(p[1])))

    if not coords:
        return float("inf"), lat, lon, -1, 0.0

    if len(coords) == 1:
        d = haversine_distance_meters(lat, lon, coords[0][0], coords[0][1])
        return d, coords[0][0], coords[0][1], 0, 0.0

    min_dist = float("inf")
    best_lat = lat
    best_lon = lon
    best_seg = 0
    best_t = 0.0

    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        dist, c_lat, c_lon, t = point_to_line_segment_distance(lat, lon, lat1, lon1, lat2, lon2)
        if dist < min_dist:
            min_dist = dist
            best_lat = c_lat
            best_lon = c_lon
            best_seg = i
            best_t = t

    return min_dist, best_lat, best_lon, best_seg, best_t


def build_traced_and_predicted_route(
    points: Sequence[dict[str, Any]],
    target_time: datetime | None = None,
    predict_forward_seconds: float = 3600.0,
) -> list[dict[str, Any]]:
    """Build a complete route sequence comprising traced historical points and predicted forward waypoints.

    Parameters:
        points: Chronological or unsorted AIS reports.
        target_time: Optional SAR pass epoch to interpolate/dead-reckon to.
        predict_forward_seconds: Forward extrapolation duration from the latest fix in seconds.

    Returns:
        list[dict[str, Any]]: Sequence of route waypoints with 'latitude', 'longitude', 'is_predicted', etc.
    """
    valid_pts: list[dict[str, Any]] = []
    for p in points:
        lat = p.get("latitude") if p.get("latitude") is not None else p.get("lat")
        lon = p.get("longitude") if p.get("longitude") is not None else p.get("lon")
        if lat is not None and lon is not None:
            valid_pts.append(dict(p))

    if not valid_pts:
        return []

    valid_pts.sort(key=lambda p: str(p.get("timestamp") or ""))

    waypoints: list[dict[str, Any]] = []
    for p in valid_pts:
        waypoints.append({
            "latitude": float(p.get("latitude") if p.get("latitude") is not None else p["lat"]),
            "longitude": float(p.get("longitude") if p.get("longitude") is not None else p["lon"]),
            "timestamp": p.get("timestamp"),
            "speed": float(p.get("speed") or p.get("sog") or 0.0),
            "heading": float(p.get("heading") or p.get("course") or 0.0),
            "is_predicted": False,
            "waypoint_type": "traced",
        })

    last_p = valid_pts[-1]
    spd = float(last_p.get("speed") or last_p.get("sog") or 0.0)
    hdg = float(last_p.get("heading") or last_p.get("course") or 0.0)

    # If target_time is given, project/interpolate to pass time
    if target_time is not None:
        try:
            interp = interpolate_kinematic_track(valid_pts, target_time)
            # Add if significantly separated from the last waypoint
            dist_to_last = haversine_distance_meters(
                waypoints[-1]["latitude"],
                waypoints[-1]["longitude"],
                interp["latitude"],
                interp["longitude"],
            )
            if dist_to_last > 15.0:
                waypoints.append({
                    "latitude": round(interp["latitude"], 6),
                    "longitude": round(interp["longitude"], 6),
                    "timestamp": target_time.isoformat(),
                    "speed": spd,
                    "heading": hdg,
                    "is_predicted": True,
                    "waypoint_type": "interpolated_pass",
                })
        except Exception:
            pass

    # Add forward prediction if speed indicates vessel is moving
    if predict_forward_seconds > 0.0 and spd >= 0.5:
        anchor_lat = waypoints[-1]["latitude"]
        anchor_lon = waypoints[-1]["longitude"]
        proj_lat, proj_lon, _ = dead_reckon_position(
            anchor_lat, anchor_lon, spd, hdg, predict_forward_seconds
        )
        dist_forward = haversine_distance_meters(anchor_lat, anchor_lon, proj_lat, proj_lon)
        if dist_forward > 25.0:
            waypoints.append({
                "latitude": round(proj_lat, 6),
                "longitude": round(proj_lon, 6),
                "timestamp": None,
                "speed": spd,
                "heading": hdg,
                "is_predicted": True,
                "waypoint_type": "future_prediction",
            })

    return waypoints


class TagRouteDetections:
    """Use case to tag SAR ship detections within a user-defined buffer of a vessel's traced/predicted route."""

    def execute(
        self,
        detections: list[dict[str, Any]],
        vessels: list[dict[str, Any]],
        buffer_meters: float = 500.0,
        target_time: datetime | None = None,
        predict_forward_seconds: float = 3600.0,
    ) -> dict[str, Any]:
        """Correlate and tag detections within route buffer corridor.

        Parameters:
            detections: List of SAR ship detection dicts.
            vessels: List of vessel dicts with AIS tracks/locations.
            buffer_meters: User-defined corridor radius in meters.
            target_time: Optional SAR pass epoch for pass-time prediction.
            predict_forward_seconds: Forward trajectory projection time in seconds.

        Returns:
            dict[str, Any]: Tagging outcome with enriched vessels and detections.
        """
        buffer_m = max(1.0, float(buffer_meters))

        prepared_vessels: list[dict[str, Any]] = []
        for v in vessels:
            v_copy = dict(v)
            route = v_copy.get("route")
            if not route:
                pts = v_copy.get("points") or v_copy.get("locations") or []
                if pts:
                    route = build_traced_and_predicted_route(
                        pts,
                        target_time=target_time,
                        predict_forward_seconds=predict_forward_seconds,
                    )
                elif v_copy.get("latitude") is not None and v_copy.get("longitude") is not None:
                    route = [{
                        "latitude": float(v_copy["latitude"]),
                        "longitude": float(v_copy["longitude"]),
                        "timestamp": v_copy.get("timestamp"),
                        "speed": float(v_copy.get("speed") or 0.0),
                        "heading": float(v_copy.get("heading") or 0.0),
                        "is_predicted": False,
                        "waypoint_type": "current",
                    }]
                else:
                    route = []
            v_copy["route"] = route
            v_copy["route_buffer_meters"] = buffer_m
            v_copy["tagged_detections"] = []
            prepared_vessels.append(v_copy)

        enriched_detections: list[dict[str, Any]] = []
        for idx, det in enumerate(detections):
            det_copy = dict(det)
            lat = (
                det_copy.get("lat")
                if det_copy.get("lat") is not None
                else det_copy.get("latitude")
                if det_copy.get("latitude") is not None
                else det_copy.get("center_lat")
            )
            lon = (
                det_copy.get("lon")
                if det_copy.get("lon") is not None
                else det_copy.get("lng")
                if det_copy.get("lng") is not None
                else det_copy.get("longitude")
                if det_copy.get("longitude") is not None
                else det_copy.get("center_lon")
            )

            if lat is None or lon is None:
                det_copy["tagged_to_vessel"] = False
                det_copy["route_tagged_vessel"] = None
                det_copy["all_route_tags"] = []
                enriched_detections.append(det_copy)
                continue

            lat_f = float(lat)
            lon_f = float(lon)

            matches: list[dict[str, Any]] = []
            for v in prepared_vessels:
                route_pts = v.get("route") or []
                if not route_pts:
                    continue

                min_dist, c_lat, c_lon, seg_idx, t = point_to_route_distance(lat_f, lon_f, route_pts)
                if min_dist <= buffer_m:
                    is_pred_seg = False
                    if 0 <= seg_idx < len(route_pts):
                        is_pred_seg = bool(route_pts[seg_idx].get("is_predicted"))
                    if 0 <= seg_idx + 1 < len(route_pts):
                        is_pred_seg = is_pred_seg or bool(route_pts[seg_idx + 1].get("is_predicted"))

                    matches.append({
                        "vessel": v,
                        "distance": min_dist,
                        "closest_lat": c_lat,
                        "closest_lon": c_lon,
                        "segment_index": seg_idx,
                        "projection_t": t,
                        "is_predicted": is_pred_seg,
                    })

            if matches:
                matches.sort(key=lambda item: item["distance"])
                best = matches[0]
                best_v = best["vessel"]

                tag_info = {
                    "mmsi": best_v.get("mmsi"),
                    "vessel_name": best_v.get("name") or best_v.get("vessel_name") or f"Vessel {best_v.get('mmsi')}",
                    "vessel_type": best_v.get("ship_type") or best_v.get("vessel_type") or "Vessel",
                    "vessel_id": best_v.get("vessel_id") or best_v.get("id"),
                    "distance_to_route_m": round(best["distance"], 1),
                    "closest_point": [round(best["closest_lat"], 6), round(best["closest_lon"], 6)],
                    "segment_index": best["segment_index"],
                    "is_predicted_segment": best["is_predicted"],
                    "buffer_meters": round(buffer_m, 1),
                    "is_within_buffer": True,
                }

                all_tags = [
                    {
                        "mmsi": m["vessel"].get("mmsi"),
                        "vessel_name": m["vessel"].get("name") or m["vessel"].get("vessel_name"),
                        "distance_to_route_m": round(m["distance"], 1),
                    }
                    for m in matches
                ]

                det_copy["tagged_to_vessel"] = True
                det_copy["route_tagged_vessel"] = tag_info
                det_copy["all_route_tags"] = all_tags

                best_v["tagged_detections"].append({
                    "detection_index": det_copy.get("index", idx),
                    "lat": round(lat_f, 6),
                    "lon": round(lon_f, 6),
                    "confidence": det_copy.get("confidence"),
                    "length": det_copy.get("length"),
                    "distance_to_route_m": round(best["distance"], 1),
                    "closest_point": [round(best["closest_lat"], 6), round(best["closest_lon"], 6)],
                    "segment_index": best["segment_index"],
                    "is_predicted_segment": best["is_predicted"],
                    "buffer_meters": round(buffer_m, 1),
                })
            else:
                det_copy["tagged_to_vessel"] = False
                det_copy["route_tagged_vessel"] = None
                det_copy["all_route_tags"] = []

            enriched_detections.append(det_copy)

        return {
            "buffer_meters": buffer_m,
            "total_detections": len(enriched_detections),
            "tagged_detections_count": sum(1 for d in enriched_detections if d.get("tagged_to_vessel")),
            "vessels_with_tags_count": sum(1 for v in prepared_vessels if len(v.get("tagged_detections", [])) > 0),
            "vessels": prepared_vessels,
            "detections": enriched_detections,
        }
