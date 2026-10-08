"""Unit tests for SAR speckle filters and preprocessing pipeline."""

import unittest
import numpy as np

from sentinel_analysis.infrastructure.imagery.preprocessing import (
    apply_frost_filter,
    apply_lee_filter,
    preprocess_sar,
)


def test_lee_filter_reduces_variance_in_homogeneous_regions() -> None:
    np.random.seed(42)
    base = np.full((50, 50), 100.0, dtype=np.float32)
    noise = np.random.normal(0, 15.0, (50, 50)).astype(np.float32)
    noisy_img = np.clip(base + noise, 0, 255).astype(np.uint8)

    filtered = apply_lee_filter(noisy_img, window_size=5)

    assert filtered.shape == (50, 50)
    assert filtered.dtype == np.uint8
    assert np.var(filtered) < np.var(noisy_img)


def test_frost_filter_execution() -> None:
    np.random.seed(42)
    img = np.random.randint(0, 255, (30, 30), dtype=np.uint8)
    filtered = apply_frost_filter(img, window_size=5, damping_factor=2.0)

    assert filtered.shape == (30, 30)
    assert filtered.dtype == np.uint8


def test_frost_filter_reduces_variance_on_larger_image() -> None:
    np.random.seed(42)
    base = np.full((100, 100), 120.0, dtype=np.float32)
    noise = np.random.normal(0, 20.0, (100, 100)).astype(np.float32)
    noisy_img = np.clip(base + noise, 0, 255).astype(np.uint8)

    filtered = apply_frost_filter(noisy_img, window_size=5, damping_factor=2.0)

    assert filtered.shape == (100, 100)
    assert filtered.dtype == np.uint8
    assert np.var(filtered) < np.var(noisy_img)


def test_preprocess_sar_pipeline() -> None:
    # Test 3-channel composite
    img_rgb = np.random.randint(0, 255, (40, 40, 3), dtype=np.uint8)
    res = preprocess_sar(img_rgb, filter_type="lee", window_size=5)
    assert res.shape == (40, 40, 3)

    # Test enhance mode
    res_enh = preprocess_sar(img_rgb, filter_type="enhance", window_size=5)
    assert res_enh.shape == (40, 40, 3)
    assert res_enh.dtype == np.uint8


def test_enhance_sar_imagery_contrast_and_speckle() -> None:
    from sentinel_analysis.infrastructure.imagery.preprocessing import enhance_sar_imagery

    np.random.seed(42)
    # Low-contrast image with heavy multiplicative noise
    base = np.full((64, 64), 25.0, dtype=np.float32)
    noise = np.random.gamma(shape=2.0, scale=0.5, size=(64, 64))
    raw = base * noise
    # Add a high-intensity metallic target in the center
    raw[30:35, 30:35] = 180.0

    enhanced = enhance_sar_imagery(raw, p_min=1.0, p_max=99.0, gamma=0.72)

    assert enhanced.shape == (64, 64)
    assert enhanced.dtype == np.uint8
    # Dynamic range should be stretched across the 0-255 spectrum
    assert enhanced.max() > 200
    assert enhanced.min() <= 50
    # Center target should remain noticeably brighter than background
    assert np.mean(enhanced[30:35, 30:35]) > np.mean(enhanced[:20, :20])


def test_enhance_sar_imagery_handles_constant_array() -> None:
    from sentinel_analysis.infrastructure.imagery.preprocessing import enhance_sar_imagery

    zeros = np.zeros((32, 32), dtype=np.float32)
    res_zeros = enhance_sar_imagery(zeros)
    assert res_zeros.shape == (32, 32)
    assert res_zeros.dtype == np.uint8
    assert not np.isnan(res_zeros).any()

    uniform = np.full((32, 32), 42.0, dtype=np.float32)
    res_uniform = enhance_sar_imagery(uniform)
    assert res_uniform.shape == (32, 32)
    assert res_uniform.dtype == np.uint8
    assert not np.isnan(res_uniform).any()


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()


