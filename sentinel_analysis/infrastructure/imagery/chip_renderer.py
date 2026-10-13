"""Infrastructure implementation of ChipRenderer using OpenCV and NumPy."""

from __future__ import annotations

import base64
import logging
import math
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from sentinel_analysis.application.ports.imagery import ChipRenderer
from sentinel_analysis.domain.coordinates import geo_to_pixel
from sentinel_analysis.domain.entities import BoundingBox

logger = logging.getLogger(__name__)


def _encode_png_base64(img: np.ndarray) -> str:
    """Encode an OpenCV BGR/grayscale image to a base64 data URI."""
    success, buffer = cv2.imencode(".png", img)
    if not success:
        return ""
    b64 = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/png;base64,{b64}"


class OpenCVChipRenderer(ChipRenderer):
    """Render tactical chips using OpenCV image processing and rendering."""

    def get_image_dimensions(self, image_path: Path | str) -> tuple[int, int]:
        """Return (width, height) of an image on disk, defaulting to (1000, 1000)."""
        p = Path(image_path)
        if p.is_file():
            try:
                img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
                if img is not None:
                    h, w = img.shape[:2]
                    return w, h
            except Exception as exc:
                logger.debug("Failed to read image dimensions from %s: %s", p, exc)
        return 1000, 1000

    def render_sar_chip(
        self,
        image_path: Path | str,
        det: dict[str, Any],
        det_x: float,
        det_y: float,
        det_w: float,
        det_h: float,
        center_x: float,
        center_y: float,
        padding_px: int,
        out_size: int,
        sensor_label: str = "S-1",
    ) -> tuple[bool, str, str, dict[str, Any]]:
        """Render high-resolution SAR chip with bounding box, wake vector, and intensity stats.

        Returns: (available, status, image_data_uri, metadata)
        """
        p = Path(image_path)
        img = None
        if p.is_file():
            img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)

        if img is None:
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
            return False, "IMAGE_NOT_FOUND", _encode_png_base64(canvas), {}

        img_h, img_w = img.shape[:2]
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
        cv2.rectangle(chip_resized, (0, 0), (out_size, 20), (15, 20, 25), -1)
        cv2.putText(
            chip_resized,
            f"SAR {sensor_label} | SNR: {snr_db}dB",
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

        return True, "SUCCESS", uri, metadata

    def render_optical_chip(
        self,
        optical_path: Optional[Path],
        lat: float,
        lon: float,
        scan_bbox: BoundingBox,
        det: dict[str, Any],
        out_size: int,
        opt_status: str,
        opt_confirmed: bool,
        cloud_cover: Optional[float] = None,
        time_delta_h: Optional[float] = None,
        scene_id: Optional[str] = None,
    ) -> tuple[bool, str, str, dict[str, Any]]:
        """Render optical cross-validation chip or tactical synthetic card.

        Returns: (available, status, image_data_uri, metadata)
        """
        if optical_path is not None and optical_path.is_file():
            try:
                opt_img = cv2.imread(str(optical_path))
                if opt_img is not None:
                    h_opt, w_opt = opt_img.shape[:2]
                    px, py = geo_to_pixel(lat, lon, scan_bbox, w_opt, h_opt)
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
                        return True, "CONFIRMED_VESSEL", _encode_png_base64(opt_crop), {
                            "source": "local_archive",
                            "optical_confirmed": True,
                        }
            except Exception as exc:
                logger.debug("Failed reading optical crop from %s: %s", optical_path, exc)

        # Tactical information card
        chip = np.zeros((out_size, out_size, 3), dtype=np.uint8)
        for r in range(out_size):
            frac = r / float(out_size)
            b = int(25 + 10 * frac)
            g = int(35 + 12 * frac)
            r_c = int(20 + 8 * frac)
            chip[r, :] = (b, g, r_c)

        grid_step = out_size // 4
        for step in range(grid_step, out_size, grid_step):
            cv2.line(chip, (step, 0), (step, out_size), (40, 50, 35), 1)
            cv2.line(chip, (0, step), (out_size, step), (40, 50, 35), 1)

        cc = out_size // 2
        cv2.circle(chip, (cc, cc), 18, (0, 200, 255), 1, cv2.LINE_AA)
        cv2.line(chip, (cc - 26, cc), (cc + 26, cc), (0, 200, 255), 1, cv2.LINE_AA)
        cv2.line(chip, (cc, cc - 26), (cc, cc + 26), (0, 200, 255), 1, cv2.LINE_AA)

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

        status_color = (0, 255, 120) if opt_confirmed else (0, 165, 255)
        if opt_status == "CLOUD_OBSCURED":
            status_color = (200, 200, 200)
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
                (120, 140, 150),
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
        return available, opt_status, _encode_png_base64(chip), metadata

    def render_previous_pass_chip(
        self,
        prior_image_path: Optional[Path],
        prior_bbox: Optional[BoundingBox],
        prior_scan_id: Optional[str],
        lat: float,
        lon: float,
        padding_px: int,
        out_size: int,
        time_delta_h: Optional[float],
        has_prior_detection: bool = False,
    ) -> tuple[bool, str, str, dict[str, Any]]:
        """Render previous pass change detection chip.

        Returns: (available, status, image_data_uri, metadata)
        """
        if prior_image_path is None or prior_bbox is None or time_delta_h is None:
            card = np.zeros((out_size, out_size, 3), dtype=np.uint8)
            card[:] = (20, 16, 12)
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
            return False, "NO_PRIOR_PASS", _encode_png_base64(card), {
                "details": "No archived prior SAR pass found covering coordinate"
            }

        if not prior_image_path.is_file():
            return False, "IMAGE_FILE_MISSING", "", {
                "prior_scan": prior_scan_id,
                "time_delta_hours": time_delta_h,
            }

        prior_img = cv2.imread(str(prior_image_path), cv2.IMREAD_UNCHANGED)
        if prior_img is None:
            return False, "LOAD_FAILED", "", {"prior_scan": prior_scan_id}

        p_h, p_w = prior_img.shape[:2]
        px, py = geo_to_pixel(lat, lon, prior_bbox, p_w, p_h)
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

        rel_cx = px_i - px1
        rel_cy = py_i - py1
        ret_color = (0, 255, 255) if has_prior_detection else (0, 200, 255)
        cv2.circle(prior_bgr, (rel_cx, rel_cy), 10, ret_color, 1, cv2.LINE_AA)
        cv2.line(prior_bgr, (rel_cx - 16, rel_cy), (rel_cx + 16, rel_cy), ret_color, 1, cv2.LINE_AA)
        cv2.line(prior_bgr, (rel_cx, rel_cy - 16), (rel_cx, rel_cy + 16), ret_color, 1, cv2.LINE_AA)

        resized = cv2.resize(prior_bgr, (out_size, out_size), interpolation=cv2.INTER_LINEAR)

        cv2.rectangle(resized, (0, 0), (out_size, 20), (15, 20, 25), -1)
        delta_str = f"-{time_delta_h:.1f}h" if time_delta_h >= 1.0 else f"-{int(time_delta_h*60)}m"
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

        status_label = "PERSISTENT CONTACT" if has_prior_detection else "EMPTY WATER (NEW ARRIVAL)"
        label_color = (0, 255, 120) if has_prior_detection else (0, 165, 255)
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

        metadata = {
            "prior_scan_id": prior_scan_id,
            "time_delta_hours": round(time_delta_h, 2),
            "vessel_present_before": has_prior_detection,
            "change_type": "PERSISTENT" if has_prior_detection else "NEW_ARRIVAL",
        }
        return True, "PREVIOUS_PASS_MATCH", _encode_png_base64(resized), metadata

    def render_ais_context_chip(
        self,
        lat: float,
        lon: float,
        det: dict[str, Any],
        nearby_vessels: Sequence[dict[str, Any]],
        out_size: int,
        max_range_nm: float = 5.0,
        nearest_dist_m: Optional[float] = None,
        nearest_mmsi: Optional[str] = None,
        correlated_mmsi: Optional[str] = None,
    ) -> tuple[bool, str, str, dict[str, Any]]:
        """Render AIS tactical context mini-map chip.

        Returns: (available, status, image_data_uri, metadata)
        """
        map_img = np.zeros((out_size, out_size, 3), dtype=np.uint8)
        map_img[:] = (26, 18, 12)  # BGR

        cx = out_size // 2
        cy = out_size // 2
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

        cv2.line(map_img, (cx, 22), (cx, out_size - 20), (45, 40, 25), 1)
        cv2.line(map_img, (10, cy), (out_size - 10, cy), (45, 40, 25), 1)

        cv2.putText(map_img, "N", (out_size - 18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 200), 1)
        cv2.arrowedLine(
            map_img,
            (out_size - 14, 45),
            (out_size - 14, 32),
            (0, 255, 200),
            1,
            tipLength=0.4,
        )

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

        is_dark = bool(det.get("is_dark", True))
        tgt_color = (0, 60, 255) if is_dark else (0, 255, 255)

        sz = 4
        cv2.rectangle(map_img, (cx - sz, cy - sz), (cx + sz, cy + sz), tgt_color, 1)
        cv2.circle(map_img, (cx, cy), 1, tgt_color, -1)

        tgt_hdg = det.get("heading") or det.get("wake_heading")
        if tgt_hdg is not None:
            try:
                rad = math.radians(float(tgt_hdg) - 90.0)
                ex = int(round(cx + 18 * math.cos(rad)))
                ey = int(round(cy + 18 * math.sin(rad)))
                cv2.arrowedLine(map_img, (cx, cy), (ex, ey), tgt_color, 1, tipLength=0.3)
            except Exception:
                pass

        cv2.rectangle(map_img, (0, 0), (out_size, 20), (12, 16, 22), -1)
        cv2.putText(
            map_img,
            "AIS TACTICAL SITUATION",
            (6, 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (0, 200, 255),
            1,
            cv2.LINE_AA,
        )

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
        return True, "SUCCESS", _encode_png_base64(map_img), metadata
