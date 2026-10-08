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

    ksize = (window_size, window_size)
    mean_full = cv2.boxFilter(padded, cv2.CV_64F, ksize, borderType=cv2.BORDER_REFLECT)
    mean_sq_full = cv2.boxFilter(padded**2, cv2.CV_64F, ksize, borderType=cv2.BORDER_REFLECT)

    local_mean = mean_full[pad : pad + height, pad : pad + width]
    local_mean_sq = mean_sq_full[pad : pad + height, pad : pad + width]
    local_var = np.maximum(local_mean_sq - local_mean**2, 0.0)
    local_std = np.sqrt(local_var)

    c = np.divide(local_std, local_mean, out=np.zeros_like(local_mean), where=local_mean > 0)

    numerator = np.zeros_like(image_float)
    denominator = np.zeros_like(image_float)

    for dy in range(-pad, pad + 1):
        for dx in range(-pad, pad + 1):
            dist = np.sqrt(dy**2 + dx**2)
            w = np.exp(-damping_factor * c * dist)
            shifted = padded[pad + dy : pad + dy + height, pad + dx : pad + dx + width]
            numerator += w * shifted
            denominator += w

    out = np.divide(numerator, denominator, out=local_mean.copy(), where=denominator > 0)
    out = np.where(local_mean > 0, out, 0.0)
    return np.clip(out, 0, 255).astype(image.dtype)


def enhance_sar_imagery(
    image: np.ndarray,
    p_min: float = 1.0,
    p_max: float = 99.5,
    gamma: float = 0.72,
    window_size: int = 5,
    noise_variance: float = 0.25,
    sharpen_amount: float = 0.35,
) -> np.ndarray:
    """Enhance raw or synthesized SAR imagery for high-contrast visual analysis and CFAR.

    Applies:
    1. Lee adaptive speckle filter to smooth multiplicative radar noise while preserving sharp boundaries.
    2. Dynamic range percentile stretch (p_min to p_max) to remove zero-fill and sensor saturation.
    3. Non-linear gamma contrast compression to expand dark ocean wave modulation without clipping bright metallic targets.
    4. Unsharp masking to accentuate vessel hull outlines, superstructure reflections, and wakes.

    Args:
        image: 2D or 3D numpy array representing SAR intensity or amplitude.
        p_min: Lower percentile threshold for contrast stretching (default 1.0%).
        p_max: Upper percentile threshold for contrast stretching (default 99.5%).
        gamma: Gamma power exponent for midtone expansion (default 0.72).
        window_size: Window size for Lee filter (positive odd integer, default 5).
        noise_variance: Noise variance parameter for Lee filter (default 0.25).
        sharpen_amount: Blend factor for unsharp masking (default 0.35; 0 to disable).

    Returns:
        np.ndarray: Enhanced image array of type uint8.
    """
    if image.ndim == 3:
        channels = [
            enhance_sar_imagery(
                image[:, :, c],
                p_min=p_min,
                p_max=p_max,
                gamma=gamma,
                window_size=window_size,
                noise_variance=noise_variance,
                sharpen_amount=sharpen_amount,
            )
            for c in range(image.shape[2])
        ]
        return np.stack(channels, axis=-1)

    img_float = np.nan_to_num(image.astype(np.float32), nan=0.0, posinf=255.0, neginf=0.0)

    # 1. Lee despeckle filter
    filtered = lee_filter(img_float, window_size=window_size, noise_variance=noise_variance).astype(np.float32)

    # 2. Dynamic range percentile stretch
    valid_pixels = filtered[filtered > 0]
    if valid_pixels.size > 0:
        p_low, p_high = np.percentile(valid_pixels, (p_min, p_max))
        p_high = max(float(p_high), float(p_low) + 1.0)
    else:
        p_low, p_high = 0.0, 255.0

    stretched = np.clip((filtered - p_low) / (p_high - p_low), 0.0, 1.0)

    # 3. Non-linear gamma tone mapping
    gamma_val = max(0.01, float(gamma))
    gamma_mapped = np.power(stretched, gamma_val) * 255.0

    # 4. Subtle unsharp mask to crisp up vessel edges
    if sharpen_amount > 0:
        blurred = cv2.GaussianBlur(gamma_mapped, (0, 0), sigmaX=1.5)
        crisp = np.clip(
            cv2.addWeighted(gamma_mapped, 1.0 + sharpen_amount, blurred, -sharpen_amount, 0),
            0,
            255,
        )
        return crisp.astype(np.uint8)

    return np.clip(gamma_mapped, 0, 255).astype(np.uint8)


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
    if filter_type == "enhance":
        return enhance_sar_imagery(image, window_size=window_size)
    raise ValueError(f"Unknown filter type: {filter_type}. Choose from 'lee', 'frost', 'enhance', 'none'.")


apply_lee_filter = lee_filter
apply_frost_filter = frost_filter


