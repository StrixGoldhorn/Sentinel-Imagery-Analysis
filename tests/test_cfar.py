"""Unit tests for CFAR and Dual-Polarization SAR ship detection."""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from sentinel_analysis.infrastructure.detection.cfar import (
    ca_cfar_2d,
    fuse_dual_polarization,
    go_cfar_2d,
    so_cfar_2d,
)
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector


class TestCFARAlgorithms(unittest.TestCase):
    def setUp(self):
        # Create synthetic sea clutter (mean=30, std=5)
        np.random.seed(42)
        self.clutter = np.clip(np.random.normal(30, 5, (120, 120)), 0, 255).astype(np.uint8)

        # Place a bright ship target (4x4, intensity 220)
        self.test_img = self.clutter.copy()
        self.test_img[58:62, 58:62] = 220

    def test_ca_cfar_2d_detects_target(self):
        mask, thresh_map = ca_cfar_2d(self.test_img, guard_size=7, train_size=21, factor=3.5, min_threshold=35)
        self.assertEqual(mask.shape, self.test_img.shape)
        self.assertEqual(thresh_map.shape, self.test_img.shape)

        # Target center must be flagged
        self.assertEqual(mask[60, 60], 255)
        # Background sea clutter should be largely 0
        clutter_detections = np.sum(mask[:40, :40] == 255)
        self.assertLessEqual(clutter_detections, 2)

    def test_go_cfar_2d_detects_target(self):
        mask, thresh_map = go_cfar_2d(self.test_img, guard_size=7, train_size=21, factor=2.5, min_threshold=35)
        self.assertEqual(mask.shape, self.test_img.shape)
        self.assertEqual(mask[60, 60], 255)

    def test_so_cfar_2d_detects_target(self):
        mask, thresh_map = so_cfar_2d(self.test_img, guard_size=7, train_size=21, factor=2.5, min_threshold=35)
        self.assertEqual(mask.shape, self.test_img.shape)
        self.assertEqual(mask[60, 60], 255)

    def test_fuse_dual_polarization_modes(self):
        vv = np.full((100, 100), 50, dtype=np.uint8)  # High ocean co-pol backscatter
        vh = np.full((100, 100), 10, dtype=np.uint8)  # Low ocean cross-pol backscatter

        # Ship has high cross-pol return
        vv[40:60, 45:55] = 200
        vh[40:60, 45:55] = 180

        for mode in ("ratio", "difference", "product", "enhanced"):
            fused = fuse_dual_polarization(vv, vh, mode=mode)
            self.assertEqual(fused.shape, (100, 100))
            self.assertEqual(fused.dtype, np.uint8)
            # Ship intensity should significantly exceed background
            self.assertGreater(fused[50, 50], fused[10, 10])


class TestClassicalShipDetectorCFAR(unittest.TestCase):
    def test_detector_with_cfar_ca_method(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            img_path = Path(tmp_dir) / "test_scene.png"
            np.random.seed(123)
            img = np.clip(np.random.normal(25, 4, (120, 120)), 0, 255).astype(np.uint8)
            # Add oriented ship
            for y in range(40, 70):
                for x in range(50, 60):
                    img[y, x] = 240
            Image.fromarray(img).save(img_path)

            detector = ClassicalShipDetector(
                min_area=20,
                pixel_spacing_m=10.0,
                detection_method="cfar_ca",
                cfar_guard_size=5,
                cfar_train_size=15,
                cfar_factor=3.0,
            )
            result = detector.detect(img_path, threshold=30)
            self.assertGreaterEqual(len(result.detections), 1)
            ship = result.detections[0]
            self.assertGreater(ship.length, 0)
            self.assertGreater(ship.beam, 0)
            self.assertGreater(ship.confidence, 0.0)

    def test_detector_with_dual_pol_auto_discovery(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            vv_path = Path(tmp_dir) / "scan_vv.png"
            vh_path = Path(tmp_dir) / "scan_vh.png"

            vv = np.full((100, 100), 60, dtype=np.uint8)
            vh = np.full((100, 100), 10, dtype=np.uint8)
            vv[40:60, 45:55] = 230
            vh[40:60, 45:55] = 210

            Image.fromarray(vv).save(vv_path)
            Image.fromarray(vh).save(vh_path)

            detector = ClassicalShipDetector(
                min_area=20,
                pixel_spacing_m=10.0,
                dual_pol_mode="ratio",
            )
            result = detector.detect(vv_path, threshold=30)
            self.assertGreaterEqual(len(result.detections), 1)


if __name__ == "__main__":
    unittest.main()
