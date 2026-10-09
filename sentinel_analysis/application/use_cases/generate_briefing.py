"""Use case for generating decision-ready maritime intelligence briefs."""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.briefing import IntelligenceBriefGenerator
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import Scan

logger = logging.getLogger(__name__)


def extract_scan_intelligence(scan: Scan) -> dict[str, Any]:
    """Extract, discover, and normalize vessel detections and intelligence metrics for a scan.

    Handles multiple storage locations:
    1. Direct in-memory list in scan.metadata["detections"]
    2. latest_cv_results pointer and detection JSON files on disk (*_detections.json, detection_results.json)
    3. GeoJSON FeatureCollection files (*_detections.geojson, detections.geojson)

    Normalizes heterogeneous detection keys (lat/latitude, lng/longitude, length/length_m,
    is_dark_vessel/is_dark, center_x/pixel_x) into standard structures.
    """
    metadata = scan.metadata or {}
    raw_detections: list[Any] = []
    ghost_vessels: list[dict[str, Any]] = list(metadata.get("ghost_vessels", []))

    # 1. Check if detections are already present in metadata
    meta_dets = metadata.get("detections")
    if isinstance(meta_dets, list) and len(meta_dets) > 0:
        raw_detections = meta_dets

    # 2. If not in metadata, discover detection files on disk
    if not raw_detections:
        latest_cv = metadata.get("latest_cv_results") or {}
        image_path = Path(scan.image_path) if scan.image_path else None

        candidates: list[Path] = []
        if image_path:
            det_json_name = latest_cv.get("detections_json")
            if det_json_name:
                candidates.extend([
                    image_path.parent / det_json_name,
                    image_path.parent.parent / det_json_name,
                    image_path.parent.parent / "images" / det_json_name,
                ])

            # Standard detection file conventions
            candidates.extend([
                image_path.parent / f"{image_path.stem}_detections.json",
                image_path.parent / "detection_results.json",
                image_path.parent.parent / "detection_results.json",
                image_path.parent.parent / "images" / "detection_results.json",
                image_path.parent / f"{image_path.stem}_detections.geojson",
                image_path.parent / "detection_results.geojson",
                image_path.parent / "detections.geojson",
            ])

        for cand in candidates:
            if cand.is_file():
                try:
                    with open(cand, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        if data.get("type") == "FeatureCollection" and "features" in data:
                            features = data.get("features", [])
                            cand_dets = []
                            for feat in features:
                                props = dict(feat.get("properties") or {})
                                geom = feat.get("geometry") or {}
                                coords = geom.get("coordinates") or []
                                if geom.get("type") == "Point" and len(coords) >= 2:
                                    props.setdefault("lng", coords[0])
                                    props.setdefault("lat", coords[1])
                                cand_dets.append(props)
                            raw_detections = cand_dets
                            break
                        elif "detections" in data:
                            raw_detections = list(data.get("detections") or [])
                            if not ghost_vessels and data.get("ghost_vessels"):
                                ghost_vessels = list(data.get("ghost_vessels") or [])
                            break
                    elif isinstance(data, list):
                        raw_detections = data
                        break
                except Exception as exc:
                    logger.debug("Failed reading candidate detection file %s: %s", cand, exc)

    # 3. Normalize individual detections
    normalized_detections: list[dict[str, Any]] = []
    for idx, d in enumerate(raw_detections):
        if not isinstance(d, dict):
            continue

        ais = d.get("correlated_ais") if isinstance(d.get("correlated_ais"), dict) else {}

        # Coordinate resolution
        lat_val = d.get("latitude") if d.get("latitude") is not None else (
            d.get("lat") if d.get("lat") is not None else ais.get("latitude")
        )
        lon_val = d.get("longitude") if d.get("longitude") is not None else (
            d.get("lng") if d.get("lng") is not None else (
                d.get("lon") if d.get("lon") is not None else ais.get("longitude")
            )
        )
        try:
            lat = float(lat_val) if lat_val is not None else None
        except (ValueError, TypeError):
            lat = None
        try:
            lon = float(lon_val) if lon_val is not None else None
        except (ValueError, TypeError):
            lon = None

        # Dimensional resolution
        try:
            length_m = float(d.get("length_m") or d.get("length") or d.get("est_length") or ais.get("length") or 0.0)
        except (ValueError, TypeError):
            length_m = 0.0
        try:
            width_m = float(d.get("width_m") or d.get("beam") or d.get("width") or ais.get("width") or 0.0)
        except (ValueError, TypeError):
            width_m = 0.0

        # Pixel coordinates
        try:
            px = float(d.get("pixel_x") or d.get("center_x") or d.get("x") or 0.0)
        except (ValueError, TypeError):
            px = 0.0
        try:
            py = float(d.get("pixel_y") or d.get("center_y") or d.get("y") or 0.0)
        except (ValueError, TypeError):
            py = 0.0

        # Confidence
        try:
            conf = float(d.get("confidence") or 0.0)
        except (ValueError, TypeError):
            conf = 0.0

        # Dark vessel classification
        if "is_dark" in d:
            is_dark = bool(d["is_dark"])
        elif "is_dark_vessel" in d:
            is_dark = bool(d["is_dark_vessel"])
        elif "is_correlated" in d:
            is_dark = not bool(d["is_correlated"])
        elif ais:
            is_dark = False
        else:
            is_dark = True

        is_correlated = bool(d.get("is_correlated", not is_dark))

        # IMO SOLAS Chapter V Regulation 19: Vessels >= 45m (approx >= 300 GT) must broadcast AIS
        is_solas_suspect = is_dark and (length_m >= 45.0)

        # Vessel identification details
        vessel_name = (
            ais.get("vessel_name")
            or ais.get("name")
            or d.get("vessel_name")
            or d.get("name")
            or None
        )
        raw_mmsi = ais.get("mmsi") or d.get("mmsi")
        mmsi = str(raw_mmsi) if raw_mmsi is not None else None

        vessel_type = (
            ais.get("vessel_type")
            or ais.get("ship_type")
            or d.get("vessel_class")
            or d.get("estimated_class")
            or ("Commercial" if is_correlated else ("Suspect Vessel" if is_solas_suspect else "Unknown Target"))
        )

        risk_category = d.get("dark_vessel_risk")
        if not risk_category:
            if is_solas_suspect and length_m >= 80.0:
                risk_category = "CRITICAL"
            elif is_solas_suspect:
                risk_category = "HIGH"
            elif is_dark:
                risk_category = "ELEVATED"
            else:
                risk_category = "NOMINAL"

        reasons = d.get("dark_vessel_reasons")
        if not reasons or not isinstance(reasons, list):
            if is_solas_suspect:
                reasons = ["SOLAS Reg 19: Uncooperative vessel (>=45m)"]
            elif is_dark:
                reasons = ["Unidentified radar contact (no AIS correlation)"]
            else:
                reasons = []

        normalized_detections.append({
            "id": idx + 1,
            "index": d.get("index", idx),
            "pixel_x": px,
            "pixel_y": py,
            "latitude": lat,
            "longitude": lon,
            "length_m": round(length_m, 1),
            "width_m": round(width_m, 1),
            "confidence": conf,
            "is_dark": is_dark,
            "is_correlated": is_correlated,
            "is_solas_suspect": is_solas_suspect,
            "vessel_name": vessel_name,
            "mmsi": mmsi,
            "vessel_type": vessel_type,
            "dark_vessel_risk": risk_category,
            "dark_vessel_reasons": reasons,
            "optical_status": d.get("optical_status"),
            "optical_confirmed": d.get("optical_confirmed"),
            "optical_confidence": d.get("optical_confidence"),
            "temporal_change_type": d.get("temporal_change_type"),
            "correlated_ais": ais,
            "raw_detection": d,
        })

    total_vessels = len(normalized_detections)
    dark_vessels = sum(1 for d in normalized_detections if d["is_dark"])
    ais_correlated = total_vessels - dark_vessels
    dark_ratio = (dark_vessels / total_vessels * 100.0) if total_vessels > 0 else 0.0
    solas_violations = sum(1 for d in normalized_detections if d["is_solas_suspect"])

    high_risk_targets = [
        d for d in normalized_detections
        if d["is_solas_suspect"] or (d["is_dark"] and (d["length_m"] >= 40.0 or d["confidence"] >= 0.85))
    ]

    return {
        "scan_folder": scan.folder_name,
        "aoi_name": metadata.get("aoi_name", "Target Area"),
        "acquisition_time": scan.acquisition.acquired_at.isoformat(),
        "satellite": scan.acquisition.satellite,
        "total_vessels": total_vessels,
        "dark_vessels": dark_vessels,
        "ais_correlated": ais_correlated,
        "dark_percentage": round(dark_ratio, 1),
        "solas_suspect_count": solas_violations,
        "high_risk_count": len(high_risk_targets),
        "high_risk_targets": high_risk_targets,
        "detections": normalized_detections,
        "ghost_vessels": ghost_vessels,
    }


class GenerateIntelligenceBrief:
    """Orchestrates generation of comprehensive PDF intelligence briefing packets."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        brief_generator: IntelligenceBriefGenerator,
    ) -> None:
        self._scan_repository = scan_repository
        self._brief_generator = brief_generator

    def execute(self, folder_name: str, target_path: Optional[Path] = None) -> Path:
        """Generate and save the PDF intelligence briefing packet for the given scan."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")

        if target_path is None:
            image_path = Path(scan.image_path)
            target_path = image_path.parent / f"{scan.folder_name}_briefing.pdf"

        return self._brief_generator.generate_brief(scan, target_path)

    def generate_summary(self, folder_name: str) -> dict[str, Any]:
        """Compute an intelligence executive summary without rendering the full PDF."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")

        intel = extract_scan_intelligence(scan)
        return {
            "scan_folder": intel["scan_folder"],
            "aoi_name": intel["aoi_name"],
            "acquisition_time": intel["acquisition_time"],
            "satellite": intel["satellite"],
            "total_vessels": intel["total_vessels"],
            "dark_vessels": intel["dark_vessels"],
            "ais_correlated": intel["ais_correlated"],
            "dark_percentage": intel["dark_percentage"],
            "solas_suspect_count": intel["solas_suspect_count"],
            "high_risk_count": intel["high_risk_count"],
            "high_risk_targets": [
                {
                    "id": t["id"],
                    "latitude": t["latitude"],
                    "longitude": t["longitude"],
                    "length_m": t["length_m"],
                    "confidence": t["confidence"],
                    "is_solas_suspect": t["is_solas_suspect"],
                }
                for t in intel["high_risk_targets"]
            ],
        }

