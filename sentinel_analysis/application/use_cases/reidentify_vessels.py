"""Application use case for multi-pass satellite vessel re-identification."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.coordinates import pixel_to_geo, resolve_image_transform
from sentinel_analysis.domain.entities import BoundingBox, Scan
from sentinel_analysis.domain.reidentification import (
    PassDetectionCandidate,
    ReIDMatchStatus,
    VesselReIDMatch,
    calculate_reid_similarity,
)

logger = logging.getLogger(__name__)


def _parse_timestamp(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class ReidentifyVessels:
    """Matches vessel detections across satellite passes using size, heading, and kinematics."""

    def __init__(self, scan_repository: Optional[ScanRepository] = None) -> None:
        self._scan_repository = scan_repository

    def execute(
        self,
        primary_scan_id: Optional[str] = None,
        reference_scan_id: Optional[str] = None,
        primary_candidates: Optional[list[dict[str, Any]]] = None,
        reference_candidates: Optional[list[dict[str, Any]]] = None,
        min_similarity: float = 0.45,
        confirmed_threshold: float = 0.70,
        max_time_hours: float = 72.0,
        max_speed_knots: float = 35.0,
        scan_id: Optional[str] = None,
        prior_scan_id: Optional[str] = None,
        current_candidates: Optional[list[dict[str, Any]]] = None,
        prior_candidates: Optional[list[dict[str, Any]]] = None,
        max_time_delta_hours: Optional[float] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform multi-pass vessel re-identification between two passes."""
        primary_id = primary_scan_id or scan_id
        ref_id = reference_scan_id or prior_scan_id
        cands_prim = primary_candidates or current_candidates
        cands_ref = reference_candidates or prior_candidates
        max_hrs = max_time_delta_hours if max_time_delta_hours is not None else max_time_hours

        # 1. Resolve candidates from repository or provided lists
        cands_1, scan_1_meta = self._resolve_candidates(
            scan_id=primary_id,
            provided_candidates=cands_prim,
            default_scan_id="pass_1",
        )

        if not ref_id and not cands_ref and primary_id and self._scan_repository:
            # Auto-discover previous scan in same AOI
            ref_id = self._find_prior_overlapping_scan(primary_id, max_time_hours=max_hrs)

        cands_2, scan_2_meta = self._resolve_candidates(
            scan_id=ref_id,
            provided_candidates=cands_ref,
            default_scan_id="pass_2",
        )

        if not cands_1 or not cands_2:
            return {
                "status": "success",
                "primary_scan": primary_scan_id or "pass_1",
                "reference_scan": ref_id or "pass_2",
                "total_primary_candidates": len(cands_1),
                "total_reference_candidates": len(cands_2),
                "total_matches": 0,
                "confirmed_matches_count": 0,
                "plausible_matches_count": 0,
                "matches": [],
                "unmatched_primary": [c.to_dict() for c in cands_1],
                "unmatched_reference": [c.to_dict() for c in cands_2],
                "geojson": {"type": "FeatureCollection", "features": []},
            }

        # 2. Pairwise evaluation
        candidate_pair_scores: list[tuple[float, PassDetectionCandidate, PassDetectionCandidate, Any]] = []

        # Track competing candidates for each target_1
        competing_map: dict[str, list[dict[str, Any]]] = {}

        for c1 in cands_1:
            competing_map[c1.detection_id] = []
            for c2 in cands_2:
                sim = calculate_reid_similarity(
                    c1,
                    c2,
                    max_commercial_speed_knots=max_speed_knots,
                )
                competing_map[c1.detection_id].append({
                    "reference_detection_id": c2.detection_id,
                    "overall_score": round(sim.overall_score, 3),
                    "size_score": round(sim.size_score, 3),
                    "kinematic_score": round(sim.kinematic_score, 3),
                    "heading_score": round(sim.heading_score, 3),
                    "distance_km": round(sim.distance_km, 2),
                    "implied_speed_knots": round(sim.implied_speed_knots, 1),
                })

                if sim.overall_score >= min_similarity:
                    candidate_pair_scores.append((sim.overall_score, c1, c2, sim))

        # Sort competing candidates by overall score descending
        for det_id in competing_map:
            competing_map[det_id].sort(key=lambda x: x["overall_score"], reverse=True)

        # 3. Greedy bipartite matching (highest similarity first)
        candidate_pair_scores.sort(key=lambda x: x[0], reverse=True)

        matched_c1_ids: set[str] = set()
        matched_c2_ids: set[str] = set()
        matches: list[VesselReIDMatch] = []

        for score, c1, c2, sim in candidate_pair_scores:
            if c1.detection_id in matched_c1_ids or c2.detection_id in matched_c2_ids:
                continue

            matched_c1_ids.add(c1.detection_id)
            matched_c2_ids.add(c2.detection_id)

            status = (
                ReIDMatchStatus.CONFIRMED
                if score >= confirmed_threshold
                else ReIDMatchStatus.PLAUSIBLE
            )

            explanation = (
                f"Re-identified vessel between {c1.scan_id} (#det {c1.detection_index}) and "
                f"{c2.scan_id} (#det {c2.detection_index}): overall similarity {score:.2f} "
                f"(size={sim.size_score:.2f}, kinematic={sim.kinematic_score:.2f}, heading={sim.heading_score:.2f}) "
                f"across {sim.distance_nm:.1f} NM over {sim.time_delta_hours:.1f}h "
                f"(implied speed: {sim.implied_speed_knots:.1f} kn)."
            )

            # Get runners-up competing candidates
            runners_up = [
                comp for comp in competing_map.get(c1.detection_id, [])
                if comp["reference_detection_id"] != c2.detection_id
            ][:3]

            match_obj = VesselReIDMatch(
                match_id=f"reid-{c1.detection_id}-{c2.detection_id}",
                target_1=c1,
                target_2=c2,
                similarity=sim,
                confidence=score,
                match_status=status,
                explanation=explanation,
                competing_candidates=runners_up,
            )
            matches.append(match_obj)

        # 4. Identify unmatched detections
        unmatched_1 = [c.to_dict() for c in cands_1 if c.detection_id not in matched_c1_ids]
        unmatched_2 = [c.to_dict() for c in cands_2 if c.detection_id not in matched_c2_ids]

        # 5. Build GeoJSON
        geojson_features = self._build_geojson_features(matches, unmatched_1, unmatched_2)

        confirmed_count = sum(1 for m in matches if m.match_status == ReIDMatchStatus.CONFIRMED)
        plausible_count = sum(1 for m in matches if m.match_status == ReIDMatchStatus.PLAUSIBLE)

        result = {
            "status": "success",
            "primary_scan": primary_id or "pass_1",
            "reference_scan": ref_id or "pass_2",
            "current_scan_id": primary_id or "pass_1",
            "prior_scan_id": ref_id or "pass_2",
            "total_primary_candidates": len(cands_1),
            "total_reference_candidates": len(cands_2),
            "total_current_candidates": len(cands_1),
            "total_prior_candidates": len(cands_2),
            "total_matches": len(matches),
            "confirmed_matches_count": confirmed_count,
            "plausible_matches_count": plausible_count,
            "matches": [m.to_dict() for m in matches],
            "unmatched_primary": unmatched_1,
            "unmatched_reference": unmatched_2,
            "geojson": {
                "type": "FeatureCollection",
                "features": geojson_features,
            },
            "geojson_tracks": {
                "type": "FeatureCollection",
                "features": geojson_features,
            },
        }

        # Optional persistence into primary scan folder
        if primary_id and self._scan_repository:
            try:
                scan = self._get_scan(primary_id)
                if scan and scan.image_path:
                    scan_dir = Path(scan.image_path).parent
                    if scan_dir.is_dir() and scan_dir not in (Path("."), Path(""), Path.cwd()):
                        out_path = scan_dir / "reidentification_results.json"
                        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not persist re-identification results: %s", exc)

        return result

    def _get_scan(self, scan_id: str) -> Optional[Scan]:
        if not self._scan_repository or not scan_id:
            return None
        getter = getattr(self._scan_repository, "get", getattr(self._scan_repository, "get_scan", None))
        if getter:
            try:
                return getter(scan_id)
            except Exception:
                return None
        return None

    def _resolve_candidates(
        self,
        scan_id: Optional[str],
        provided_candidates: Optional[list[dict[str, Any]]],
        default_scan_id: str,
    ) -> tuple[list[PassDetectionCandidate], dict[str, Any]]:
        """Extract PassDetectionCandidate list from repository scan or raw dicts."""
        candidates: list[PassDetectionCandidate] = []
        meta: dict[str, Any] = {}

        scan_obj = None
        default_time = None
        if scan_id and self._scan_repository is not None:
            try:
                scan_obj = self._get_scan(scan_id)
                if scan_obj is not None:
                    if hasattr(scan_obj, "acquisition") and scan_obj.acquisition:
                        default_time = scan_obj.acquisition.acquired_at
                    meta["acquired_at"] = default_time.isoformat() if default_time else None
                    meta["aoi"] = getattr(scan_obj, "aoi_name", None)
            except Exception:
                pass

        if provided_candidates is not None:
            for idx, raw in enumerate(provided_candidates):
                cand = self._dict_to_candidate(raw, scan_id or default_scan_id, idx, default_time=default_time, scan_obj=scan_obj)
                if cand:
                    candidates.append(cand)
            return candidates, meta

        if scan_obj is not None:
            try:
                scan_time = default_time or datetime.now(timezone.utc)
                raw_dets: list[dict[str, Any]] = []
                # Try detection_results.json
                if scan_obj.image_path:
                    det_json_path = Path(scan_obj.image_path).parent / "detection_results.json"
                    if det_json_path.is_file():
                        try:
                            d_data = json.loads(det_json_path.read_text(encoding="utf-8"))
                            raw_dets = d_data.get("detections", [])
                        except Exception:
                            pass

                if not raw_dets and hasattr(scan_obj, "detections") and scan_obj.detections:
                    raw_dets = [
                        d if isinstance(d, dict) else (d.to_dict() if hasattr(d, "to_dict") else vars(d))
                        for d in scan_obj.detections
                    ]

                for idx, det in enumerate(raw_dets):
                    cand = self._dict_to_candidate(det, scan_id, idx, default_time=scan_time, scan_obj=scan_obj)
                    if cand:
                        candidates.append(cand)
            except Exception as exc:
                logger.warning("Could not load candidates for scan %s: %s", scan_id, exc)

        return candidates, meta

    def _dict_to_candidate(
        self,
        raw: dict[str, Any],
        scan_id: str,
        idx: int,
        default_time: Optional[datetime] = None,
        scan_obj: Optional[Scan] = None,
    ) -> Optional[PassDetectionCandidate]:
        lat = raw.get("lat") or raw.get("latitude")
        lon = raw.get("lng") or raw.get("lon") or raw.get("longitude")

        # Project coordinates if missing and scan_obj available
        if (lat is None or lon is None) and scan_obj and scan_obj.bbox:
            cx = raw.get("center_x") or (raw.get("x", 0) + raw.get("width", 0) / 2.0)
            cy = raw.get("center_y") or (raw.get("y", 0) + raw.get("height", 0) / 2.0)
            try:
                img_path = getattr(scan_obj, "image_path", None)
                meta_w = scan_obj.metadata.get("image_width") if scan_obj.metadata else None
                meta_h = scan_obj.metadata.get("image_height") if scan_obj.metadata else None
                img_w = int(raw.get("image_width") or meta_w or 1000)
                img_h = int(raw.get("image_height") or meta_h or 1000)
                transform = resolve_image_transform(img_path, bbox=scan_obj.bbox, image_width=img_w, image_height=img_h)
                lat, lon = pixel_to_geo(float(cx), float(cy), bbox=scan_obj.bbox, image_width=img_w, image_height=img_h, transform_fn=transform)
            except Exception:
                pass

        if lat is None or lon is None:
            return None

        try:
            flat = float(lat)
            flon = float(lon)
        except (ValueError, TypeError):
            return None

        ts = _parse_timestamp(raw.get("timestamp") or default_time or datetime.now(timezone.utc))

        length_m = raw.get("length_m") or raw.get("estimated_length_m") or raw.get("length")
        width_m = raw.get("width_m") or raw.get("estimated_beam_m") or raw.get("width")
        heading_deg = raw.get("heading_deg") or raw.get("estimated_heading_deg") or raw.get("orientation") or raw.get("heading")
        speed_knots = raw.get("speed_knots") or raw.get("wake_speed_knots") or raw.get("speed")
        rcs_mean = raw.get("rcs_mean") or raw.get("max_intensity") or raw.get("confidence")

        def _to_float(v: Any) -> Optional[float]:
            if v is None:
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        det_id = str(raw.get("detection_id") or f"{scan_id}_det_{idx}")

        pixel_box = None
        if "x" in raw and "y" in raw:
            pixel_box = {
                "x": float(raw.get("x", 0)),
                "y": float(raw.get("y", 0)),
                "width": float(raw.get("width", 0)),
                "height": float(raw.get("height", 0)),
            }

        return PassDetectionCandidate(
            detection_id=det_id,
            scan_id=scan_id,
            detection_index=idx,
            latitude=flat,
            longitude=flon,
            timestamp=ts,
            length_m=_to_float(length_m),
            width_m=_to_float(width_m),
            heading_deg=_to_float(heading_deg),
            speed_knots=_to_float(speed_knots),
            rcs_mean=_to_float(rcs_mean),
            pixel_box=pixel_box,
            metadata=raw,
        )

    def _find_prior_overlapping_scan(self, primary_scan_id: str, max_time_hours: float = 72.0) -> Optional[str]:
        """Find the most recent earlier scan covering the same AOI or overlapping bounds."""
        if self._scan_repository is None:
            return None
        try:
            primary_scan = self._scan_repository.get_scan(primary_scan_id)
            if not primary_scan:
                return None
            p_time = primary_scan.acquisition.acquired_at if primary_scan.acquisition else None
            if not p_time:
                return None

            scans = self._scan_repository.list_scans()
            candidates = []
            for s in scans:
                if s.folder_name == primary_scan_id:
                    continue
                s_time = s.acquisition.acquired_at if s.acquisition else None
                if not s_time or s_time >= p_time:
                    continue

                dt_h = (p_time - s_time).total_seconds() / 3600.0
                if dt_h > max_time_hours:
                    continue

                # Same AOI or spatial overlap
                if s.aoi_name and primary_scan.aoi_name and s.aoi_name == primary_scan.aoi_name:
                    candidates.append((dt_h, s.folder_name))
                elif s.bbox and primary_scan.bbox:
                    # Check bbox overlap
                    b1, b2 = primary_scan.bbox, s.bbox
                    overlap = not (
                        b1.max_longitude < b2.min_longitude
                        or b1.min_longitude > b2.max_longitude
                        or b1.max_latitude < b2.min_latitude
                        or b1.min_latitude > b2.max_latitude
                    )
                    if overlap:
                        candidates.append((dt_h, s.folder_name))

            if candidates:
                candidates.sort(key=lambda x: x[0])  # Smallest time delta first
                return candidates[0][1]
        except Exception as exc:
            logger.debug("Failed auto-discovering prior scan: %s", exc)

        return None

    def _build_geojson_features(
        self,
        matches: list[VesselReIDMatch],
        unmatched_1: list[dict[str, Any]],
        unmatched_2: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []

        # 1. Matched displacement tracks (LineString)
        for m in matches:
            t1 = m.target_1
            t2 = m.target_2
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(t1.longitude, 6), round(t1.latitude, 6)],
                        [round(t2.longitude, 6), round(t2.latitude, 6)],
                    ],
                },
                "properties": {
                    "feature_type": "REID_DISPLACEMENT_TRACK",
                    "match_id": m.match_id,
                    "match_status": m.match_status.value,
                    "confidence": m.confidence,
                    "overall_score": m.similarity.overall_score,
                    "size_score": m.similarity.size_score,
                    "kinematic_score": m.similarity.kinematic_score,
                    "heading_score": m.similarity.heading_score,
                    "distance_km": m.similarity.distance_km,
                    "distance_nm": m.similarity.distance_nm,
                    "time_delta_hours": m.similarity.time_delta_hours,
                    "implied_speed_knots": m.similarity.implied_speed_knots,
                    "explanation": m.explanation,
                },
            })

            # Point for target 1
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(t1.longitude, 6), round(t1.latitude, 6)],
                },
                "properties": {
                    "feature_type": "REID_PRIMARY_TARGET",
                    "match_id": m.match_id,
                    "detection_id": t1.detection_id,
                    "scan_id": t1.scan_id,
                    "detection_index": t1.detection_index,
                    "length_m": t1.length_m,
                    "heading_deg": t1.heading_deg,
                },
            })

            # Point for target 2
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(t2.longitude, 6), round(t2.latitude, 6)],
                },
                "properties": {
                    "feature_type": "REID_REFERENCE_TARGET",
                    "match_id": m.match_id,
                    "detection_id": t2.detection_id,
                    "scan_id": t2.scan_id,
                    "detection_index": t2.detection_index,
                    "length_m": t2.length_m,
                    "heading_deg": t2.heading_deg,
                },
            })

        # Unmatched primary (new appearances or lost tracks)
        for u1 in unmatched_1:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [u1["longitude"], u1["latitude"]],
                },
                "properties": {
                    "feature_type": "REID_UNMATCHED_PRIMARY",
                    "detection_id": u1["detection_id"],
                    "scan_id": u1["scan_id"],
                    "detection_index": u1["detection_index"],
                },
            })

        for u2 in unmatched_2:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [u2["longitude"], u2["latitude"]],
                },
                "properties": {
                    "feature_type": "REID_UNMATCHED_REFERENCE",
                    "detection_id": u2["detection_id"],
                    "scan_id": u2["scan_id"],
                    "detection_index": u2["detection_index"],
                },
            })

        return features
