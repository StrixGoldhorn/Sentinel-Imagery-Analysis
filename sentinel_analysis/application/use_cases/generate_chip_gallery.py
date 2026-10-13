"""Multimodal Candidate Chip Gallery Generator.

Produces a unified 4-sensor tactical chip gallery around any detection candidate:
1. SAR: High-resolution Synthetic Aperture Radar crop with bounding box, wake vector, and intensity stats.
2. OPTICAL: Concurrent optical validation crop (Sentinel-2 multi-spectral) or tactical cloud-penetration card.
3. PREVIOUS_PASS: Co-registered prior satellite pass showing temporal change (presence/absence).
4. AIS_CONTEXT: 360-degree tactical mini-map with range rings, nearby AIS contacts, and kinematics.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional, Sequence

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.application.ports.imagery import ChipRenderer
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.chip_gallery import (
    CandidateChipGallery,
    ChipArtifact,
    ChipModality,
)
from sentinel_analysis.domain.coordinates import pixel_to_geo
from sentinel_analysis.domain.entities import BoundingBox, Scan

logger = logging.getLogger(__name__)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great Circle distance in meters between two coordinates."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


class GenerateChipGallery:
    """Use case for extracting and rendering multimodal chip galleries around detections."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        ais_repository: Optional[AISRepository] = None,
        sentinel2_client: Optional[Any] = None,
        chip_renderer: Optional[ChipRenderer] = None,
    ) -> None:
        self._scan_repo = scan_repository
        self._ais_repo = ais_repository
        self._sentinel2_client = sentinel2_client
        self._renderer = chip_renderer

    def _get_renderer(self) -> ChipRenderer:
        if self._renderer is not None:
            return self._renderer
        import importlib
        module = importlib.import_module("sentinel_analysis.infrastructure.imagery.chip_renderer")
        self._renderer = module.OpenCVChipRenderer()
        return self._renderer

    def execute(
        self,
        scan_id: str,
        detection_idx: int = 0,
        padding_px: int = 80,
        chip_size: int = 256,
    ) -> dict[str, Any]:
        """Generate a complete 4-modality chip gallery for a single detection."""
        scan = self._scan_repo.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan '{scan_id}' not found")

        # Load detection list
        detections = self._load_detections(scan)
        if not detections:
            # Fallback synthetic detection at scan center if none exist
            det: dict[str, Any] = {
                "x": 100,
                "y": 100,
                "width": 40,
                "height": 40,
                "confidence": 0.5,
                "is_dark": True,
            }
            target_idx = 0
        else:
            target_idx = min(max(0, detection_idx), len(detections) - 1)
            det = dict(detections[target_idx])

        # Resolve detection coordinates
        image_path = Path(scan.image_path)
        renderer = self._get_renderer()
        img_w, img_h = renderer.get_image_dimensions(image_path)

        det_x = float(det.get("x", img_w / 2.0))
        det_y = float(det.get("y", img_h / 2.0))
        det_w = float(det.get("width", 40.0))
        det_h = float(det.get("height", 40.0))
        center_x = det_x + det_w / 2.0
        center_y = det_y + det_h / 2.0

        lat = det.get("latitude", det.get("lat"))
        lon = det.get("longitude", det.get("lon", det.get("lng")))
        if lat is None or lon is None:
            calc_lat, calc_lon = pixel_to_geo(center_x, center_y, scan.bbox, img_w, img_h)
            lat = round(calc_lat, 6)
            lon = round(calc_lon, 6)
        else:
            lat = round(float(lat), 6)
            lon = round(float(lon), 6)

        target_coords = {
            "latitude": lat,
            "longitude": lon,
            "x": round(det_x, 1),
            "y": round(det_y, 1),
            "width": round(det_w, 1),
            "height": round(det_h, 1),
            "center_x": round(center_x, 1),
            "center_y": round(center_y, 1),
            "heading": det.get("heading") or det.get("wake_heading"),
            "length_m": det.get("length_m") or det.get("estimated_length_m"),
            "mmsi": det.get("mmsi") or det.get("matched_mmsi"),
            "is_dark": det.get("is_dark", True),
        }

        # 1. Build SAR Chip
        sar_chip = self._generate_sar_chip(
            scan=scan,
            det=det,
            det_x=det_x,
            det_y=det_y,
            det_w=det_w,
            det_h=det_h,
            center_x=center_x,
            center_y=center_y,
            padding_px=padding_px,
            out_size=chip_size,
        )

        # 2. Build Optical Chip
        optical_chip = self._generate_optical_chip(
            scan=scan,
            det=det,
            det_idx=target_idx,
            lat=lat,
            lon=lon,
            out_size=chip_size,
        )

        # 3. Build Previous Pass Chip
        prev_chip = self._generate_previous_pass_chip(
            current_scan=scan,
            lat=lat,
            lon=lon,
            padding_px=padding_px,
            out_size=chip_size,
        )

        # 4. Build AIS Tactical Context Chip
        ais_chip = self._generate_ais_context_chip(
            scan=scan,
            det=det,
            lat=lat,
            lon=lon,
            out_size=chip_size,
        )

        chips = {
            ChipModality.SAR.value: sar_chip,
            ChipModality.OPTICAL.value: optical_chip,
            ChipModality.PREVIOUS_PASS.value: prev_chip,
            ChipModality.AIS_CONTEXT.value: ais_chip,
        }

        summary = {
            "sar_snr_db": sar_chip.metadata.get("snr_db"),
            "sar_rcs_dbsm": sar_chip.metadata.get("rcs_dbsm"),
            "optical_status": optical_chip.status,
            "optical_confirmed": optical_chip.metadata.get("optical_confirmed", False),
            "previous_pass_status": prev_chip.status,
            "previous_pass_time_delta_h": prev_chip.metadata.get("time_delta_hours"),
            "ais_context_contacts": ais_chip.metadata.get("nearby_vessels_count", 0),
            "correlated_mmsi": ais_chip.metadata.get("correlated_mmsi"),
            "tactical_assessment": self._generate_tactical_assessment(
                sar_chip=sar_chip,
                optical_chip=optical_chip,
                prev_chip=prev_chip,
                ais_chip=ais_chip,
            ),
        }

        gallery = CandidateChipGallery(
            candidate_id=f"{scan_id}_det_{target_idx}",
            scan_id=scan_id,
            detection_index=target_idx,
            target_coordinates=target_coords,
            chips=chips,
            summary=summary,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        return gallery.to_dict()

    def execute_all(
        self,
        scan_id: str,
        padding_px: int = 80,
        chip_size: int = 256,
        max_candidates: int = 25,
    ) -> list[dict[str, Any]]:
        """Generate chip galleries for all candidate detections in a scan."""
        scan = self._scan_repo.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan '{scan_id}' not found")

        detections = self._load_detections(scan)
        if not detections:
            return [self.execute(scan_id, 0, padding_px, chip_size)]

        results = []
        for idx in range(min(len(detections), max_candidates)):
            results.append(self.execute(scan_id, idx, padding_px, chip_size))
        return results

    # -------------------------------------------------------------------------
    # Modality 1: SAR Radar Chip
    # -------------------------------------------------------------------------
    def _generate_sar_chip(
        self,
        scan: Scan,
        det: dict[str, Any],
        det_x: float,
        det_y: float,
        det_w: float,
        det_h: float,
        center_x: float,
        center_y: float,
        padding_px: int,
        out_size: int,
    ) -> ChipArtifact:
        renderer = self._get_renderer()
        sat_label = (
            getattr(getattr(scan, "acquisition", None), "satellite", None)
            or getattr(scan, "sensor", None)
            or "S-1"
        )
        avail, status, uri, meta = renderer.render_sar_chip(
            image_path=scan.image_path,
            det=det,
            det_x=det_x,
            det_y=det_y,
            det_w=det_w,
            det_h=det_h,
            center_x=center_x,
            center_y=center_y,
            padding_px=padding_px,
            out_size=out_size,
            sensor_label=sat_label,
        )
        return ChipArtifact(
            modality=ChipModality.SAR,
            available=avail,
            status=status,
            image_data_uri=uri,
            width=out_size,
            height=out_size,
            metadata=meta,
        )

    # -------------------------------------------------------------------------
    # Modality 2: Optical Cross-Validation Chip
    # -------------------------------------------------------------------------
    def _generate_optical_chip(
        self,
        scan: Scan,
        det: dict[str, Any],
        det_idx: int,
        lat: float,
        lon: float,
        out_size: int,
    ) -> ChipArtifact:
        scan_dir = Path(scan.image_path).parent

        # 1. Check for existing local optical image files
        optical_path = None
        for candidate_name in ("optical_image.png", "sentinel2.png", "optical.png"):
            p = scan_dir / candidate_name
            if p.is_file():
                optical_path = p
                break

        # 2. Check detection optical metadata (from cross_validate_optical)
        opt_status = det.get("optical_status")
        cloud_cover = det.get("optical_cloud_cover")
        time_delta_h = det.get("optical_time_delta_hours")
        scene_id = det.get("optical_scene_id")
        opt_confirmed = bool(det.get("optical_confirmed", False))

        # If not present in detection, query Sentinel2Client if available
        if opt_status is None and self._sentinel2_client is not None:
            try:
                scenes = self._sentinel2_client.search_concurrent_optical(
                    bbox=scan.bbox,
                    target_datetime=scan.acquisition.acquired_at,
                    time_window_hours=48.0,
                    limit=5,
                )
                if scenes:
                    best = scenes[0]
                    scene_id = best.scene_id
                    cloud_cover = best.cloud_cover
                    time_delta_h = best.time_delta_hours
                    val_res = self._sentinel2_client.validate_detection_optical(
                        detection_idx=det_idx,
                        det=det,
                        scene=best,
                    )
                    opt_status = val_res.status
                    opt_confirmed = val_res.optical_confirmed
            except Exception as exc:
                logger.debug("Optical query error: %s", exc)

        if opt_status is None:
            opt_status = "NO_CONCURRENT_OPTICAL"

        renderer = self._get_renderer()
        avail, status, uri, meta = renderer.render_optical_chip(
            optical_path=optical_path,
            lat=lat,
            lon=lon,
            scan_bbox=scan.bbox,
            det=det,
            out_size=out_size,
            opt_status=opt_status,
            opt_confirmed=opt_confirmed,
            cloud_cover=cloud_cover,
            time_delta_h=time_delta_h,
            scene_id=scene_id,
        )

        return ChipArtifact(
            modality=ChipModality.OPTICAL,
            available=avail,
            status=status,
            image_data_uri=uri,
            width=out_size,
            height=out_size,
            metadata=meta,
        )

    # -------------------------------------------------------------------------
    # Modality 3: Previous Pass Change Detection Chip
    # -------------------------------------------------------------------------
    def _generate_previous_pass_chip(
        self,
        current_scan: Scan,
        lat: float,
        lon: float,
        padding_px: int,
        out_size: int,
    ) -> ChipArtifact:
        scans = list(self._scan_repo.list())
        current_dt = current_scan.acquisition.acquired_at
        if current_dt.utcoffset() is None:
            current_dt = current_dt.replace(tzinfo=timezone.utc)

        # Search for prior scans containing the candidate coordinate
        prior_candidates: list[tuple[float, Scan]] = []
        for s in scans:
            if s.folder_name == current_scan.folder_name:
                continue
            s_dt = s.acquisition.acquired_at
            if s_dt.utcoffset() is None:
                s_dt = s_dt.replace(tzinfo=timezone.utc)

            # Prior pass only (s_dt < current_dt)
            if s_dt >= current_dt:
                continue

            # Check bbox spatial containment
            b = s.bbox
            if b.min_latitude <= lat <= b.max_latitude and b.min_longitude <= lon <= b.max_longitude:
                delta_h = (current_dt - s_dt).total_seconds() / 3600.0
                prior_candidates.append((delta_h, s))

        # Sort by shortest time delta (most recent prior pass)
        prior_candidates.sort(key=lambda x: x[0])

        renderer = self._get_renderer()
        if not prior_candidates:
            avail, status, uri, meta = renderer.render_previous_pass_chip(
                prior_image_path=None,
                prior_bbox=None,
                prior_scan_id=None,
                lat=lat,
                lon=lon,
                padding_px=padding_px,
                out_size=out_size,
                time_delta_h=None,
            )
            return ChipArtifact(
                modality=ChipModality.PREVIOUS_PASS,
                available=avail,
                status=status,
                image_data_uri=uri,
                width=out_size,
                height=out_size,
                metadata=meta,
            )

        best_delta_h, prior_scan = prior_candidates[0]
        prior_img_path = Path(prior_scan.image_path)

        # Check if prior scan also had a detection at this location
        prior_dets = self._load_detections(prior_scan)
        vessel_present_before = False
        for pd in prior_dets:
            p_lat = pd.get("latitude", pd.get("lat"))
            p_lon = pd.get("longitude", pd.get("lon", pd.get("lng")))
            if p_lat is not None and p_lon is not None:
                dist = _haversine_m(lat, lon, float(p_lat), float(p_lon))
                if dist < 200.0:
                    vessel_present_before = True
                    break

        avail, status, uri, meta = renderer.render_previous_pass_chip(
            prior_image_path=prior_img_path,
            prior_bbox=prior_scan.bbox,
            prior_scan_id=prior_scan.folder_name,
            lat=lat,
            lon=lon,
            padding_px=padding_px,
            out_size=out_size,
            time_delta_h=best_delta_h,
            has_prior_detection=vessel_present_before,
        )

        return ChipArtifact(
            modality=ChipModality.PREVIOUS_PASS,
            available=avail,
            status=status,
            image_data_uri=uri,
            width=out_size,
            height=out_size,
            metadata=meta,
        )

    # -------------------------------------------------------------------------
    # Modality 4: AIS Tactical Context Mini-Map Chip
    # -------------------------------------------------------------------------
    def _generate_ais_context_chip(
        self,
        scan: Scan,
        det: dict[str, Any],
        lat: float,
        lon: float,
        out_size: int,
    ) -> ChipArtifact:
        max_range_nm = 5.0
        max_range_m = max_range_nm * 1852.0

        nearby_vessels: list[dict[str, Any]] = []
        nearest_dist_m: Optional[float] = None
        nearest_mmsi: Optional[str] = None
        correlated_mmsi: Optional[str] = det.get("mmsi") or det.get("matched_mmsi")

        if self._ais_repo is not None and hasattr(self._ais_repo, "get_vessel_positions"):
            try:
                # 0.1 degree box is ~11 km
                search_bbox = BoundingBox(lon - 0.12, lat - 0.12, lon + 0.12, lat + 0.12)
                records = self._ais_repo.get_vessel_positions(
                    bbox=search_bbox,
                    latest_only=True,
                    limit=200,
                )
                for rec in records:
                    r_lat = rec.get("latitude")
                    r_lon = rec.get("longitude")
                    if r_lat is None or r_lon is None:
                        continue
                    dist = _haversine_m(lat, lon, float(r_lat), float(r_lon))
                    if dist <= max_range_m * 1.2:
                        rec_copy = dict(rec)
                        rec_copy["distance_m"] = dist
                        nearby_vessels.append(rec_copy)
                        if nearest_dist_m is None or dist < nearest_dist_m:
                            nearest_dist_m = dist
                            nearest_mmsi = str(rec.get("mmsi") or "")
            except Exception as exc:
                logger.debug("Error querying AIS for context chip: %s", exc)

        renderer = self._get_renderer()
        avail, status, uri, meta = renderer.render_ais_context_chip(
            lat=lat,
            lon=lon,
            det=det,
            nearby_vessels=nearby_vessels,
            out_size=out_size,
            max_range_nm=max_range_nm,
            nearest_dist_m=nearest_dist_m,
            nearest_mmsi=nearest_mmsi,
            correlated_mmsi=correlated_mmsi,
        )

        return ChipArtifact(
            modality=ChipModality.AIS_CONTEXT,
            available=avail,
            status=status,
            image_data_uri=uri,
            width=out_size,
            height=out_size,
            metadata=meta,
        )

    # -------------------------------------------------------------------------
    # Detection File Loading Helper
    # -------------------------------------------------------------------------
    def _load_detections(self, scan: Scan) -> list[dict[str, Any]]:
        """Load detection JSON list from standard scan output locations."""
        scan_dir = Path(scan.image_path).parent
        for fname in ("detection_results.json", "detections.json", "ships.json", "results.json"):
            p = scan_dir / fname
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        candidates = (
                            data.get("detections")
                            or data.get("candidates")
                            or data.get("ships")
                            or data.get("contacts")
                        )
                        if isinstance(candidates, list):
                            return candidates
                except Exception as exc:
                    logger.debug("Failed parsing detection file %s: %s", p, exc)
        return []

    def _generate_tactical_assessment(
        self,
        sar_chip: ChipArtifact,
        optical_chip: ChipArtifact,
        prev_chip: ChipArtifact,
        ais_chip: ChipArtifact,
    ) -> str:
        signals: list[str] = []
        is_dark = ais_chip.metadata.get("is_dark", True)
        contacts = ais_chip.metadata.get("nearby_vessels_count", 0)

        if is_dark:
            signals.append("Non-broadcasting dark target with radar RCS signature")
        else:
            mmsi = ais_chip.metadata.get("correlated_mmsi")
            signals.append(f"Correlated with AIS contact MMSI {mmsi}")

        opt_status = optical_chip.status
        if opt_status == "CONFIRMED_VESSEL":
            signals.append("Sentinel-2 optical NDWI independently corroborates vessel metal structure")
        elif opt_status == "CLOUD_OBSCURED":
            signals.append("Concurrent optical visual inspection obstructed by cloud cover")
        elif opt_status == "LAND_FALSE_ALARM":
            signals.append("Optical spectral signature suggests stationary land or reef")

        if prev_chip.available:
            change = prev_chip.metadata.get("change_type")
            delta_h = prev_chip.metadata.get("time_delta_hours", 0)
            if change == "NEW_ARRIVAL":
                signals.append(f"Temporal change: Target absent in prior pass ({delta_h:.1f}h ago) - newly arrived")
            elif change == "PERSISTENT":
                signals.append(f"Temporal change: Contact was also detected in prior pass ({delta_h:.1f}h ago)")

        if contacts > 5:
            signals.append(f"High traffic density area ({contacts} AIS contacts within 5NM)")

        return ". ".join(signals) + "."

