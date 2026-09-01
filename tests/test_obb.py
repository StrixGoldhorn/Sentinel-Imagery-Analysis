"""Unit tests for Oriented Bounding Box (OBB) ship detection."""

import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector


def test_obb_calculates_heading_and_dimensions() -> None:
    import tempfile
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        image_path = Path(temp_dir) / "synthetic_ship.png"

        # Create 100x100 synthetic SAR image with a bright oriented rectangle
        img = np.zeros((100, 100), dtype=np.uint8)
        for y in range(30, 70):
            for x in range(45, 55):
                img[y, x] = 250

        Image.fromarray(img).save(image_path)

        detector = ClassicalShipDetector(min_area=20, pixel_spacing_m=10.0)
        detections, width, height = detector.detect(image_path, threshold=50)

        assert width == 100
        assert height == 100
        assert len(detections) == 1

        ship = detections[0]
        assert ship.length > 0
        assert ship.beam > 0
        assert ship.length >= ship.beam
        assert ship.angle is not None
        assert len(ship.polygon_points) == 4
        assert ship.confidence > 0.0


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

