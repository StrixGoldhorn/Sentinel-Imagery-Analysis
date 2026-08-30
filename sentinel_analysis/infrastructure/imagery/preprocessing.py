"""SAR image preprocessing and speckle filtering algorithms."""

import cv2
import numpy as np


def lee_filter(
    image: np.ndarray,
    window_size: int = 7,
    noise_variance: float = 0.25,
) -> np.ndarray:
    """Apply Lee speckle filter to attenuate multiplicative SAR speckle noise.

    Preserves edges while smoothing homogeneous clutter areas.
    """
    if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size <= 0 or window_size % 2 == 0:
        raise ValueError("Lee-filter window size must be a positive odd integer")
    if noise_variance < 0:
        raise ValueError("Lee-filter noise variance cannot be negative")

    image_float = image.astype(np.float64)
    kernel_size = (window_size, window_size)
    local_mean = cv2.boxFilter(image_float, -1, kernel_size)
    local_mean_squared = cv2.boxFilter(image_float**2, -1, kernel_size)
    local_variance = np.maximum(local_mean_squared - local_mean**2, 0)
    noise = (local_mean**2) * noise_variance

    weight = np.divide(
        local_variance - noise,
        local_variance + noise,
        out=np.zeros_like(local_variance),
        where=(local_variance + noise) != 0,
    )
    weight = np.clip(weight, 0.0, 1.0)
    filtered = local_mean + weight * (image_float - local_mean)
    return np.clip(filtered, 0, 255).astype(image.dtype)


def frost_filter(
    image: np.ndarray,
    window_size: int = 7,
    damping_factor: float = 2.0,
) -> np.ndarray:
    """Apply Frost filter using exponentially damped spatial weighting."""
    if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size <= 0 or window_size % 2 == 0:
        raise ValueError("Frost-filter window size must be a positive odd integer")
    if damping_factor <= 0:
        raise ValueError("Frost-filter damping factor must be positive")

    image_float = image.astype(np.float64)
    height, width = image.shape[:2]
    pad = window_size // 2
    padded = np.pad(image_float, pad, mode="reflect")

    # Spatial distance matrix
    y_coords, x_coords = np.mgrid[-pad : pad + 1, -pad : pad + 1]
    distances = np.sqrt(x_coords**2 + y_coords**2)

    output = np.zeros_like(image_float)
    for i in range(height):
        for j in range(width):
            window = padded[i : i + window_size, j : j + window_size]
            mean = np.mean(window)
            if mean > 0:
                std = np.std(window)
                c = std / mean
                weights = np.exp(-damping_factor * c * distances)
                total_w = np.sum(weights)
                output[i, j] = np.sum(weights * window) / total_w if total_w > 0 else mean
            else:
                output[i, j] = 0.0

    return np.clip(output, 0, 255).astype(image.dtype)


def preprocess_sar(
    image: np.ndarray,
    filter_type: str = "lee",
    window_size: int = 7,
) -> np.ndarray:
    """Convenience pipeline to denoise SAR imagery."""
    if filter_type == "none":
        return image
    if filter_type == "lee":
        return lee_filter(image, window_size=window_size)
    if filter_type == "frost":
        return frost_filter(image, window_size=window_size)
    raise ValueError(f"Unknown filter type: {filter_type}. Choose from 'lee', 'frost', 'none'.")


apply_lee_filter = lee_filter
apply_frost_filter = frost_filter

