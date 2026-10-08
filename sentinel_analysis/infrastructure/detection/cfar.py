"""Constant False Alarm Rate (CFAR) and Dual-Polarization SAR Detection.

Provides robust, adaptive clutter thresholding (CA-CFAR, GO-CFAR, SO-CFAR) and
polarimetric channel fusion (VV + VH) to detect vessels in non-homogeneous sea clutter.
"""

from __future__ import annotations

import cv2
import numpy as np


def ca_cfar_2d(
    image: np.ndarray,
    guard_size: int = 5,
    train_size: int = 15,
    factor: float = 3.5,
    min_threshold: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """2D Cell-Averaging Constant False Alarm Rate (CA-CFAR) detector.

    Uses sliding 2D box filters to compute local clutter mean and standard deviation
    in O(1) time per pixel, dynamically adjusting threshold to sea state.

    Parameters:
        image: 2D uint8 or float grayscale SAR image.
        guard_size: Odd integer size of the guard window around the cell under test (CUT).
        train_size: Odd integer size of the outer background training window (train_size > guard_size).
        factor: Multiplier k for clutter standard deviation (T = mu + k * sigma).
        min_threshold: Absolute minimum floor for detection threshold (0-255).

    Returns:
        tuple[np.ndarray, np.ndarray]: (binary_mask, threshold_map)
    """
    g = max(1, guard_size if guard_size % 2 == 1 else guard_size + 1)
    t = max(g + 2, train_size if train_size % 2 == 1 else train_size + 1)

    img = image.astype(np.float32)

    # Box filters compute local sum across windows in O(1)
    sum_train = cv2.boxFilter(img, -1, (t, t), normalize=False, borderType=cv2.BORDER_REFLECT)
    sum_guard = cv2.boxFilter(img, -1, (g, g), normalize=False, borderType=cv2.BORDER_REFLECT)
    clutter_sum = np.maximum(0.0, sum_train - sum_guard)

    num_cells = float(t * t - g * g)
    mu = clutter_sum / max(1.0, num_cells)

    # Local variance and standard deviation
    img_sq = img * img
    sum_sq_train = cv2.boxFilter(img_sq, -1, (t, t), normalize=False, borderType=cv2.BORDER_REFLECT)
    sum_sq_guard = cv2.boxFilter(img_sq, -1, (g, g), normalize=False, borderType=cv2.BORDER_REFLECT)
    clutter_sq_sum = np.maximum(0.0, sum_sq_train - sum_sq_guard)

    variance = np.maximum(0.0, (clutter_sq_sum / max(1.0, num_cells)) - (mu * mu))
    sigma = np.sqrt(variance)

    # Dynamic threshold surface
    threshold_map = mu + factor * sigma
    threshold_map = np.maximum(threshold_map, float(min_threshold))

    binary_mask = (img > threshold_map).astype(np.uint8) * 255
    return binary_mask, threshold_map.astype(np.float32)


def go_cfar_2d(
    image: np.ndarray,
    guard_size: int = 5,
    train_size: int = 15,
    factor: float = 3.0,
    min_threshold: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """2D Greatest-Of CFAR (GO-CFAR) detector.

    Divides training cells into leading and lagging halves and uses the maximum
    clutter estimate. Ideal for sharp clutter boundaries (e.g. sea-ice edges, squalls).
    """
    g = max(1, guard_size if guard_size % 2 == 1 else guard_size + 1)
    t = max(g + 2, train_size if train_size % 2 == 1 else train_size + 1)
    half_t = (t - g) // 2

    img = image.astype(np.float32)

    # Left and right training blocks
    kernel_left = np.zeros((t, t), dtype=np.float32)
    kernel_left[:, :half_t] = 1.0
    kernel_right = np.zeros((t, t), dtype=np.float32)
    kernel_right[:, -half_t:] = 1.0

    cells_per_side = float(np.sum(kernel_left))
    mu_left = cv2.filter2D(img, -1, kernel_left / max(1.0, cells_per_side), borderType=cv2.BORDER_REFLECT)
    mu_right = cv2.filter2D(img, -1, kernel_right / max(1.0, cells_per_side), borderType=cv2.BORDER_REFLECT)

    mu_go = np.maximum(mu_left, mu_right)
    threshold_map = np.maximum(mu_go * factor, float(min_threshold))

    binary_mask = (img > threshold_map).astype(np.uint8) * 255
    return binary_mask, threshold_map.astype(np.float32)


def so_cfar_2d(
    image: np.ndarray,
    guard_size: int = 5,
    train_size: int = 15,
    factor: float = 3.0,
    min_threshold: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """2D Smallest-Of CFAR (SO-CFAR) detector.

    Divides training cells into leading and lagging halves and uses the minimum
    clutter estimate. Prevents target masking in dense multi-target maritime areas.
    """
    g = max(1, guard_size if guard_size % 2 == 1 else guard_size + 1)
    t = max(g + 2, train_size if train_size % 2 == 1 else train_size + 1)
    half_t = (t - g) // 2

    img = image.astype(np.float32)

    kernel_left = np.zeros((t, t), dtype=np.float32)
    kernel_left[:, :half_t] = 1.0
    kernel_right = np.zeros((t, t), dtype=np.float32)
    kernel_right[:, -half_t:] = 1.0

    cells_per_side = float(np.sum(kernel_left))
    mu_left = cv2.filter2D(img, -1, kernel_left / max(1.0, cells_per_side), borderType=cv2.BORDER_REFLECT)
    mu_right = cv2.filter2D(img, -1, kernel_right / max(1.0, cells_per_side), borderType=cv2.BORDER_REFLECT)

    mu_so = np.minimum(mu_left, mu_right)
    threshold_map = np.maximum(mu_so * factor, float(min_threshold))

    binary_mask = (img > threshold_map).astype(np.uint8) * 255
    return binary_mask, threshold_map.astype(np.float32)


def fuse_dual_polarization(
    vv: np.ndarray,
    vh: np.ndarray,
    mode: str = "ratio",
    epsilon: float = 1.0,
) -> np.ndarray:
    """Fuse co-polarized (VV) and cross-polarized (VH) SAR channels.

    Cross-polarization (VH) features significantly lower sea-clutter backscatter than
    co-polarization (VV), whereas vessels exhibit strong depolarized double-bounce returns.
    Fusing them suppresses waves and sharpens ship hulls.

    Parameters:
        vv: Grayscale 2D array of VV backscatter intensity.
        vh: Grayscale 2D array of VH backscatter intensity.
        mode: Fusion strategy: 'ratio', 'difference', 'product', or 'enhanced'.
        epsilon: Small constant to prevent division by zero.

    Returns:
        np.ndarray: uint8 fused SAR intensity image.
    """
    if vv.shape[:2] != vh.shape[:2]:
        vh = cv2.resize(vh, (vv.shape[1], vv.shape[0]), interpolation=cv2.INTER_LINEAR)

    vv_f = vv.astype(np.float32)
    vh_f = vh.astype(np.float32)

    if mode == "ratio":
        # Cross-polarization ratio (VH / VV) with baseline sea clutter subtraction
        ratio = (vh_f + epsilon) / (vv_f + epsilon)
        baseline = float(np.median(ratio))
        ratio_eff = np.maximum(0.0, ratio - baseline)
        p99 = np.percentile(ratio_eff, 99.5)
        scale = 255.0 / max(1e-4, float(p99))
        fused = np.clip(ratio_eff * scale, 0, 255).astype(np.uint8)
    elif mode == "difference":
        # Differential co-cross metric: VH - 0.2 * VV with baseline subtraction
        diff = vh_f - 0.2 * vv_f
        baseline = float(np.median(diff))
        diff_eff = np.maximum(0.0, diff - baseline)
        p99 = np.percentile(diff_eff, 99.5)
        scale = 255.0 / max(1e-4, float(p99))
        fused = np.clip(diff_eff * scale, 0, 255).astype(np.uint8)
    elif mode == "product":
        # Geometric mean: sqrt(VV * VH) with baseline subtraction
        prod = np.sqrt(np.clip(vv_f * vh_f, 0, None))
        baseline = float(np.median(prod))
        prod_eff = np.maximum(0.0, prod - baseline)
        p99 = np.percentile(prod_eff, 99.5)
        scale = 255.0 / max(1e-4, float(p99))
        fused = np.clip(prod_eff * scale, 0, 255).astype(np.uint8)
    elif mode == "enhanced":
        # VH weighted by polarization ratio with baseline subtraction
        ratio = (vh_f + epsilon) / (vv_f + epsilon)
        enh = vh_f * (1.0 + np.clip(ratio, 0.0, 5.0))
        baseline = float(np.median(enh))
        enh_eff = np.maximum(0.0, enh - baseline)
        p99 = np.percentile(enh_eff, 99.5)
        scale = 255.0 / max(1e-4, float(p99))
        fused = np.clip(enh_eff * scale, 0, 255).astype(np.uint8)
    else:
        # Default fallback to VH
        fused = vh.astype(np.uint8)

    return fused
