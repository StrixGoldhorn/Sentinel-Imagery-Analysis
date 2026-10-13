"""Application use case for detecting maritime AIS identity anomalies and kinematic spoofing."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.identity_anomalies import (
    IdentityAnomaly,
    IdentityAnomalySeverity,
    IdentityAnomalyType,
    get_country_from_mmsi,
    haversine_distance_km,
)

logger = logging.getLogger(__name__)


def _parse_utc_timestamp(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, str):
        val_clean = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(val_clean)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


class DetectIdentityAnomalies:
    """Evaluates AIS records and scan context to flag identity, flag, gap, and kinematic anomalies."""

    def __init__(
        self,
        ais_repository: Optional[Any] = None,
        scan_repository: Optional[Any] = None,
    ) -> None:
        self._ais_repository = ais_repository
        self._scan_repository = scan_repository

    def execute(
        self,
        ais_records: Optional[list[dict[str, Any]]] = None,
        scan_id: Optional[str] = None,
        aoi_bbox: Optional[BoundingBox] = None,
        reference_time: Optional[datetime] = None,
        designated_anchorages: Optional[list[dict[str, Any]]] = None,
        max_speed_knots: float = 55.0,
        gap_threshold_hours: float = 2.0,
        loiter_speed_knots: float = 2.5,
        loiter_min_hours: float = 1.5,
    ) -> dict[str, Any]:
        records = list(ais_records or [])

        # If records not provided directly and repository exists, fetch records
        if not records and self._ais_repository is not None:
            try:
                records = self._ais_repository.get_vessel_positions(
                    bbox=aoi_bbox,
                    limit=5000,
                )
            except Exception as exc:
                logger.warning("Failed to fetch AIS records from repository: %s", exc)

        # Group records by MMSI
        mmsi_groups: dict[str, list[dict[str, Any]]] = {}
        for r in records:
            raw_mmsi = r.get("mmsi") or r.get("vessel_id")
            if not raw_mmsi:
                continue
            mmsi_str = str(raw_mmsi).strip()
            mmsi_groups.setdefault(mmsi_str, []).append(r)

        anomalies: list[IdentityAnomaly] = []
        anomaly_counter = 0

        for mmsi, group in mmsi_groups.items():
            # Sort chronological
            valid_group = []
            for item in group:
                ts = _parse_utc_timestamp(item.get("timestamp"))
                lat = item.get("latitude")
                lon = item.get("longitude")
                if lat is not None and lon is not None:
                    valid_group.append({
                        **item,
                        "_parsed_ts": ts,
                        "_lat": float(lat),
                        "_lon": float(lon),
                    })

            valid_group.sort(key=lambda x: x["_parsed_ts"] or datetime.min.replace(tzinfo=timezone.utc))

            # 1. MMSI Reuse & Metadata Inconsistency
            reuse_anomalies = self._detect_mmsi_reuse(mmsi, valid_group)
            anomalies.extend(reuse_anomalies)

            # 2. Flag and Callsign Inconsistency
            flag_anomalies = self._detect_flag_callsign_changes(mmsi, valid_group)
            anomalies.extend(flag_anomalies)

            # 3. Duplicate Identity Broadcasts (simultaneous disparate positions)
            dup_anomalies = self._detect_duplicate_identities(mmsi, valid_group)
            anomalies.extend(dup_anomalies)

            # 4. Impossible Kinematic Jumps & Speeds
            jump_anomalies = self._detect_impossible_jumps(mmsi, valid_group, max_speed_knots=max_speed_knots)
            anomalies.extend(jump_anomalies)

            # 5. Teleportation Anomaly
            teleport_anomalies = self._detect_teleportation(mmsi, valid_group)
            anomalies.extend(teleport_anomalies)

            # 6. Course Discontinuity Anomaly
            course_anomalies = self._detect_course_discontinuities(mmsi, valid_group)
            anomalies.extend(course_anomalies)

            # 7. AIS Blackout Gaps
            gap_anomalies = self._detect_ais_gaps(mmsi, valid_group, gap_hours_threshold=gap_threshold_hours)
            anomalies.extend(gap_anomalies)

            # 8. Loitering
            loiter_anomalies = self._detect_loitering(
                mmsi,
                valid_group,
                max_speed_knots=loiter_speed_knots,
                min_hours=loiter_min_hours,
                designated_anchorages=designated_anchorages,
            )
            anomalies.extend(loiter_anomalies)

            # 9. Unusual Route & High Sinuosity
            route_anomalies = self._detect_unusual_routes(mmsi, valid_group)
            anomalies.extend(route_anomalies)

        # 10. Multi-vessel Tactical Interactions: Convoy and Rendezvous
        convoy_anomalies = self._detect_convoys(mmsi_groups)
        anomalies.extend(convoy_anomalies)

        rendezvous_anomalies = self._detect_rendezvous(mmsi_groups, designated_anchorages=designated_anchorages)
        anomalies.extend(rendezvous_anomalies)

        # Generate GeoJSON features
        geojson_features = []
        for a in anomalies:
            if a.latitude is not None and a.longitude is not None:
                geojson_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(a.longitude, 6), round(a.latitude, 6)],
                    },
                    "properties": {
                        "feature_type": "IDENTITY_ANOMALY",
                        "anomaly_id": a.anomaly_id,
                        "anomaly_type": a.anomaly_type.value,
                        "mmsi": a.mmsi,
                        "vessel_name": a.vessel_name,
                        "severity": a.severity.value,
                        "confidence": a.confidence,
                        "description": a.description,
                        "evidence": a.evidence,
                    },
                })

        counts_by_type = {t.value: 0 for t in IdentityAnomalyType}
        for a in anomalies:
            type_val = a.anomaly_type.value if isinstance(a.anomaly_type, IdentityAnomalyType) else str(a.anomaly_type)
            counts_by_type[type_val] = counts_by_type.get(type_val, 0) + 1

        overall_threat = "LOW"
        if any(a.severity == IdentityAnomalySeverity.CRITICAL for a in anomalies):
            overall_threat = "CRITICAL"
        elif any(a.severity == IdentityAnomalySeverity.HIGH for a in anomalies):
            overall_threat = "HIGH"
        elif any(a.severity == IdentityAnomalySeverity.MEDIUM for a in anomalies):
            overall_threat = "MEDIUM"

        result = {
            "status": "success",
            "total_anomalies": len(anomalies),
            "threat_level": overall_threat,
            "anomalies_by_type": counts_by_type,
            "anomalies": [a.to_dict() for a in anomalies],
            "geojson": {
                "type": "FeatureCollection",
                "features": geojson_features,
            },
        }

        # Optional persistence to scan directory
        if scan_id and self._scan_repository is not None:
            try:
                scan = self._scan_repository.get_scan(scan_id)
                if scan and scan.image_path:
                    scan_dir = Path(scan.image_path).parent
                    out_json = scan_dir / "identity_anomalies.json"
                    out_geojson = scan_dir / "identity_anomalies.geojson"
                    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
                    out_geojson.write_text(json.dumps(result["geojson"], indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not persist identity anomalies for scan %s: %s", scan_id, exc)

        return result

    def _detect_mmsi_reuse(self, mmsi: str, records: list[dict[str, Any]]) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 2:
            return anomalies

        names = set()
        lengths = set()
        callsigns = set()

        for r in records:
            name = (r.get("vessel_name") or r.get("name") or "").strip().upper()
            if name and name not in {"UNKNOWN", "N/A", "NONE"}:
                names.add(name)
            l = r.get("length")
            if l is not None:
                try:
                    lf = float(l)
                    if lf > 5.0:
                        lengths.add(lf)
                except (ValueError, TypeError):
                    pass
            cs = (r.get("callsign") or "").strip().upper()
            if cs and cs not in {"UNKNOWN", "N/A", "NONE", "0", "0000"}:
                callsigns.add(cs)

        # Distinct names check
        if len(names) >= 2:
            name_list = sorted(list(names))
            latest = records[-1]
            anomalies.append(
                IdentityAnomaly(
                    anomaly_id=f"anom-reuse-{mmsi}-{len(anomalies)+1}",
                    anomaly_type=IdentityAnomalyType.MMSI_REUSE,
                    mmsi=mmsi,
                    vessel_name=latest.get("vessel_name") or name_list[0],
                    severity=IdentityAnomalySeverity.HIGH,
                    confidence=0.92,
                    description=f"MMSI {mmsi} is associated with multiple distinct vessel names: {', '.join(name_list)}",
                    evidence={"distinct_names": name_list, "mmsi": mmsi},
                    detected_at=latest["_parsed_ts"] or datetime.now(timezone.utc),
                    latitude=latest.get("_lat"),
                    longitude=latest.get("_lon"),
                )
            )

        # Conflicting lengths check
        if len(lengths) >= 2:
            min_l, max_l = min(lengths), max(lengths)
            if max_l > min_l * 1.4 and (max_l - min_l) > 20.0:
                latest = records[-1]
                anomalies.append(
                    IdentityAnomaly(
                        anomaly_id=f"anom-reuse-dim-{mmsi}-{len(anomalies)+1}",
                        anomaly_type=IdentityAnomalyType.MMSI_REUSE,
                        mmsi=mmsi,
                        vessel_name=latest.get("vessel_name"),
                        severity=IdentityAnomalySeverity.HIGH,
                        confidence=0.88,
                        description=f"MMSI {mmsi} reports conflicting hull lengths: {min_l:.1f}m vs {max_l:.1f}m (Δ={max_l-min_l:.1f}m)",
                        evidence={"min_length_m": min_l, "max_length_m": max_l, "mmsi": mmsi},
                        detected_at=latest["_parsed_ts"] or datetime.now(timezone.utc),
                        latitude=latest.get("_lat"),
                        longitude=latest.get("_lon"),
                    )
                )

        return anomalies

    def _detect_flag_callsign_changes(self, mmsi: str, records: list[dict[str, Any]]) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if not records:
            return anomalies

        expected_country, expected_iso = get_country_from_mmsi(mmsi)

        flags_seen = set()
        callsigns_seen = set()

        for r in records:
            flag = (r.get("flag") or r.get("flag_country") or "").strip()
            if flag:
                flags_seen.add(flag.upper())
            cs = (r.get("callsign") or "").strip().upper()
            if cs and cs not in {"UNKNOWN", "N/A", "NONE"}:
                callsigns_seen.add(cs)

        latest = records[-1]

        # Check MID vs stated flag
        if expected_country and flags_seen:
            for flag in flags_seen:
                # If flag does not match expected country name or ISO
                if (
                    expected_country.upper() not in flag
                    and flag not in expected_country.upper()
                    and (not expected_iso or flag != expected_iso)
                ):
                    anomalies.append(
                        IdentityAnomaly(
                            anomaly_id=f"anom-flag-{mmsi}-{len(anomalies)+1}",
                            anomaly_type=IdentityAnomalyType.FLAG_CALLSIGN_CHANGE,
                            mmsi=mmsi,
                            vessel_name=latest.get("vessel_name"),
                            severity=IdentityAnomalySeverity.MEDIUM,
                            confidence=0.85,
                            description=f"MMSI MID ({mmsi[:3]}) designates {expected_country} ({expected_iso}), but vessel broadcasts flag state '{flag}'",
                            evidence={
                                "mid": mmsi[:3],
                                "expected_country": expected_country,
                                "expected_iso": expected_iso,
                                "broadcast_flag": flag,
                            },
                            detected_at=latest["_parsed_ts"] or datetime.now(timezone.utc),
                            latitude=latest.get("_lat"),
                            longitude=latest.get("_lon"),
                        )
                    )
                    break

        # Check mutating callsigns
        if len(callsigns_seen) >= 2:
            cs_list = sorted(list(callsigns_seen))
            anomalies.append(
                IdentityAnomaly(
                    anomaly_id=f"anom-callsign-{mmsi}-{len(anomalies)+1}",
                    anomaly_type=IdentityAnomalyType.FLAG_CALLSIGN_CHANGE,
                    mmsi=mmsi,
                    vessel_name=latest.get("vessel_name"),
                    severity=IdentityAnomalySeverity.MEDIUM,
                    confidence=0.80,
                    description=f"MMSI {mmsi} alternated callsigns within track: {', '.join(cs_list)}",
                    evidence={"callsigns": cs_list, "mmsi": mmsi},
                    detected_at=latest["_parsed_ts"] or datetime.now(timezone.utc),
                    latitude=latest.get("_lat"),
                    longitude=latest.get("_lon"),
                )
            )

        return anomalies

    def _detect_duplicate_identities(self, mmsi: str, records: list[dict[str, Any]]) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 2:
            return anomalies

        # Look for transmissions occurring within 30 minutes of each other separated by > 35 km
        # or within 2 minutes separated by > 3 km
        for i in range(len(records)):
            r1 = records[i]
            t1 = r1["_parsed_ts"]
            if t1 is None:
                continue
            for j in range(i + 1, min(i + 10, len(records))):
                r2 = records[j]
                t2 = r2["_parsed_ts"]
                if t2 is None:
                    continue
                dt_sec = abs((t2 - t1).total_seconds())
                dist_km = haversine_distance_km(r1["_lat"], r1["_lon"], r2["_lat"], r2["_lon"])

                is_dup = False
                if dt_sec <= 120.0 and dist_km > 3.0:
                    is_dup = True
                elif dt_sec <= 1800.0 and dist_km > 35.0:
                    is_dup = True

                if is_dup:
                    anomalies.append(
                        IdentityAnomaly(
                            anomaly_id=f"anom-dup-{mmsi}-{i}-{j}",
                            anomaly_type=IdentityAnomalyType.DUPLICATE_IDENTITY,
                            mmsi=mmsi,
                            vessel_name=r2.get("vessel_name") or r1.get("vessel_name"),
                            severity=IdentityAnomalySeverity.CRITICAL,
                            confidence=0.96,
                            description=(
                                f"Duplicate identity broadcast: MMSI {mmsi} reported simultaneously from two separate positions "
                                f"{dist_km:.1f} km apart within {dt_sec/60.0:.1f} minutes."
                            ),
                            evidence={
                                "position_1": {"lat": r1["_lat"], "lon": r1["_lon"], "timestamp": t1.isoformat()},
                                "position_2": {"lat": r2["_lat"], "lon": r2["_lon"], "timestamp": t2.isoformat()},
                                "distance_km": round(dist_km, 2),
                                "time_delta_seconds": dt_sec,
                            },
                            detected_at=t2,
                            latitude=r2["_lat"],
                            longitude=r2["_lon"],
                        )
                    )
                    return anomalies  # One duplicate anomaly per MMSI suffices

        return anomalies

    def _detect_impossible_jumps(
        self,
        mmsi: str,
        records: list[dict[str, Any]],
        max_speed_knots: float = 55.0,
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 2:
            return anomalies

        for i in range(len(records) - 1):
            r1 = records[i]
            r2 = records[i + 1]
            t1 = r1["_parsed_ts"]
            t2 = r2["_parsed_ts"]
            if not t1 or not t2:
                continue

            dt_sec = (t2 - t1).total_seconds()
            # If transmissions are within 15 seconds, GPS jitter might cause high apparent speed
            if dt_sec < 15.0:
                continue

            dist_km = haversine_distance_km(r1["_lat"], r1["_lon"], r2["_lat"], r2["_lon"])
            implied_knots = (dist_km / (dt_sec / 3600.0)) * 0.539957

            if implied_knots > max_speed_knots:
                sev = IdentityAnomalySeverity.CRITICAL if implied_knots > 90.0 else IdentityAnomalySeverity.HIGH
                anomalies.append(
                    IdentityAnomaly(
                        anomaly_id=f"anom-jump-{mmsi}-{i}",
                        anomaly_type=IdentityAnomalyType.IMPOSSIBLE_JUMP,
                        mmsi=mmsi,
                        vessel_name=r2.get("vessel_name") or r1.get("vessel_name"),
                        severity=sev,
                        confidence=0.95,
                        description=(
                            f"Kinematically impossible jump: MMSI {mmsi} traveled {dist_km:.1f} km in "
                            f"{dt_sec/60.0:.1f} minutes, requiring speed of {implied_knots:.1f} knots "
                            f"(threshold: {max_speed_knots:.1f} kn)."
                        ),
                        evidence={
                            "implied_speed_knots": round(implied_knots, 1),
                            "distance_km": round(dist_km, 2),
                            "elapsed_minutes": round(dt_sec / 60.0, 1),
                            "from_lat": r1["_lat"],
                            "from_lon": r1["_lon"],
                            "to_lat": r2["_lat"],
                            "to_lon": r2["_lon"],
                        },
                        detected_at=t2,
                        latitude=r2["_lat"],
                        longitude=r2["_lon"],
                    )
                )
                anomalies.append(
                    IdentityAnomaly(
                        anomaly_id=f"anom-speed-{mmsi}-{i}",
                        anomaly_type=IdentityAnomalyType.IMPOSSIBLE_SPEED,
                        mmsi=mmsi,
                        vessel_name=r2.get("vessel_name") or r1.get("vessel_name"),
                        severity=sev,
                        confidence=0.95,
                        description=(
                            f"Impossible speed detected: MMSI {mmsi} implied speed of {implied_knots:.1f} knots "
                            f"exceeds vessel kinematic limit of {max_speed_knots:.1f} knots."
                        ),
                        evidence={
                            "implied_speed_knots": round(implied_knots, 1),
                            "distance_km": round(dist_km, 2),
                            "elapsed_minutes": round(dt_sec / 60.0, 1),
                            "speed_limit_knots": max_speed_knots,
                        },
                        detected_at=t2,
                        latitude=r2["_lat"],
                        longitude=r2["_lon"],
                    )
                )

        return anomalies

    def _detect_ais_gaps(
        self,
        mmsi: str,
        records: list[dict[str, Any]],
        gap_hours_threshold: float = 2.0,
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 2:
            return anomalies

        threshold_sec = gap_hours_threshold * 3600.0

        for i in range(len(records) - 1):
            r1 = records[i]
            r2 = records[i + 1]
            t1 = r1["_parsed_ts"]
            t2 = r2["_parsed_ts"]
            if not t1 or not t2:
                continue

            dt_sec = (t2 - t1).total_seconds()
            if dt_sec >= threshold_sec:
                gap_hours = dt_sec / 3600.0
                dist_km = haversine_distance_km(r1["_lat"], r1["_lon"], r2["_lat"], r2["_lon"])
                s1 = float(r1.get("speed") or r1.get("sog") or 0.0)
                s2 = float(r2.get("speed") or r2.get("sog") or 0.0)

                # Flag if active voyage (either speed > 2.0 kn or significant distance)
                if s1 > 2.0 or s2 > 2.0 or dist_km > 10.0:
                    anomalies.append(
                        IdentityAnomaly(
                            anomaly_id=f"anom-gap-{mmsi}-{i}",
                            anomaly_type=IdentityAnomalyType.AIS_GAP,
                            mmsi=mmsi,
                            vessel_name=r2.get("vessel_name") or r1.get("vessel_name"),
                            severity=IdentityAnomalySeverity.HIGH if gap_hours >= 6.0 else IdentityAnomalySeverity.MEDIUM,
                            confidence=0.90,
                            description=(
                                f"Unannounced AIS blackout: MMSI {mmsi} went silent for {gap_hours:.1f} hours "
                                f"during transit ({dist_km:.1f} km gap displacement)."
                            ),
                            evidence={
                                "gap_hours": round(gap_hours, 2),
                                "gap_distance_km": round(dist_km, 2),
                                "last_seen": t1.isoformat(),
                                "reappeared": t2.isoformat(),
                                "last_position": {"lat": r1["_lat"], "lon": r1["_lon"]},
                                "reappear_position": {"lat": r2["_lat"], "lon": r2["_lon"]},
                            },
                            detected_at=t2,
                            latitude=r2["_lat"],
                            longitude=r2["_lon"],
                        )
                    )

        return anomalies

    def _detect_loitering(
        self,
        mmsi: str,
        records: list[dict[str, Any]],
        max_speed_knots: float = 2.5,
        min_hours: float = 1.5,
        designated_anchorages: Optional[list[dict[str, Any]]] = None,
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 3:
            return anomalies

        # Find sustained spans where speed is below max_speed_knots
        current_span: list[dict[str, Any]] = []

        for r in records:
            spd = float(r.get("speed") or r.get("sog") or 0.0)
            if spd <= max_speed_knots:
                current_span.append(r)
            else:
                if len(current_span) >= 3:
                    self._evaluate_loitering_span(
                        mmsi, current_span, min_hours, designated_anchorages, anomalies
                    )
                current_span = []

        if len(current_span) >= 3:
            self._evaluate_loitering_span(
                mmsi, current_span, min_hours, designated_anchorages, anomalies
            )

        return anomalies

    def _evaluate_loitering_span(
        self,
        mmsi: str,
        span: list[dict[str, Any]],
        min_hours: float,
        designated_anchorages: Optional[list[dict[str, Any]]],
        anomalies: list[IdentityAnomaly],
    ) -> None:
        t_start = span[0]["_parsed_ts"]
        t_end = span[-1]["_parsed_ts"]
        if not t_start or not t_end:
            return

        duration_hours = (t_end - t_start).total_seconds() / 3600.0
        if duration_hours < min_hours:
            return

        # Check drift radius
        center_lat = sum(r["_lat"] for r in span) / len(span)
        center_lon = sum(r["_lon"] for r in span) / len(span)
        max_drift_km = max(
            haversine_distance_km(center_lat, center_lon, r["_lat"], r["_lon"])
            for r in span
        )

        # Check if inside designated anchorage
        if designated_anchorages:
            for anch in designated_anchorages:
                a_lat = anch.get("latitude")
                a_lon = anch.get("longitude")
                radius = anch.get("radius_km", 5.0)
                if a_lat is not None and a_lon is not None:
                    if haversine_distance_km(center_lat, center_lon, float(a_lat), float(a_lon)) <= radius:
                        return  # Inside legitimate anchorage

        latest = span[-1]
        anomalies.append(
            IdentityAnomaly(
                anomaly_id=f"anom-loiter-{mmsi}-{len(anomalies)+1}",
                anomaly_type=IdentityAnomalyType.LOITERING,
                mmsi=mmsi,
                vessel_name=latest.get("vessel_name"),
                severity=IdentityAnomalySeverity.MEDIUM,
                confidence=0.86,
                description=(
                    f"Anomalous open-water loitering: MMSI {mmsi} drifted at < 2.5 knots for "
                    f"{duration_hours:.1f} hours outside designated port/anchorage zones."
                ),
                evidence={
                    "duration_hours": round(duration_hours, 2),
                    "drift_radius_km": round(max_drift_km, 2),
                    "start_time": t_start.isoformat(),
                    "end_time": t_end.isoformat(),
                    "center_lat": round(center_lat, 6),
                    "center_lon": round(center_lon, 6),
                },
                detected_at=t_end,
                latitude=center_lat,
                longitude=center_lon,
            )
        )

    def _detect_teleportation(
        self,
        mmsi: str,
        records: list[dict[str, Any]],
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 2:
            return anomalies

        for i in range(len(records) - 1):
            r1 = records[i]
            r2 = records[i + 1]
            t1 = r1["_parsed_ts"]
            t2 = r2["_parsed_ts"]
            if not t1 or not t2:
                continue

            dt_sec = abs((t2 - t1).total_seconds())
            if dt_sec < 1.0:
                continue

            dist_km = haversine_distance_km(r1["_lat"], r1["_lon"], r2["_lat"], r2["_lon"])
            implied_knots = (dist_km / (dt_sec / 3600.0)) * 0.539957

            # Teleportation condition: extreme jump (> 140 knots, or > 100 km in <= 30 mins)
            if implied_knots >= 140.0 or (dist_km >= 100.0 and dt_sec <= 1800.0):
                anomalies.append(
                    IdentityAnomaly(
                        anomaly_id=f"anom-teleport-{mmsi}-{i}",
                        anomaly_type=IdentityAnomalyType.TELEPORTATION,
                        mmsi=mmsi,
                        vessel_name=r2.get("vessel_name") or r1.get("vessel_name"),
                        severity=IdentityAnomalySeverity.CRITICAL,
                        confidence=0.98,
                        description=(
                            f"Kinematic teleportation anomaly: MMSI {mmsi} displaced by {dist_km:.1f} km "
                            f"in {dt_sec/60.0:.1f} minutes (implied speed: {implied_knots:.1f} knots), "
                            f"indicating coordinate corruption or GNSS spoofing."
                        ),
                        evidence={
                            "implied_speed_knots": round(implied_knots, 1),
                            "distance_km": round(dist_km, 2),
                            "time_delta_seconds": dt_sec,
                            "from_lat": r1["_lat"],
                            "from_lon": r1["_lon"],
                            "to_lat": r2["_lat"],
                            "to_lon": r2["_lon"],
                        },
                        detected_at=t2,
                        latitude=r2["_lat"],
                        longitude=r2["_lon"],
                    )
                )

        return anomalies

    def _detect_course_discontinuities(
        self,
        mmsi: str,
        records: list[dict[str, Any]],
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 2:
            return anomalies

        for i in range(len(records) - 1):
            r1 = records[i]
            r2 = records[i + 1]
            t1 = r1["_parsed_ts"]
            t2 = r2["_parsed_ts"]
            if not t1 or not t2:
                continue

            dt_sec = abs((t2 - t1).total_seconds())
            if dt_sec < 5.0 or dt_sec > 900.0:
                continue

            s1 = float(r1.get("speed") or r1.get("sog") or 0.0)
            s2 = float(r2.get("speed") or r2.get("sog") or 0.0)
            avg_speed = max(s1, s2)

            # 1. Sudden COG change at speed
            cog1 = r1.get("cog") or r1.get("course")
            cog2 = r2.get("cog") or r2.get("course")
            if cog1 is not None and cog2 is not None and avg_speed >= 8.0:
                try:
                    c1 = float(cog1) % 360.0
                    c2 = float(cog2) % 360.0
                    diff = abs(c1 - c2)
                    turn_deg = min(diff, 360.0 - diff)
                    if turn_deg >= 85.0 and dt_sec <= 300.0:
                        anomalies.append(
                            IdentityAnomaly(
                                anomaly_id=f"anom-course-{mmsi}-{i}",
                                anomaly_type=IdentityAnomalyType.COURSE_DISCONTINUITY,
                                mmsi=mmsi,
                                vessel_name=r2.get("vessel_name") or r1.get("vessel_name"),
                                severity=IdentityAnomalySeverity.HIGH,
                                confidence=0.89,
                                description=(
                                    f"Severe course discontinuity: MMSI {mmsi} made an abrupt {turn_deg:.1f}° "
                                    f"turn in {dt_sec:.0f}s while steaming at {avg_speed:.1f} knots without deceleration."
                                ),
                                evidence={
                                    "course_delta_deg": round(turn_deg, 1),
                                    "speed_knots": round(avg_speed, 1),
                                    "time_delta_seconds": dt_sec,
                                    "previous_cog": round(c1, 1),
                                    "new_cog": round(c2, 1),
                                },
                                detected_at=t2,
                                latitude=r2["_lat"],
                                longitude=r2["_lon"],
                            )
                        )
                        continue
                except (ValueError, TypeError):
                    pass

            # 2. Heading vs COG divergence
            h2 = r2.get("heading")
            c2_val = r2.get("cog") or r2.get("course")
            if h2 is not None and c2_val is not None and s2 >= 6.0:
                try:
                    h_val = float(h2) % 360.0
                    c_val = float(c2_val) % 360.0
                    diff_hc = abs(h_val - c_val)
                    dev_deg = min(diff_hc, 360.0 - diff_hc)
                    if dev_deg >= 65.0:
                        anomalies.append(
                            IdentityAnomaly(
                                anomaly_id=f"anom-heading-cog-{mmsi}-{i}",
                                anomaly_type=IdentityAnomalyType.COURSE_DISCONTINUITY,
                                mmsi=mmsi,
                                vessel_name=r2.get("vessel_name"),
                                severity=IdentityAnomalySeverity.HIGH,
                                confidence=0.87,
                                description=(
                                    f"Heading vs COG divergence: MMSI {mmsi} heading ({h_val:.1f}°) diverges by {dev_deg:.1f}° "
                                    f"from Course Over Ground ({c_val:.1f}°) at {s2:.1f} knots."
                                ),
                                evidence={
                                    "heading_deg": round(h_val, 1),
                                    "cog_deg": round(c_val, 1),
                                    "divergence_deg": round(dev_deg, 1),
                                    "speed_knots": round(s2, 1),
                                },
                                detected_at=t2,
                                latitude=r2["_lat"],
                                longitude=r2["_lon"],
                            )
                        )
                except (ValueError, TypeError):
                    pass

        return anomalies

    def _detect_unusual_routes(
        self,
        mmsi: str,
        records: list[dict[str, Any]],
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        if len(records) < 4:
            return anomalies

        total_path_km = 0.0
        for i in range(len(records) - 1):
            total_path_km += haversine_distance_km(
                records[i]["_lat"], records[i]["_lon"], records[i + 1]["_lat"], records[i + 1]["_lon"]
            )

        direct_km = haversine_distance_km(
            records[0]["_lat"], records[0]["_lon"], records[-1]["_lat"], records[-1]["_lon"]
        )

        if total_path_km >= 12.0 and direct_km >= 0.05:
            sinuosity = total_path_km / direct_km
            if sinuosity >= 2.3:
                latest = records[-1]
                anomalies.append(
                    IdentityAnomaly(
                        anomaly_id=f"anom-route-{mmsi}-{len(anomalies)+1}",
                        anomaly_type=IdentityAnomalyType.UNUSUAL_ROUTE,
                        mmsi=mmsi,
                        vessel_name=latest.get("vessel_name"),
                        severity=IdentityAnomalySeverity.MEDIUM,
                        confidence=0.84,
                        description=(
                            f"Unusual route / high sinuosity: MMSI {mmsi} traversed {total_path_km:.1f} km "
                            f"for a net displacement of only {direct_km:.1f} km (sinuosity ratio: {sinuosity:.2f}), "
                            f"indicating erratic wandering, evasion, or search behavior."
                        ),
                        evidence={
                            "total_path_km": round(total_path_km, 2),
                            "direct_displacement_km": round(direct_km, 2),
                            "sinuosity_ratio": round(sinuosity, 2),
                            "points_evaluated": len(records),
                        },
                        detected_at=latest["_parsed_ts"] or datetime.now(timezone.utc),
                        latitude=latest.get("_lat"),
                        longitude=latest.get("_lon"),
                    )
                )

        return anomalies

    def _detect_convoys(
        self,
        valid_groups: dict[str, list[dict[str, Any]]],
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        mmsi_keys = list(valid_groups.keys())
        if len(mmsi_keys) < 2:
            return anomalies

        checked_pairs: set[tuple[str, str]] = set()

        for idx1 in range(len(mmsi_keys)):
            m1 = mmsi_keys[idx1]
            rec1 = valid_groups[m1]
            for idx2 in range(idx1 + 1, len(mmsi_keys)):
                m2 = mmsi_keys[idx2]
                rec2 = valid_groups[m2]
                pair_key = (min(m1, m2), max(m1, m2))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)

                matched_pairs = []
                for p1 in rec1:
                    t1 = _parse_utc_timestamp(p1.get("timestamp")) or p1.get("_parsed_ts")
                    lat1 = float(p1.get("latitude") or p1.get("_lat") or 0.0)
                    lon1 = float(p1.get("longitude") or p1.get("_lon") or 0.0)
                    if not t1 or lat1 == 0.0:
                        continue
                    for p2 in rec2:
                        t2 = _parse_utc_timestamp(p2.get("timestamp")) or p2.get("_parsed_ts")
                        lat2 = float(p2.get("latitude") or p2.get("_lat") or 0.0)
                        lon2 = float(p2.get("longitude") or p2.get("_lon") or 0.0)
                        if not t2 or lat2 == 0.0:
                            continue
                        dt = abs((t2 - t1).total_seconds())
                        if dt <= 180.0:
                            dist_km = haversine_distance_km(lat1, lon1, lat2, lon2)
                            if 0.15 <= dist_km <= 4.5:
                                s1 = float(p1.get("speed") or p1.get("sog") or 0.0)
                                s2 = float(p2.get("speed") or p2.get("sog") or 0.0)
                                if s1 >= 3.0 and s2 >= 3.0 and abs(s1 - s2) <= 2.5:
                                    c1 = p1.get("cog") or p1.get("course") or p1.get("heading")
                                    c2 = p2.get("cog") or p2.get("course") or p2.get("heading")
                                    if c1 is not None and c2 is not None:
                                        c_diff = abs(float(c1) - float(c2)) % 360.0
                                        c_ang = min(c_diff, 360.0 - c_diff)
                                        if c_ang <= 20.0:
                                            matched_pairs.append((p1, p2, dist_km, s1, s2, c_ang, lat1, lon1, t1))

                if len(matched_pairs) >= 2:
                    p1_last, p2_last, last_dist, sp1, sp2, ang, l_lat, l_lon, l_ts = matched_pairs[-1]
                    anomalies.append(
                        IdentityAnomaly(
                            anomaly_id=f"anom-convoy-{m1}-{m2}",
                            anomaly_type=IdentityAnomalyType.CONVOY,
                            mmsi=m1,
                            vessel_name=p1_last.get("vessel_name") or p1_last.get("name"),
                            severity=IdentityAnomalySeverity.MEDIUM,
                            confidence=0.88,
                            description=(
                                f"Coordinated convoy formation: MMSI {m1} and MMSI {m2} navigating in close formation "
                                f"({last_dist:.2f} km separation) with matched speed ({sp1:.1f} kn vs {sp2:.1f} kn) "
                                f"and course alignment (Δ={ang:.1f}°)."
                            ),
                            evidence={
                                "partner_mmsi": m2,
                                "partner_vessel_name": p2_last.get("vessel_name") or p2_last.get("name"),
                                "distance_km": round(last_dist, 2),
                                "speed_1_knots": round(sp1, 1),
                                "speed_2_knots": round(sp2, 1),
                                "course_diff_deg": round(ang, 1),
                                "coordinated_observations": len(matched_pairs),
                            },
                            detected_at=l_ts,
                            latitude=l_lat,
                            longitude=l_lon,
                        )
                    )

        return anomalies

    def _detect_rendezvous(
        self,
        valid_groups: dict[str, list[dict[str, Any]]],
        designated_anchorages: Optional[list[dict[str, Any]]] = None,
    ) -> list[IdentityAnomaly]:
        anomalies: list[IdentityAnomaly] = []
        mmsi_keys = list(valid_groups.keys())
        if len(mmsi_keys) < 2:
            return anomalies

        checked_pairs: set[tuple[str, str]] = set()

        for idx1 in range(len(mmsi_keys)):
            m1 = mmsi_keys[idx1]
            rec1 = valid_groups[m1]
            for idx2 in range(idx1 + 1, len(mmsi_keys)):
                m2 = mmsi_keys[idx2]
                rec2 = valid_groups[m2]
                pair_key = (min(m1, m2), max(m1, m2))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)

                for p1 in rec1:
                    t1 = _parse_utc_timestamp(p1.get("timestamp")) or p1.get("_parsed_ts")
                    lat1 = float(p1.get("latitude") or p1.get("_lat") or 0.0)
                    lon1 = float(p1.get("longitude") or p1.get("_lon") or 0.0)
                    if not t1 or lat1 == 0.0:
                        continue
                    for p2 in rec2:
                        t2 = _parse_utc_timestamp(p2.get("timestamp")) or p2.get("_parsed_ts")
                        lat2 = float(p2.get("latitude") or p2.get("_lat") or 0.0)
                        lon2 = float(p2.get("longitude") or p2.get("_lon") or 0.0)
                        if not t2 or lat2 == 0.0:
                            continue
                        dt = abs((t2 - t1).total_seconds())
                        if dt <= 180.0:
                            dist_km = haversine_distance_km(lat1, lon1, lat2, lon2)
                            if dist_km <= 0.6:  # Under 600m
                                s1 = float(p1.get("speed") or p1.get("sog") or 0.0)
                                s2 = float(p2.get("speed") or p2.get("sog") or 0.0)
                                if s1 <= 3.5 and s2 <= 3.5:
                                    in_anchorage = False
                                    if designated_anchorages:
                                        for anch in designated_anchorages:
                                            alat = anch.get("latitude")
                                            alon = anch.get("longitude")
                                            rad = anch.get("radius_km", 5.0)
                                            if alat is not None and alon is not None:
                                                if haversine_distance_km(lat1, lon1, float(alat), float(alon)) <= rad:
                                                    in_anchorage = True
                                                    break
                                    if not in_anchorage:
                                        anomalies.append(
                                            IdentityAnomaly(
                                                anomaly_id=f"anom-rdvz-{m1}-{m2}",
                                                anomaly_type=IdentityAnomalyType.RENDEZVOUS,
                                                mmsi=m1,
                                                vessel_name=p1.get("vessel_name") or p1.get("name"),
                                                severity=IdentityAnomalySeverity.HIGH,
                                                confidence=0.91,
                                                description=(
                                                    f"Ship-to-Ship rendezvous / transshipment pattern: MMSI {m1} and MMSI {m2} "
                                                    f"in close proximity ({dist_km*1000.0:.0f}m) at low speed outside designated zones."
                                                ),
                                                evidence={
                                                    "partner_mmsi": m2,
                                                    "partner_vessel_name": p2.get("vessel_name") or p2.get("name"),
                                                    "separation_meters": round(dist_km * 1000.0, 1),
                                                    "speed_1_knots": round(s1, 1),
                                                    "speed_2_knots": round(s2, 1),
                                                },
                                                detected_at=t1,
                                                latitude=lat1,
                                                longitude=lon1,
                                            )
                                        )
                                        break
                    if any(a.anomaly_id == f"anom-rdvz-{m1}-{m2}" for a in anomalies):
                        break

        return anomalies
