"""Unit tests for SAR speckle filters and preprocessing pipeline."""

import unittest
import numpy as np

from sentinel_analysis.infrastructure.imagery.preprocessing import (
    apply_frost_filter,
    apply_lee_filter,
    preprocess_sar,
)


class PreprocessingTests(unittest.TestCase):
    def test_lee_filter_reduces_variance_in_homogeneous_regions(self) -> None:
        np.random.seed(42)
        base = np.full((50, 50), 100.0, dtype=np.float32)
        noise = np.random.normal(0, 15.0, (50, 50)).astype(np.float32)
        noisy_img = np.clip(base + noise, 0, 255).astype(np.uint8)

        filtered = apply_lee_filter(noisy_img, window_size=5)

        self.assertEqual(filtered.shape, (50, 50))
        self.assertEqual(filtered.dtype, np.uint8)
        self.assertLess(np.var(filtered), np.var(noisy_img))

    def test_frost_filter_execution(self) -> None:
        np.random.seed(42)
        img = np.random.randint(0, 255, (30, 30), dtype=np.uint8)
        filtered = apply_frost_filter(img, window_size=5, damping_factor=2.0)

        self.assertEqual(filtered.shape, (30, 30))
        self.assertEqual(filtered.dtype, np.uint8)

    def test_preprocess_sar_pipeline(self) -> None:
        # Test 3-channel composite
        img_rgb = np.random.randint(0, 255, (40, 40, 3), dtype=np.uint8)
        res = preprocess_sar(img_rgb, filter_type="lee", window_size=5)
        self.assertEqual(res.shape, (40, 40, 3))


if __name__ == "__main__":
    unittest.main()
