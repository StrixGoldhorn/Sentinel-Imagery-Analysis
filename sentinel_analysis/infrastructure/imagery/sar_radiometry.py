"""Concrete SAR radiometry processor using OpenCV and NumPy."""

from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from sentinel_analysis.domain.sar_radiometry import (
    DespeckleFilter,
    HistogramData,
    RadiometryAdjustment,
)


class OpenCVSarRadiometryProcessor:
    """Computes SAR image histograms and applies radiometric / despeckling transformations."""

    def compute_histogram(
        self,
        image_path: Path | str,
        polarization: Optional[str] = None,
        num_bins: int = 256,
    ) -> HistogramData:
        """Compute pixel intensity histogram and distribution statistics for a SAR image."""
        p = Path(image_path)
        if not p.is_file():
            raise FileNotFoundError(f"Image not found at {p}")

        raw = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise ValueError(f"Unable to read image at {p}")

        gray = self._extract_channel(raw, polarization)

        hist = cv2.calcHist([gray], [0], None, [num_bins], [0, 256]).flatten()
        total_pixels = int(gray.size)
        counts = [int(c) for c in hist]
        bins = list(range(num_bins))

        # Compute cumulative distribution function (CDF)
        cdf = (np.cumsum(hist) / float(total_pixels)).tolist()
        cdf = [round(float(v), 5) for v in cdf]

        min_val = float(np.min(gray))
        max_val = float(np.max(gray))
        mean_val = float(np.mean(gray))
        std_val = float(np.std(gray))

        percentiles_list = [1, 5, 25, 50, 75, 95, 99]
        pct_vals = np.percentile(gray, percentiles_list)
        pct_dict = {f"p{p}": float(val) for p, val in zip(percentiles_list, pct_vals)}

        return HistogramData(
            bins=bins,
            counts=counts,
            cdf=cdf,
            min_val=min_val,
            max_val=max_val,
            mean=mean_val,
            std_dev=std_val,
            percentiles=pct_dict,
            polarization=polarization,
            total_pixels=total_pixels,
        )

    def enhance_radiometry(
        self,
        image_path: Path | str,
        adjustment: RadiometryAdjustment,
    ) -> bytes:
        """Apply contrast stretching, log scaling, gamma, and despeckle filtering."""
        p = Path(image_path)
        if not p.is_file():
            raise FileNotFoundError(f"Image not found at {p}")

        raw = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise ValueError(f"Unable to read image at {p}")

        gray = self._extract_channel(raw, adjustment.polarization).astype(np.float32)

        # 1. Despeckle filtering
        filtered = self._apply_despeckle(gray, adjustment.despeckle_filter, adjustment.filter_kernel_size)

        # 2. Contrast stretching (linear min-max normalization)
        c_min = float(adjustment.contrast_min)
        c_max = float(adjustment.contrast_max)
        denom = max(1e-4, c_max - c_min)
        stretched = np.clip((filtered - c_min) / denom, 0.0, 1.0)

        # 3. Logarithmic dynamic range stretch
        if adjustment.log_stretch:
            # Expands lower radar returns while compressing specular metallic peaks
            stretched = np.log1p(9.0 * stretched) / np.log(10.0)
            stretched = np.clip(stretched, 0.0, 1.0)

        # 4. Gamma correction
        if abs(adjustment.gamma - 1.0) > 1e-3:
            # gamma < 1.0 brightens shadows/sea clutter; gamma > 1.0 increases contrast & darkens clutter
            gamma_exp = float(adjustment.gamma)
            stretched = np.power(stretched, gamma_exp)
            stretched = np.clip(stretched, 0.0, 1.0)

        # 5. Scale to 8-bit uint8 and encode to PNG
        out_uint8 = (stretched * 255.0).astype(np.uint8)
        success, encoded = cv2.imencode(".png", out_uint8)
        if not success:
            raise RuntimeError("Failed encoding enhanced radiometry image to PNG")

        return encoded.tobytes()

    def _extract_channel(self, img: np.ndarray, polarization: Optional[str]) -> np.ndarray:
        if img.ndim == 2:
            return img
        if img.ndim == 3:
            # Check polarization request
            if polarization:
                norm_pol = polarization.upper().strip()
                if norm_pol in ("VH", "HV") and img.shape[2] >= 2:
                    return img[:, :, 1]
                elif norm_pol in ("VV", "HH") and img.shape[2] >= 1:
                    return img[:, :, 0]
            # Default: BGR to Grayscale
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img

    def _apply_despeckle(self, img: np.ndarray, filter_name: str, kernel_size: int) -> np.ndarray:
        f_name = (filter_name or "none").lower().strip()
        k = kernel_size if kernel_size % 2 == 1 else kernel_size + 1
        k = max(3, min(15, k))

        if f_name == DespeckleFilter.MEDIAN.value:
            # Non-linear rank filter for salt-and-pepper radar spikes
            return cv2.medianBlur(img.astype(np.uint8), k).astype(np.float32)

        elif f_name == DespeckleFilter.BOXCAR.value:
            # Mean averaging filter
            return cv2.blur(img, (k, k))

        elif f_name == DespeckleFilter.LEE.value:
            # Standard adaptive SAR Lee filter
            # Local mean: E[X]
            local_mean = cv2.boxFilter(img, -1, (k, k))
            # Local variance: Var(X) = E[X^2] - (E[X])^2
            img_sq = cv2.boxFilter(img * img, -1, (k, k))
            local_var = np.maximum(0.0, img_sq - (local_mean * local_mean))

            # Noise variance parameter for SAR amplitude (multiplicative Rayleigh/Gamma)
            # Typically for 4-look SAR GRD, noise_var_ratio ~ 0.25
            noise_var_ratio = 0.25
            var_noise = (local_mean * local_mean) * noise_var_ratio

            # Weight factor W = Var(X) / (Var(X) + Var(Noise))
            denom = local_var + var_noise + 1e-4
            weight = np.clip(local_var / denom, 0.0, 1.0)

            # Filtered pixel = Mean + W * (Original - Mean)
            lee_filtered = local_mean + weight * (img - local_mean)
            return np.clip(lee_filtered, 0.0, 255.0)

        elif f_name == DespeckleFilter.FROST.value:
            # Adaptive exponentially damped Frost filter
            local_mean = cv2.boxFilter(img, -1, (k, k))
            img_sq = cv2.boxFilter(img * img, -1, (k, k))
            local_var = np.maximum(0.0, img_sq - (local_mean * local_mean))
            local_std = np.sqrt(local_var)

            # Coefficient of variation: Ci = sigma / mean
            c_i = local_std / (local_mean + 1e-4)

            # Damping factor: high damping on homogeneous ocean (high smoothing), low damping on vessels
            # Weighting between original and local mean based on exponential variation
            damping = np.exp(-np.clip(c_i * 2.0, 0.0, 5.0))
            frost_filtered = damping * local_mean + (1.0 - damping) * img
            return np.clip(frost_filtered, 0.0, 255.0)

        return img
