"""Unit tests for Oriented Bounding Box (OBB) ship detection."""

import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector


class OBBDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path(__file__).resolve().parent / "runtime" / "obb_test"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.image_path = self.test_dir / "synthetic_ship.png"

    def tearDown(self) -> None:
        if self.image_path.is_file():
            self.image_path.unlink(missing_ok=True)

    def test_obb_calculates_heading_and_dimensions(self) -> None:
        # Create 100x100 synthetic SAR image with a bright oriented rectangle
        img = np.zeros((100, 100), dtype=np.uint8)
        # Create synthetic vessel (oriented blob)
        for y in range(30, 70):
            for x in range(45, 55):
                img[y, x] = 250

        Image.fromarray(img).save(self.image_path)

        detector = ClassicalShipDetector(min_area=20, pixel_spacing_m=10.0)
        detections, width, height = detector.detect(self.image_path, threshold=50)

        self.assertEqual(width, 100)
        self.assertEqual(height, 100)
        self.assertEqual(len(detections), 1)

        ship = detections[0]
        self.assertGreater(ship.length, 0)
        self.assertGreater(ship.beam, 0)
        self.assertGreaterEqual(ship.length, ship.beam)
        self.assertIsNotNone(ship.angle)
        self.assertEqual(len(ship.polygon_points), 4)
        self.assertGreater(ship.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
