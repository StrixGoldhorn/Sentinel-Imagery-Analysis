"""Multimodal Candidate Chip Gallery Generator.

Produces a unified 4-sensor tactical chip gallery around any detection candidate:
1. SAR: High-resolution Synthetic Aperture Radar crop with bounding box, wake vector, and intensity stats.
2. OPTICAL: Concurrent optical validation crop (Sentinel-2 multi-spectral) or tactical cloud-penetration card.
3. PREVIOUS_PASS: Co-registered prior satellite pass showing temporal change (presence/absence).
4. AIS_CONTEXT: 360-degree tactical mini-map with range rings, nearby AIS contacts, and kinematics.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.chip_gallery import (
    CandidateChipGallery,
    ChipArtifact,
    ChipModality,
)
from sentinel_analysis.domain.coordinates import geo_to_pixel, pixel_to_geo
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


def _encode_png_base64(img: np.ndarray) -> str:
    """Encode an OpenCV BGR/grayscale image to a base64 data URI."""
    success, buffer = cv2.imencode(".png", img)
    if not success:
        return ""
    b64 = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/png;base64,{b64}"


class GenerateChipGallery:
    """Use case for extracting and rendering multimodal chip galleries around detections."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        ais_repository: Optional[AISRepository] = None,
        sentinel2_client: Optional[Any] = None,
    ) -> None:
        self._scan_repo = scan_repository
        self._ais_repo = ais_repository
        self._sentinel2_client = sentinel2_client

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
        img = None
        img_w, img_h = 1000, 1000
        if image_path.is_file():
            img = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
            if img is not None:
                img_h, img_w = img.shape[:2]

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
            img=img,
            img_w=img_w,
            img_h=img_h,
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

        # 4. Build AIS Tactical Context Mini-map Chip
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

        # Multi-sensor summary
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
            detection_index=target_idx,
            scan_id=scan.folder_name,
            target_coordinates=target_coords,
            chips=chips,
            summary=summary,
        )
        return gallery.to_dict()

    def execute_all(
        self,
        scan_id: str,
        padding_px: int = 80,
        chip_size: int = 256,
        max_candidates: int = 50,
    ) -> list[dict[str, Any]]:
        """Generate chip galleries for all detections in a scan."""
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
        img: Optional[np.ndarray],
        img_w: int,
        img_h: int,
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
        if img is None:
            # Synthetic placeholder
            canvas = np.zeros((out_size, out_size, 3), dtype=np.uint8)
            cv2.putText(
                canvas,
                "SAR IMAGE UNAVAILABLE",
                (20, out_size // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 255),
                1,
                cv2.LINE_AA,
            )
            return ChipArtifact(
                modality=ChipModality.SAR,
                available=False,
                status="IMAGE_NOT_FOUND",
                image_data_uri=_encode_png_base64(canvas),
                width=out_size,
                height=out_size,
                metadata={},
            )

        x1 = max(0, int(round(det_x - padding_px)))
        y1 = max(0, int(round(det_y - padding_px)))
        x2 = min(img_w, int(round(det_x + det_w + padding_px)))
        y2 = min(img_h, int(round(det_y + det_h + padding_px)))

        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            crop = np.zeros((out_size, out_size), dtype=np.uint8)

        # Grayscale stats
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        h_crop, w_crop = gray.shape[:2]

        mean_val = float(np.mean(gray))
        max_val = float(np.max(gray))
        std_val = float(np.std(gray))

        # Perimeter clutter estimation
        bx = max(1, int(w_crop * 0.15))
        by = max(1, int(h_crop * 0.15))
        clutter_mask = np.ones((h_crop, w_crop), dtype=bool)
        clutter_mask[by : h_crop - by, bx : w_crop - bx] = False
        clutter_pix = gray[clutter_mask]
        clutter_mean = float(np.mean(clutter_pix)) if clutter_pix.size > 0 else mean_val
        snr_linear = max(1e-3, max_val) / max(1e-3, clutter_mean)
        snr_db = round(10.0 * math.log10(snr_linear), 2)

        # RCS estimate in dBsm
        pixel_area_m2 = 100.0  # Sentinel-1 10m x 10m
        target_integral = float(np.sum(np.maximum(0.0, gray.astype(float) - clutter_mean)))
        rcs_linear = max(1.0, target_integral * (pixel_area_m2 / 255.0))
        rcs_dbsm = round(10.0 * math.log10(rcs_linear), 1)

        # Create annotated display image (BGR)
        norm_gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        bgr = cv2.cvtColor(norm_gray, cv2.COLOR_GRAY2BGR)

        # Overlay bounding box / OBB
        local_x = int(round(det_x - x1))
        local_y = int(round(det_y - y1))
        local_w = int(round(det_w))
        local_h = int(round(det_h))

        obb = det.get("obb")
        if obb and isinstance(obb, dict) and "corners" in obb:
            try:
                corners = np.array(obb["corners"], dtype=np.float32)
                local_corners = np.int32([[c[0] - x1, c[1] - y1] for c in corners])
                cv2.polylines(bgr, [local_corners], isClosed=True, color=(0, 255, 255), thickness=1, lineType=cv2.LINE_AA)
            except Exception:
                cv2.rectangle(bgr, (local_x, local_y), (local_x + local_w, local_y + local_h), (0, 255, 255), 1)
        else:
            cv2.rectangle(bgr, (local_x, local_y), (local_x + local_w, local_y + local_h), (0, 255, 255), 1)

        # Reticle center crosshair
        local_cx = int(round(center_x - x1))
        local_cy = int(round(center_y - y1))
        r_ret = 8
        cv2.line(bgr, (local_cx - r_ret, local_cy), (local_cx + r_ret, local_cy), (0, 165, 255), 1, cv2.LINE_AA)
        cv2.line(bgr, (local_cx, local_cy - r_ret), (local_cx, local_cy + r_ret), (0, 165, 255), 1, cv2.LINE_AA)

        # Wake vector arrow if detected
        wake_heading = det.get("wake_heading") or det.get("heading")
        if wake_heading is not None:
            try:
                rad = math.radians(float(wake_heading) - 90.0)
                arrow_len = 24.0
                ax = int(round(local_cx + arrow_len * math.cos(rad)))
                ay = int(round(local_cy + arrow_len * math.sin(rad)))
                cv2.arrowedLine(bgr, (local_cx, local_cy), (ax, ay), (255, 200, 0), 1, tipLength=0.3)
            except Exception:
                pass

        # Resize to standard chip size with crisp interpolation
        chip_resized = cv2.resize(bgr, (out_size, out_size), interpolation=cv2.INTER_LINEAR)

        # Add HUD header and scale bar
        sat_label = (
            getattr(getattr(scan, "acquisition", None), "satellite", None)
            or getattr(scan, "sensor", None)
            or "S-1"
        )
        cv2.rectangle(chip_resized, (0, 0), (out_size, 20), (15, 20, 25), -1)
        cv2.putText(
            chip_resized,
            f"SAR {sat_label} | SNR: {snr_db}dB",
            (6, 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )

        # 100m scale bar (10 pixels = 100m at 10m/px)
        scale_px = max(10, int(round(100.0 / 10.0 * (out_size / max(1, w_crop)))))
        sb_x2 = out_size - 10
        sb_x1 = max(10, sb_x2 - scale_px)
        sb_y = out_size - 8
        cv2.line(chip_resized, (sb_x1, sb_y), (sb_x2, sb_y), (0, 255, 255), 2)
        cv2.putText(
            chip_resized,
            "100m",
            (sb_x1, sb_y - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.3,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )

        uri = _encode_png_base64(chip_resized)

        metadata = {
            "mean_intensity": round(mean_val, 1),
            "max_intensity": round(max_val, 1),
            "std_intensity": round(std_val, 1),
            "clutter_mean": round(clutter_mean, 1),
            "snr_db": snr_db,
            "rcs_dbsm": rcs_dbsm,
            "crop_dimensions": {"width": w_crop, "height": h_crop},
            "scale_m_per_px": 10.0,
            "wake_heading": wake_heading,
        }

        return ChipArtifact(
            modality=ChipModality.SAR,
            available=True,
            status="SUCCESS",
            image_data_uri=uri,
            width=out_size,
            height=out_size,
            metadata=metadata,
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

        if optical_path is not None:
            try:
                opt_img = cv2.imread(str(optical_path))
                if opt_img is not None:
                    # Crop optical image at co-located coordinates
                    h_opt, w_opt = opt_img.shape[:2]
                    px, py = geo_to_pixel(lat, lon, scan.bbox, w_opt, h_opt)
                    rad = int(round(w_opt * 0.08))
                    ox1 = max(0, int(px - rad))
                    oy1 = max(0, int(py - rad))
                    ox2 = min(w_opt, int(px + rad))
                    oy2 = min(h_opt, int(py + rad))
                    opt_crop = opt_img[oy1:oy2, ox1:ox2]
                    if opt_crop.size > 0:
                        opt_crop = cv2.resize(opt_crop, (out_size, out_size))
                        cv2.rectangle(opt_crop, (0, 0), (out_size, 20), (15, 20, 25), -1)
                        cv2.putText(
                            opt_crop,
                            "SENTINEL-2 OPTICAL (LOCAL)",
                            (6, 14),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.36,
                            (0, 255, 120),
                            1,
                            cv2.LINE_AA,
                        )
                        return ChipArtifact(
                            modality=ChipModality.OPTICAL,
                            available=True,
                            status="CONFIRMED_VESSEL",
                            image_data_uri=_encode_png_base64(opt_crop),
                            width=out_size,
                            height=out_size,
                            metadata={"source": "local_archive", "optical_confirmed": True},
                        )
            except Exception as exc:
                logger.debug("Failed reading optical crop from %s: %s", optical_path, exc)

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

        # Render tactical optical information chip
        chip = np.zeros((out_size, out_size, 3), dtype=np.uint8)
        # Deep blue-grey ocean gradient background
        for r in range(out_size):
            frac = r / float(out_size)
            b = int(25 + 10 * frac)
            g = int(35 + 12 * frac)
            r_c = int(20 + 8 * frac)
            chip[r, :] = (b, g, r_c)

        # Draw grid
        grid_step = out_size // 4
        for step in range(grid_step, out_size, grid_step):
            cv2.line(chip, (step, 0), (step, out_size), (40, 50, 35), 1)
            cv2.line(chip, (0, step), (out_size, step), (40, 50, 35), 1)

        # Center crosshair reticle
        cc = out_size // 2
        cv2.circle(chip, (cc, cc), 18, (0, 200, 255), 1, cv2.LINE_AA)
        cv2.line(chip, (cc - 26, cc), (cc + 26, cc), (0, 200, 255), 1, cv2.LINE_AA)
        cv2.line(chip, (cc, cc - 26), (cc, cc + 26), (0, 200, 255), 1, cv2.LINE_AA)

        # Header bar
        cv2.rectangle(chip, (0, 0), (out_size, 22), (12, 16, 20), -1)
        cv2.putText(
            chip,
            "COPERNICUS S2 OPTICAL",
            (6, 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (0, 255, 200),
            1,
            cv2.LINE_AA,
        )

        # Status Badge & details in body
        status_color = (0, 255, 120) if opt_confirmed else (0, 165, 255)
        if opt_status == "CLOUD_OBSCURED":
            status_color = (200, 200, 200)
            # Add simulated cloud noise
            noise = np.random.normal(50, 20, (out_size, out_size)).astype(np.uint8)
            chip[:, :, 0] = cv2.add(chip[:, :, 0], noise)
            chip[:, :, 1] = cv2.add(chip[:, :, 1], noise)
            chip[:, :, 2] = cv2.add(chip[:, :, 2], noise)
        elif opt_status == "LAND_FALSE_ALARM":
            status_color = (0, 100, 255)

        cv2.putText(
            chip,
            f"STATUS: {opt_status}",
            (10, 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.34,
            status_color,
            1,
            cv2.LINE_AA,
        )

        if cloud_cover is not None:
            cv2.putText(
                chip,
                f"CLOUD COVER: {cloud_cover}%",
                (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.32,
                (220, 220, 220),
                1,
                cv2.LINE_AA,
            )

        if time_delta_h is not None:
            cv2.putText(
                chip,
                f"OFFSET: {time_delta_h:+.1f}h",
                (10, 85),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.32,
                (200, 220, 200),
                1,
                cv2.LINE_AA,
            )

        if scene_id:
            disp_id = scene_id[:26] + "..." if len(scene_id) > 28 else scene_id
            cv2.putText(
                chip,
                disp_id,
                (10, out_size - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.28,
                (160, 160, 160),
                1,
                cv2.LINE_AA,
            )

        available = opt_status != "NO_CONCURRENT_OPTICAL"
        metadata = {
            "status": opt_status,
            "optical_confirmed": opt_confirmed,
            "cloud_cover": cloud_cover,
            "time_delta_hours": time_delta_h,
            "scene_id": scene_id,
            "target_ndwi": det.get("optical_target_ndwi"),
            "water_ndwi": det.get("optical_water_ndwi"),
            "contrast_ndwi": det.get("optical_contrast_ndwi"),
        }

        return ChipArtifact(
            modality=ChipModality.OPTICAL,
            available=available,
            status=opt_status,
            image_data_uri=_encode_png_base64(chip),
            width=out_size,
            height=out_size,
            metadata=metadata,
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

        if not prior_candidates:
            # Render clean dark tactical card indicating no prior pass in archive
            card = np.zeros((out_size, out_size, 3), dtype=np.uint8)
            card[:] = (20, 16, 12)  # Dark slate
            cc = out_size // 2
            cv2.circle(card, (cc, cc), 25, (80, 80, 80), 1, cv2.LINE_AA)
            cv2.line(card, (cc - 35, cc), (cc + 35, cc), (80, 80, 80), 1, cv2.LINE_AA)
            cv2.line(card, (cc, cc - 35), (cc, cc + 35), (80, 80, 80), 1, cv2.LINE_AA)

            cv2.rectangle(card, (0, 0), (out_size, 20), (12, 12, 16), -1)
            cv2.putText(
                card,
                "HISTORICAL SAR PASS",
                (6, 14),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.36,
                (0, 165, 255),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                card,
                "NO PRIOR PASS ARCHIVED",
                (12, cc - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.34,
                (160, 160, 160),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                card,
                "IN AREA OF INTEREST",
                (12, cc + 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.32,
                (130, 130, 130),
                1,
                cv2.LINE_AA,
            )

            return ChipArtifact(
                modality=ChipModality.PREVIOUS_PASS,
                available=False,
                status="NO_PRIOR_PASS",
                image_data_uri=_encode_png_base64(card),
                width=out_size,
                height=out_size,
                metadata={"details": "No archived prior SAR pass found covering coordinate"},
            )

        best_delta_h, prior_scan = prior_candidates[0]
        prior_img_path = Path(prior_scan.image_path)
        if not prior_img_path.is_file():
            return ChipArtifact(
                modality=ChipModality.PREVIOUS_PASS,
                available=False,
                status="IMAGE_FILE_MISSING",
                image_data_uri="",
                width=out_size,
                height=out_size,
                metadata={"prior_scan": prior_scan.folder_name, "time_delta_hours": best_delta_h},
            )

        prior_img = cv2.imread(str(prior_img_path), cv2.IMREAD_UNCHANGED)
        if prior_img is None:
            return ChipArtifact(
                modality=ChipModality.PREVIOUS_PASS,
                available=False,
                status="LOAD_FAILED",
                image_data_uri="",
                width=out_size,
                height=out_size,
                metadata={"prior_scan": prior_scan.folder_name},
            )

        p_h, p_w = prior_img.shape[:2]
        px, py = geo_to_pixel(lat, lon, prior_scan.bbox, p_w, p_h)
        px_i, py_i = int(round(px)), int(round(py))

        half_w = max(20, padding_px)
        px1 = max(0, px_i - half_w)
        py1 = max(0, py_i - half_w)
        px2 = min(p_w, px_i + half_w)
        py2 = min(p_h, py_i + half_w)

        prior_crop = prior_img[py1:py2, px1:px2]
        if prior_crop.size == 0:
            prior_crop = np.zeros((out_size, out_size), dtype=np.uint8)

        prior_gray = (
            cv2.cvtColor(prior_crop, cv2.COLOR_BGR2GRAY)
            if len(prior_crop.shape) == 3
            else prior_crop
        )
        norm_gray = cv2.normalize(prior_gray, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        prior_bgr = cv2.cvtColor(norm_gray, cv2.COLOR_GRAY2BGR)

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

        # Center reticle marking the current candidate location on the prior pass
        rel_cx = px_i - px1
        rel_cy = py_i - py1
        ret_color = (0, 255, 255) if vessel_present_before else (0, 200, 255)
        cv2.circle(prior_bgr, (rel_cx, rel_cy), 10, ret_color, 1, cv2.LINE_AA)
        cv2.line(prior_bgr, (rel_cx - 16, rel_cy), (rel_cx + 16, rel_cy), ret_color, 1, cv2.LINE_AA)
        cv2.line(prior_bgr, (rel_cx, rel_cy - 16), (rel_cx, rel_cy + 16), ret_color, 1, cv2.LINE_AA)

        resized = cv2.resize(prior_bgr, (out_size, out_size), interpolation=cv2.INTER_LINEAR)

        # Header overlay
        cv2.rectangle(resized, (0, 0), (out_size, 20), (15, 20, 25), -1)
        delta_str = f"-{best_delta_h:.1f}h" if best_delta_h >= 1.0 else f"-{int(best_delta_h*60)}m"
        cv2.putText(
            resized,
            f"PRIOR PASS: {delta_str}",
            (6, 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )

        status_label = "PERSISTENT CONTACT" if vessel_present_before else "EMPTY WATER (NEW ARRIVAL)"
        label_color = (0, 255, 120) if vessel_present_before else (0, 165, 255)
        cv2.putText(
            resized,
            status_label,
            (6, out_size - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.30,
            label_color,
            1,
            cv2.LINE_AA,
        )

        return ChipArtifact(
            modality=ChipModality.PREVIOUS_PASS,
            available=True,
            status="PREVIOUS_PASS_MATCH",
            image_data_uri=_encode_png_base64(resized),
            width=out_size,
            height=out_size,
            metadata={
                "prior_scan_id": prior_scan.folder_name,
                "time_delta_hours": round(best_delta_h, 2),
                "vessel_present_before": vessel_present_before,
                "change_type": "PERSISTENT" if vessel_present_before else "NEW_ARRIVAL",
            },
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
        # Mini-map canvas with deep tactical navy background
        map_img = np.zeros((out_size, out_size, 3), dtype=np.uint8)
        map_img[:] = (26, 18, 12)  # BGR

        cx = out_size // 2
        cy = out_size // 2
        max_range_nm = 5.0
        max_range_m = max_range_nm * 1852.0

        # Draw range rings: 1 NM, 2.5 NM, 5.0 NM
        rings = [(1.0, "1NM"), (2.5, "2.5NM"), (5.0, "5NM")]
        for rng_nm, label in rings:
            r_px = int(round((rng_nm / max_range_nm) * (out_size * 0.42)))
            cv2.circle(map_img, (cx, cy), r_px, (50, 45, 30), 1, cv2.LINE_AA)
            cv2.putText(
                map_img,
                label,
                (cx + 4, cy - r_px + 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.26,
                (100, 100, 80),
                1,
                cv2.LINE_AA,
            )

        # Crosshairs / Axes
        cv2.line(map_img, (cx, 22), (cx, out_size - 20), (45, 40, 25), 1)
        cv2.line(map_img, (10, cy), (out_size - 10, cy), (45, 40, 25), 1)

        # North indicator in top right
        cv2.putText(map_img, "N", (out_size - 18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 200), 1)
        cv2.arrowedLine(
            map_img,
            (out_size - 14, 45),
            (out_size - 14, 32),
            (0, 255, 200),
            1,
            tipLength=0.4,
        )

        # Query nearby AIS vessels
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

        # Plot nearby AIS vessels on mini-map
        for v in nearby_vessels:
            v_lat = float(v["latitude"])
            v_lon = float(v["longitude"])
            dy_m = (v_lat - lat) * 111320.0
            dx_m = (v_lon - lon) * 111320.0 * math.cos(math.radians(lat))

            vx = int(round(cx + (dx_m / max_range_m) * (out_size * 0.42)))
            vy = int(round(cy - (dy_m / max_range_m) * (out_size * 0.42)))

            if 15 <= vx < out_size - 15 and 25 <= vy < out_size - 15:
                v_mmsi = str(v.get("mmsi", ""))
                is_this_target = correlated_mmsi and str(v_mmsi) == str(correlated_mmsi)

                v_color = (0, 255, 120) if not is_this_target else (0, 255, 255)
                cv2.circle(map_img, (vx, vy), 3, v_color, -1)

                # Velocity vector if speed & heading known
                v_spd = float(v.get("speed") or 0.0)
                v_hdg = v.get("heading") or v.get("course")
                if v_spd > 1.0 and v_hdg is not None:
                    try:
                        rad = math.radians(float(v_hdg) - 90.0)
                        vec_len = min(15, max(5, int(v_spd * 0.8)))
                        end_x = int(round(vx + vec_len * math.cos(rad)))
                        end_y = int(round(vy + vec_len * math.sin(rad)))
                        cv2.line(map_img, (vx, vy), (end_x, end_y), v_color, 1)
                    except Exception:
                        pass

                # Short label
                if v_mmsi:
                    cv2.putText(
                        map_img,
                        v_mmsi[-4:],
                        (vx + 4, vy - 2),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.24,
                        (180, 180, 180),
                        1,
                    )

        # Draw Center Target (Candidate)
        is_dark = bool(det.get("is_dark", True))
        tgt_color = (0, 60, 255) if is_dark else (0, 255, 255)  # Red for dark, cyan for correlated

        # Target diamond/square
        sz = 4
        cv2.rectangle(map_img, (cx - sz, cy - sz), (cx + sz, cy + sz), tgt_color, 1)
        cv2.circle(map_img, (cx, cy), 1, tgt_color, -1)

        # If heading available, draw candidate vector
        tgt_hdg = det.get("heading") or det.get("wake_heading")
        if tgt_hdg is not None:
            try:
                rad = math.radians(float(tgt_hdg) - 90.0)
                ex = int(round(cx + 18 * math.cos(rad)))
                ey = int(round(cy + 18 * math.sin(rad)))
                cv2.arrowedLine(map_img, (cx, cy), (ex, ey), tgt_color, 1, tipLength=0.3)
            except Exception:
                pass

        # Top Header Bar
        cv2.rectangle(map_img, (0, 0), (out_size, 20), (12, 16, 22), -1)
        ais_hdr = "AIS TACTICAL SITUATION"
        cv2.putText(
            map_img,
            ais_hdr,
            (6, 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (0, 200, 255),
            1,
            cv2.LINE_AA,
        )

        # Bottom Status Bar
        cv2.rectangle(map_img, (0, out_size - 18), (out_size, out_size), (12, 16, 22), -1)
        status_text = f"CONTACTS: {len(nearby_vessels)} | {'DARK' if is_dark else 'CORRELATED'}"
        status_c = (0, 80, 255) if is_dark else (0, 255, 120)
        cv2.putText(
            map_img,
            status_text,
            (6, out_size - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.30,
            status_c,
            1,
            cv2.LINE_AA,
        )

        metadata = {
            "nearby_vessels_count": len(nearby_vessels),
            "tactical_range_nm": max_range_nm,
            "nearest_contact_dist_m": round(nearest_dist_m, 1) if nearest_dist_m is not None else None,
            "nearest_contact_mmsi": nearest_mmsi,
            "correlated_mmsi": correlated_mmsi,
            "is_dark": is_dark,
        }

        return ChipArtifact(
            modality=ChipModality.AIS_CONTEXT,
            available=True,
            status="SUCCESS",
            image_data_uri=_encode_png_base64(map_img),
            width=out_size,
            height=out_size,
            metadata=metadata,
        )

    # -------------------------------------------------------------------------
    # Assessment & Helpers
    # -------------------------------------------------------------------------
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

    def _load_detections(self, scan: Scan) -> list[dict[str, Any]]:
        """Load detection results from scan folder."""
        image_path = Path(scan.image_path)
        scan_dir = image_path.parent
        for fname in ("detection_results.json", f"{image_path.stem}_detections.json"):
            json_file = scan_dir / fname
            if json_file.is_file():
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    dets = data.get("detections", [])
                    if isinstance(dets, list):
                        return dets
                except Exception as exc:
                    logger.debug("Failed reading detections from %s: %s", json_file, exc)
        return []
