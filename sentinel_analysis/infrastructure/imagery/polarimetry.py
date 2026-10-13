"""Concrete SAR polarimetric preset processor using OpenCV and NumPy."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from sentinel_analysis.domain.polarimetry import (
    PolarimetricPresetCatalog,
    PolarimetricPresetType,
)


class OpenCVPolarimetricProcessor:
    """Renders calibrated SAR polarimetric presets and multi-temporal change composites."""

    def render_preset(
        self,
        image_path: Path | str,
        preset_type: str,
        prior_image_path: Optional[Path | str] = None,
        channel_paths: Optional[dict[str, Path | str]] = None,
        gamma: float = 1.0,
    ) -> bytes:
        """Render a polarimetric preset and return encoded PNG bytes."""
        norm_type = (preset_type or "").lower().strip()
        if not PolarimetricPresetCatalog.is_valid(norm_type):
            valid_keys = ", ".join(p.value for p in PolarimetricPresetType)
            raise ValueError(f"Invalid polarimetric preset '{preset_type}'. Valid options: {valid_keys}")

        p = Path(image_path)
        if not p.is_file():
            raise FileNotFoundError(f"SAR image not found at {p}")

        # Extract or resolve VV and VH channel arrays
        vv_arr, vh_arr = self._resolve_polarization_channels(p, channel_paths)

        if norm_type == PolarimetricPresetType.VV.value:
            rendered = self._render_vv(vv_arr, gamma)
        elif norm_type == PolarimetricPresetType.VH.value:
            rendered = self._render_vh(vh_arr, gamma)
        elif norm_type == PolarimetricPresetType.VV_VH_RATIO.value:
            rendered = self._render_vv_vh_ratio(vv_arr, vh_arr, gamma)
        elif norm_type == PolarimetricPresetType.FALSE_COLOR_COMPOSITE.value:
            rendered = self._render_false_color_composite(vv_arr, vh_arr, gamma)
        elif norm_type == PolarimetricPresetType.CHANGE_DETECTION.value:
            rendered = self._render_change_detection(vv_arr, prior_image_path, gamma)
        else:
            raise ValueError(f"Unhandled preset: {norm_type}")

        success, encoded = cv2.imencode(".png", rendered)
        if not success:
            raise RuntimeError(f"Failed encoding polarimetric preset '{norm_type}' to PNG")

        return encoded.tobytes()

    def _resolve_polarization_channels(
        self,
        primary_path: Path,
        channel_paths: Optional[dict[str, Path | str]],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Load or derive VV and VH channels from disk or multi-channel image."""
        raw = cv2.imread(str(primary_path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise ValueError(f"Unable to read image file at {primary_path}")

        # 1. Check if external channel paths provided
        if channel_paths:
            vv_p = channel_paths.get("vv") or channel_paths.get("VV")
            vh_p = channel_paths.get("vh") or channel_paths.get("VH")
            if vv_p and Path(vv_p).is_file() and vh_p and Path(vh_p).is_file():
                vv_img = cv2.imread(str(vv_p), cv2.IMREAD_GRAYSCALE)
                vh_img = cv2.imread(str(vh_p), cv2.IMREAD_GRAYSCALE)
                if vv_img is not None and vh_img is not None:
                    return vv_img.astype(np.float32), vh_img.astype(np.float32)

        # 2. Check if parent directory contains dedicated vv.png and vh.png
        parent_dir = primary_path.parent
        possible_vv = parent_dir / "vv.png"
        possible_vh = parent_dir / "vh.png"
        if possible_vv.is_file() and possible_vh.is_file():
            vv_img = cv2.imread(str(possible_vv), cv2.IMREAD_GRAYSCALE)
            vh_img = cv2.imread(str(possible_vh), cv2.IMREAD_GRAYSCALE)
            if vv_img is not None and vh_img is not None:
                return vv_img.astype(np.float32), vh_img.astype(np.float32)

        # 3. If multi-channel image (e.g. BGR or multi-band)
        if raw.ndim == 3 and raw.shape[2] >= 2:
            # OpenCV BGR convention: channel 0 is Blue (VV), channel 1 is Green (VH)
            vv = raw[:, :, 0].astype(np.float32)
            vh = raw[:, :, 1].astype(np.float32)
            return vv, vh

        # 4. Single-channel grayscale: VV is direct backscatter, synthesize VH cross-pol response
        gray = raw if raw.ndim == 2 else cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        vv = gray.astype(np.float32)

        # Cross-polarization physics approximation:
        # Sea surface single-bounce is heavily attenuated in VH; high-frequency multi-bounce
        # superstructures and specular reflectors remain strong.
        # Morphological top-hat isolates localized reflective structures:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        top_hat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel).astype(np.float32)
        # Combine attenuated diffuse background with emphasized superstructure peaks
        vh = 0.25 * vv + 1.5 * top_hat
        vh = np.clip(vh, 0.0, 255.0)

        return vv, vh

    def _normalize_percentile(
        self,
        arr: np.ndarray,
        p_low: float = 1.0,
        p_high: float = 99.5,
    ) -> np.ndarray:
        """Robust percentile contrast stretching to [0.0, 1.0]."""
        low_val, high_val = np.percentile(arr, [p_low, p_high])
        denom = max(1e-4, high_val - low_val)
        stretched = np.clip((arr - low_val) / denom, 0.0, 1.0)
        return stretched

    def _render_vv(self, vv: np.ndarray, gamma: float) -> np.ndarray:
        """Render single-bounce VV co-polarization."""
        norm = self._normalize_percentile(vv, p_low=1.0, p_high=99.0)
        if abs(gamma - 1.0) > 1e-3:
            norm = np.power(norm, max(0.1, min(5.0, float(gamma))))
        return (norm * 255.0).astype(np.uint8)

    def _render_vh(self, vh: np.ndarray, gamma: float) -> np.ndarray:
        """Render depolarized VH cross-polarization."""
        norm = self._normalize_percentile(vh, p_low=1.0, p_high=99.2)
        if abs(gamma - 1.0) > 1e-3:
            norm = np.power(norm, max(0.1, min(5.0, float(gamma))))
        return (norm * 255.0).astype(np.uint8)

    def _render_vv_vh_ratio(self, vv: np.ndarray, vh: np.ndarray, gamma: float) -> np.ndarray:
        """Render VV/VH polarization ratio in dB and apply pseudo-color palette."""
        eps = 1.0
        # Ratio in dB: 10 * (log10(VV + eps) - log10(VH + eps))
        ratio_db = 10.0 * (np.log10(np.maximum(vv + eps, eps)) - np.log10(np.maximum(vh + eps, eps)))

        # Sea surface has high ratio (strong VV, weak VH); ships have low or negative ratio (strong depolarization)
        norm = self._normalize_percentile(ratio_db, p_low=2.0, p_high=98.0)
        if abs(gamma - 1.0) > 1e-3:
            norm = np.power(norm, max(0.1, min(5.0, float(gamma))))

        u8 = (norm * 255.0).astype(np.uint8)
        # Apply high-contrast Viridis colormap for tactical ratio analysis
        colored = cv2.applyColorMap(u8, cv2.COLORMAP_VIRIDIS)
        return colored

    def _render_false_color_composite(
        self,
        vv: np.ndarray,
        vh: np.ndarray,
        gamma: float,
    ) -> np.ndarray:
        """Render standard 3-channel polarimetric RGB composite."""
        norm_vv = self._normalize_percentile(vv, p_low=1.0, p_high=99.0)
        norm_vh = self._normalize_percentile(vh, p_low=1.0, p_high=99.5)

        # Depolarization difference / ratio for blue channel
        eps = 1.0
        diff = np.abs(vv - vh)
        norm_diff = self._normalize_percentile(diff, p_low=1.0, p_high=98.5)

        if abs(gamma - 1.0) > 1e-3:
            g = max(0.1, min(5.0, float(gamma)))
            norm_vv = np.power(norm_vv, g)
            norm_vh = np.power(norm_vh, g)
            norm_diff = np.power(norm_diff, g)

        # OpenCV expects BGR order:
        # Blue = Depolarization difference
        # Green = VH amplitude (Volume / Superstructure)
        # Red = VV amplitude (Surface roughness)
        b = (norm_diff * 255.0).astype(np.uint8)
        g = (norm_vh * 255.0).astype(np.uint8)
        r = (norm_vv * 255.0).astype(np.uint8)

        bgr = cv2.merge([b, g, r])
        return bgr

    def _render_change_detection(
        self,
        current_vv: np.ndarray,
        prior_image_path: Optional[Path | str],
        gamma: float,
    ) -> np.ndarray:
        """Render multi-temporal change detection composite or spatial anomaly if no prior."""
        h, w = current_vv.shape[:2]
        base_norm = self._normalize_percentile(current_vv, p_low=1.0, p_high=99.0)
        if abs(gamma - 1.0) > 1e-3:
            base_norm = np.power(base_norm, max(0.1, min(5.0, float(gamma))))
        
        # Base dimmed grayscale background (40% brightness for contrast)
        base_u8 = (base_norm * 110.0).astype(np.uint8)
        bgr = cv2.merge([base_u8, base_u8, base_u8])

        if prior_image_path and Path(prior_image_path).is_file():
            prior_raw = cv2.imread(str(prior_image_path), cv2.IMREAD_GRAYSCALE)
            if prior_raw is not None:
                if prior_raw.shape != (h, w):
                    prior_raw = cv2.resize(prior_raw, (w, h), interpolation=cv2.INTER_LINEAR)
                prior_vv = prior_raw.astype(np.float32)

                # Compute dB difference: 10 * log10(T2 / T1)
                eps = 1.0
                diff_db = 10.0 * (np.log10(np.maximum(current_vv + eps, eps)) - np.log10(np.maximum(prior_vv + eps, eps)))

                # Arrived: T2 > T1 by >= 3.5 dB and current is sufficiently bright
                arrived_mask = (diff_db >= 3.5) & (current_vv >= 30.0)
                # Departed: T1 > T2 by >= 3.5 dB and prior was sufficiently bright
                departed_mask = (diff_db <= -3.5) & (prior_vv >= 30.0)
                # Persistent offshore structure: Both passes bright with minimal change
                persistent_mask = (current_vv >= 60.0) & (prior_vv >= 60.0) & (np.abs(diff_db) <= 2.0)

                # Overlays:
                # Arrived = Bright Green [BGR: 0, 255, 60]
                bgr[arrived_mask] = [0, 255, 60]
                # Departed = Bright Red [BGR: 40, 40, 255]
                bgr[departed_mask] = [40, 40, 255]
                # Persistent = Bright Cyan [BGR: 255, 230, 0]
                bgr[persistent_mask] = [255, 230, 0]
                return bgr

        # Fallback: Spatial anomaly filter (Difference-of-Gaussians)
        blur_small = cv2.GaussianBlur(current_vv, (3, 3), 1.0)
        blur_large = cv2.GaussianBlur(current_vv, (21, 21), 5.0)
        dog = np.maximum(0.0, blur_small - blur_large)
        dog_mask = dog > np.percentile(dog, 98.0)
        # Highlight anomalous specular returns in Yellow [BGR: 0, 230, 255]
        bgr[dog_mask] = [0, 230, 255]
        return bgr
