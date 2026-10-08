"""Use case to detect Ship-to-Ship (STS) transshipment rendezvous and anomalous loitering."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.domain.entities import (
    BoundingBox,
    LoiteringAnomaly,
    Scan,
    TransshipmentRendezvous,
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
    return radius * (2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a)))


def initial_compass_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the initial compass bearing in degrees [0, 360) from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - (math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda))
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return round(bearing, 1)


class DetectTransshipmentAnomalies:
    """Detect Ship-to-Ship (STS) rendezvous pairs and anomalous loitering patterns."""

    def __init__(self, scan_repository: Optional[Any] = None) -> None:
        self._scan_repository = scan_repository

    @staticmethod
    def _extract_coords(
        det: dict[str, Any],
        scan_bbox: Optional[BoundingBox] = None,
        img_w: int = 1000,
        img_h: int = 1000,
    ) -> tuple[Optional[float], Optional[float]]:
        """Extract or project WGS-84 (lat, lon) coordinates from a detection record."""
        # 1. Direct coordinates
        lat = det.get("lat") or det.get("latitude")
        lon = det.get("lng") or det.get("lon") or det.get("longitude")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon)
            except (TypeError, ValueError):
                pass

        # 2. Project from pixel coordinates and scan bounding box
        if scan_bbox is not None and img_w > 0 and img_h > 0:
            cx = det.get("center_x") or det.get("x", 0) + det.get("width", 0) / 2.0
            cy = det.get("center_y") or det.get("y", 0) + det.get("height", 0) / 2.0
            lat_scale = (scan_bbox.max_latitude - scan_bbox.min_latitude) / img_h
            lon_scale = (scan_bbox.max_longitude - scan_bbox.min_longitude) / img_w
            proj_lat = scan_bbox.max_latitude - float(cy) * lat_scale
            proj_lon = scan_bbox.min_longitude + float(cx) * lon_scale
            return round(proj_lat, 6), round(proj_lon, 6)

        return None, None

    @staticmethod
    def _extract_speed(det: dict[str, Any]) -> float:
        """Extract vessel speed in knots from wake or correlated AIS."""
        # Wake speed is the primary physical SAR observable
        wake_speed = det.get("wake_speed_knots")
        if wake_speed is not None:
            try:
                return float(wake_speed)
            except (TypeError, ValueError):
                pass

        # AIS correlated speed
        correlated_ais = det.get("correlated_ais")
        if isinstance(correlated_ais, dict) and correlated_ais.get("speed") is not None:
            try:
                return float(correlated_ais["speed"])
            except (TypeError, ValueError):
                pass

        # If no wake detected on radar, ship is typically stationary or slow drifting
        return 0.0

    @staticmethod
    def _extract_identifier(det: dict[str, Any], idx: int) -> str:
        """Extract human-readable vessel identifier or callsign."""
        ais = det.get("correlated_ais")
        if isinstance(ais, dict):
            mmsi = ais.get("mmsi")
            name = ais.get("vessel_name") or ais.get("name")
            if name and mmsi:
                return f"{name} ({mmsi})"
            if mmsi:
                return f"MMSI:{mmsi}"
            if name:
                return str(name)

        if det.get("is_dark_vessel", False):
            return f"DARK_SAR_#{idx}"
        return f"TARGET_#{idx}"

    def execute_from_detections(
        self,
        detections: list[dict[str, Any]],
        *,
        scan_bbox: Optional[BoundingBox] = None,
        image_width: int = 1000,
        image_height: int = 1000,
        max_rendezvous_distance_meters: float = 600.0,
        max_rendezvous_speed_knots: float = 3.5,
        min_loiter_length_meters: float = 25.0,
    ) -> dict[str, Any]:
        """Analyze detection records to uncover STS rendezvous pairs and anomalous loitering."""
        # Prepare normalized targets with geographic coordinates
        targets: list[dict[str, Any]] = []
        for i, det in enumerate(detections):
            lat, lon = self._extract_coords(det, scan_bbox, image_width, image_height)
            if lat is None or lon is None:
                continue
            is_dark = bool(det.get("is_dark_vessel", not det.get("is_correlated", False)))
            length = det.get("length")
            try:
                length_val = float(length) if length is not None and float(length) > 0 else None
            except (TypeError, ValueError):
                length_val = None

            targets.append({
                "index": det.get("index", i),
                "lat": lat,
                "lon": lon,
                "is_dark": is_dark,
                "length": length_val,
                "beam": det.get("beam"),
                "speed_knots": self._extract_speed(det),
                "identifier": self._extract_identifier(det, det.get("index", i)),
                "raw": det,
            })

        rendezvous_events: list[TransshipmentRendezvous] = []
        n = len(targets)

        # 1. Pairwise STS Rendezvous detection
        for i in range(n):
            for j in range(i + 1, n):
                t1 = targets[i]
                t2 = targets[j]

                dist = haversine_distance_meters(t1["lat"], t1["lon"], t2["lat"], t2["lon"])
                if dist > max_rendezvous_distance_meters:
                    continue

                # Speed constraint: Both vessels must be slow or stationary during an STS transfer
                if t1["speed_knots"] > max_rendezvous_speed_knots or t2["speed_knots"] > max_rendezvous_speed_knots:
                    continue

                bearing = initial_compass_bearing(t1["lat"], t1["lon"], t2["lat"], t2["lon"])

                # Threat evaluation based on dark status combination
                both_dark = t1["is_dark"] and t2["is_dark"]
                one_dark = t1["is_dark"] or t2["is_dark"]

                dist_penalty = (dist / max_rendezvous_distance_meters) * 15.0

                if both_dark:
                    risk_level = "CRITICAL"
                    risk_score = round(max(50.0, 98.0 - dist_penalty), 1)
                    narrative = (
                        f"CRITICAL: Unidentified STS rendezvous between two dark vessels ({t1['identifier']} "
                        f"and {t2['identifier']}) separated by {dist:.1f}m with no active AIS broadcasts."
                    )
                elif one_dark:
                    risk_level = "HIGH"
                    risk_score = round(max(40.0, 85.0 - dist_penalty), 1)
                    dark_id = t1["identifier"] if t1["is_dark"] else t2["identifier"]
                    comp_id = t2["identifier"] if t1["is_dark"] else t1["identifier"]
                    narrative = (
                        f"HIGH RISK: Suspected illicit transshipment: dark vessel {dark_id} rendezvoused "
                        f"with AIS-broadcasting vessel {comp_id} at {dist:.1f}m separation."
                    )
                else:
                    risk_level = "MEDIUM"
                    risk_score = round(max(20.0, 60.0 - dist_penalty), 1)
                    narrative = (
                        f"MEDIUM: Potential open-sea STS transfer: two AIS-broadcasting vessels ({t1['identifier']} "
                        f"and {t2['identifier']}) in close proximity ({dist:.1f}m)."
                    )

                event = TransshipmentRendezvous(
                    vessel_a_index=t1["index"],
                    vessel_b_index=t2["index"],
                    distance_meters=round(dist, 1),
                    relative_bearing_deg=bearing,
                    vessel_a_is_dark=t1["is_dark"],
                    vessel_b_is_dark=t2["is_dark"],
                    risk_level=risk_level,
                    risk_score=risk_score,
                    vessel_a_lat=t1["lat"],
                    vessel_a_lon=t1["lon"],
                    vessel_b_lat=t2["lat"],
                    vessel_b_lon=t2["lon"],
                    vessel_a_length=t1["length"],
                    vessel_b_length=t2["length"],
                    vessel_a_identifier=t1["identifier"],
                    vessel_b_identifier=t2["identifier"],
                    narrative=narrative,
                )
                rendezvous_events.append(event)

        # 2. Anomalous Loitering Detection
        loitering_events: list[LoiteringAnomaly] = []
        for t in targets:
            # Low speed condition
            if t["speed_knots"] <= 2.0:
                length = t["length"]
                is_dark = t["is_dark"]

                # Check if significant vessel or dark
                if is_dark or (length is not None and length >= min_loiter_length_meters):
                    if is_dark and length and length >= 75.0:
                        risk_level = "CRITICAL"
                        risk_score = 90.0
                        narrative = (
                            f"CRITICAL: Large dark vessel ({length:.0f}m, {t['identifier']}) stationary/loitering "
                            f"({t['speed_knots']:.1f} kn) in open sea without AIS."
                        )
                    elif is_dark:
                        risk_level = "HIGH"
                        risk_score = 75.0
                        narrative = (
                            f"HIGH RISK: Dark vessel ({t['identifier']}) drifting or loitering ({t['speed_knots']:.1f} kn) "
                            f"without active AIS transmission."
                        )
                    else:
                        risk_level = "MEDIUM"
                        risk_score = 50.0
                        narrative = (
                            f"MEDIUM: AIS vessel {t['identifier']} ({length:.0f}m) loitering ({t['speed_knots']:.1f} kn) "
                            f"in open waters."
                        )

                    loitering = LoiteringAnomaly(
                        vessel_index=t["index"],
                        lat=t["lat"],
                        lon=t["lon"],
                        is_dark=is_dark,
                        speed_knots=round(t["speed_knots"], 1),
                        risk_level=risk_level,
                        risk_score=risk_score,
                        length=length,
                        identifier=t["identifier"],
                        narrative=narrative,
                    )
                    loitering_events.append(loitering)

        # 3. Overall Threat Assessment
        max_score = 0.0
        if rendezvous_events:
            max_score = max(max_score, max(r.risk_score for r in rendezvous_events))
        if loitering_events:
            max_score = max(max_score, max(l.risk_score for l in loitering_events))

        if max_score >= 85.0:
            overall_threat = "CRITICAL"
        elif max_score >= 70.0:
            overall_threat = "HIGH"
        elif max_score >= 40.0:
            overall_threat = "MEDIUM"
        else:
            overall_threat = "LOW"

        # 4. Generate GeoJSON features
        geojson_features: list[dict[str, Any]] = []

        # Rendezvous connection lines
        for r in rendezvous_events:
            geojson_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [r.vessel_a_lon, r.vessel_a_lat],
                        [r.vessel_b_lon, r.vessel_b_lat],
                    ],
                },
                "properties": {
                    "feature_type": "STS_RENDEZVOUS",
                    "risk_level": r.risk_level,
                    "risk_score": r.risk_score,
                    "distance_meters": r.distance_meters,
                    "relative_bearing_deg": r.relative_bearing_deg,
                    "vessel_a": r.vessel_a_identifier,
                    "vessel_b": r.vessel_b_identifier,
                    "narrative": r.narrative,
                },
            })

        # Loitering points
        for l in loitering_events:
            geojson_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [l.lon, l.lat],
                },
                "properties": {
                    "feature_type": "LOITERING_ANOMALY",
                    "risk_level": l.risk_level,
                    "risk_score": l.risk_score,
                    "speed_knots": l.speed_knots,
                    "length": l.length,
                    "identifier": l.identifier,
                    "narrative": l.narrative,
                },
            })

        return {
            "status": "success",
            "overall_threat_level": overall_threat,
            "max_risk_score": round(max_score, 1),
            "total_targets_evaluated": len(targets),
            "rendezvous_count": len(rendezvous_events),
            "critical_rendezvous_count": sum(1 for r in rendezvous_events if r.risk_level == "CRITICAL"),
            "high_rendezvous_count": sum(1 for r in rendezvous_events if r.risk_level == "HIGH"),
            "loitering_count": len(loitering_events),
            "rendezvous_events": [
                {
                    "vessel_a_index": r.vessel_a_index,
                    "vessel_b_index": r.vessel_b_index,
                    "distance_meters": r.distance_meters,
                    "relative_bearing_deg": r.relative_bearing_deg,
                    "vessel_a_is_dark": r.vessel_a_is_dark,
                    "vessel_b_is_dark": r.vessel_b_is_dark,
                    "risk_level": r.risk_level,
                    "risk_score": r.risk_score,
                    "vessel_a_lat": r.vessel_a_lat,
                    "vessel_a_lon": r.vessel_a_lon,
                    "vessel_b_lat": r.vessel_b_lat,
                    "vessel_b_lon": r.vessel_b_lon,
                    "vessel_a_length": r.vessel_a_length,
                    "vessel_b_length": r.vessel_b_length,
                    "vessel_a_identifier": r.vessel_a_identifier,
                    "vessel_b_identifier": r.vessel_b_identifier,
                    "narrative": r.narrative,
                }
                for r in rendezvous_events
            ],
            "loitering_events": [
                {
                    "vessel_index": l.vessel_index,
                    "lat": l.lat,
                    "lon": l.lon,
                    "is_dark": l.is_dark,
                    "speed_knots": l.speed_knots,
                    "risk_level": l.risk_level,
                    "risk_score": l.risk_score,
                    "length": l.length,
                    "identifier": l.identifier,
                    "narrative": l.narrative,
                }
                for l in loitering_events
            ],
            "geojson": {
                "type": "FeatureCollection",
                "features": geojson_features,
            },
        }

    def execute(
        self,
        folder_name: str,
        *,
        max_rendezvous_distance_meters: float = 600.0,
        max_rendezvous_speed_knots: float = 3.5,
        min_loiter_length_meters: float = 25.0,
    ) -> dict[str, Any]:
        """Load scan and detection results, then execute transshipment and loitering analysis."""
        if self._scan_repository is None:
            raise RuntimeError("ScanRepository is required to execute by scan folder name")

        scan_getter = getattr(self._scan_repository, "get", None) or getattr(self._scan_repository, "get_scan", None)
        if scan_getter is None:
            raise RuntimeError("ScanRepository must provide get() or get_scan()")
        scan = scan_getter(folder_name)
        if scan is None:
            raise ValueError(f"Scan {folder_name} not found")

        image_path = Path(scan.image_path)
        candidates = [
            image_path.parent / "detections.json",
            image_path.parent / f"{image_path.stem}_detections.json",
            image_path.parent / "detection_results.json",
        ]
        detections: list[dict[str, Any]] = []
        for c in candidates:
            if c.is_file():
                try:
                    loaded = json.loads(c.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        detections = loaded
                    elif isinstance(loaded, dict) and "detections" in loaded:
                        detections = loaded["detections"]
                    break
                except Exception:
                    pass

        return self.execute_from_detections(
            detections,
            scan_bbox=scan.bbox,
            max_rendezvous_distance_meters=max_rendezvous_distance_meters,
            max_rendezvous_speed_knots=max_rendezvous_speed_knots,
            min_loiter_length_meters=min_loiter_length_meters,
        )
