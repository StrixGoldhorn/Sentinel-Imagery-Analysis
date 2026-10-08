"""Numpy and Scipy implementation of SAR log-ratio change detection and coherence analysis."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage


class NumpySARChangeDetector:
    """Multi-temporal SAR change detector using log-ratio amplitude differencing.

    Detects:
    1. Arrived targets (positive log-ratio backscatter surge on T2 pass).
    2. Departed targets (negative log-ratio backscatter drop on T2 pass).
    3. Persistent fixed structures (oil platforms, lighthouses, wind turbines with consistent high backscatter).
    """

    def compute_change_map(
        self,
        reference_image_path: Path | str,
        target_image_path: Path | str,
        output_path: Path | str,
        *,
        threshold_db: float = 4.5,
    ) -> dict[str, Any]:
        """Compute log-ratio difference map and identify change targets."""
        ref_p = Path(reference_image_path)
        tgt_p = Path(target_image_path)
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if not ref_p.is_file():
            raise FileNotFoundError(f"Reference image not found: {ref_p}")
        if not tgt_p.is_file():
            raise FileNotFoundError(f"Target image not found: {tgt_p}")

        # 1. Load images into grayscale numpy arrays
        with Image.open(tgt_p) as img_tgt:
            tgt_gray = img_tgt.convert("L")
            tgt_w, tgt_h = tgt_gray.size
            tgt_arr = np.asarray(tgt_gray, dtype=np.float32)

        with Image.open(ref_p) as img_ref:
            if img_ref.size != (tgt_w, tgt_h):
                ref_resized = img_ref.convert("L").resize((tgt_w, tgt_h), resample=Image.Resampling.BILINEAR)
                ref_arr = np.asarray(ref_resized, dtype=np.float32)
            else:
                ref_arr = np.asarray(img_ref.convert("L"), dtype=np.float32)

        # 2. Compute log-ratio difference in decibels (dB)
        # Ratio = 10 * log10((I_target + eps) / (I_ref + eps))
        eps = 1.0
        log_tgt = 10.0 * np.log10(np.maximum(tgt_arr + eps, eps))
        log_ref = 10.0 * np.log10(np.maximum(ref_arr + eps, eps))
        diff_db = log_tgt - log_ref

        # 3. Formulate binary masks for change categories
        # Arrived: Significant backscatter increase and significant target brightness
        arrived_mask = (diff_db >= threshold_db) & (tgt_arr >= 35.0)

        # Departed: Significant backscatter decrease and significant reference brightness
        departed_mask = (diff_db <= -threshold_db) & (ref_arr >= 35.0)

        # Persistent structure: High backscatter in both passes with stable ratio (|diff| <= 2.5 dB)
        persistent_mask = (
            (tgt_arr >= 55.0)
            & (ref_arr >= 55.0)
            & (np.abs(diff_db) <= 2.5)
        )

        change_points: list[dict[str, Any]] = []

        def _extract_centroids(
            mask: np.ndarray,
            change_type: str,
            default_narrative_prefix: str,
        ) -> list[dict[str, Any]]:
            labeled, num_features = ndimage.label(mask)
            if num_features == 0:
                return []
            
            slices = ndimage.find_objects(labeled)
            extracted: list[dict[str, Any]] = []

            for idx, sl in enumerate(slices):
                if sl is None:
                    continue
                comp_mask = labeled[sl] == (idx + 1)
                pixel_count = int(np.sum(comp_mask))
                if pixel_count < 1:
                    continue

                # Center of mass in full array coordinates
                y_indices, x_indices = np.nonzero(comp_mask)
                cy = float(sl[0].start + np.mean(y_indices))
                cx = float(sl[1].start + np.mean(x_indices))

                # Compute local peak or mean magnitude
                comp_diff = diff_db[sl][comp_mask]
                mean_mag = float(np.mean(comp_diff))
                peak_mag = float(np.max(comp_diff) if mean_mag >= 0 else np.min(comp_diff))

                # Confidence heuristic based on magnitude and cluster size
                confidence = min(0.99, max(0.50, round(0.5 + abs(peak_mag) / 20.0 + min(pixel_count, 10) * 0.02, 2)))

                extracted.append({
                    "x": round(cx, 1),
                    "y": round(cy, 1),
                    "change_type": change_type,
                    "magnitude_db": round(peak_mag, 2),
                    "pixel_count": pixel_count,
                    "confidence": confidence,
                    "narrative": f"{default_narrative_prefix} at pixel ({cx:.0f}, {cy:.0f}) with {peak_mag:+.1f} dB SAR amplitude shift.",
                })

            return extracted

        arrived_points = _extract_centroids(arrived_mask, "ARRIVED", "Arrived vessel detected")
        departed_points = _extract_centroids(departed_mask, "DEPARTED", "Departed vessel detected")
        persistent_points = _extract_centroids(persistent_mask, "PERSISTENT_STRUCTURE", "Persistent offshore structure identified")

        change_points.extend(arrived_points)
        change_points.extend(departed_points)
        change_points.extend(persistent_points)

        # 4. Generate visual 3-channel composite change map
        # Base layer: Grayscale target image
        composite = np.zeros((tgt_h, tgt_w, 3), dtype=np.uint8)
        base_gray = np.clip(tgt_arr, 0, 255).astype(np.uint8)
        composite[:, :, 0] = base_gray
        composite[:, :, 1] = base_gray
        composite[:, :, 2] = base_gray

        # Color highlight overlays:
        # Green = Arrived
        composite[arrived_mask] = [0, 255, 60]
        # Red = Departed
        composite[departed_mask] = [255, 40, 40]
        # Cyan = Persistent
        composite[persistent_mask] = [0, 230, 255]

        out_img = Image.fromarray(composite)
        out_img.save(out_p)

        return {
            "status": "success",
            "reference_image": str(ref_p),
            "target_image": str(tgt_p),
            "output_path": str(out_p),
            "threshold_db": threshold_db,
            "dimensions": {"width": tgt_w, "height": tgt_h},
            "mean_difference_db": round(float(np.mean(diff_db)), 2),
            "arrived_count": len(arrived_points),
            "departed_count": len(departed_points),
            "persistent_structures_count": len(persistent_points),
            "total_change_points": len(change_points),
            "change_points": change_points,
        }
