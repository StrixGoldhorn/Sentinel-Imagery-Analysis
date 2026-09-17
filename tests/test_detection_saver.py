"""Tests for saving latest ship detection CV results."""

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from sentinel_analysis.domain.entities import ShipDetection
from sentinel_analysis.infrastructure.detection.detection_saver import save_detection_results


class TestDetectionSaver(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        # Create a sample test image (100x100 grayscale image)
        self.image_path = self.dir_path / "test_sar.png"
        sample_img = np.zeros((100, 100), dtype=np.uint8)
        sample_img[30:50, 40:60] = 200  # Bright spot
        cv2.imwrite(str(self.image_path), sample_img)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_detection_results_creates_annotated_image_and_json(self) -> None:
        detections = [
            ShipDetection(
                x=40,
                y=30,
                width=20,
                height=20,
                confidence=0.88,
                angle=45.0,
                length=25.0,
                beam=12.0,
                center_x=50.0,
                center_y=40.0,
                polygon_points=[[40, 30], [60, 30], [60, 50], [40, 50]],
            )
        ]

        result = save_detection_results(
            image_path=self.image_path,
            detections=detections,
            image_width=100,
            image_height=100,
            metadata={"threshold": 40},
        )

        # Check returned dictionary
        self.assertEqual(result["ship_count"], 1)
        self.assertEqual(result["detected_image_name"], "test_sar_detected.png")
        self.assertEqual(result["detections_json_name"], "test_sar_detections.json")

        # Verify files exist in the same folder as the image
        expected_detected_img = self.dir_path / "test_sar_detected.png"
        expected_standard_img = self.dir_path / "detected_ships.png"
        expected_detections_json = self.dir_path / "test_sar_detections.json"
        expected_standard_json = self.dir_path / "detection_results.json"

        self.assertTrue(expected_detected_img.is_file(), "Detected image file should exist")
        self.assertTrue(expected_standard_img.is_file(), "Standard detected_ships.png should exist")
        self.assertTrue(expected_detections_json.is_file(), "Detections JSON file should exist")
        self.assertTrue(expected_standard_json.is_file(), "Standard detection_results.json should exist")

        # Verify annotated image can be opened and has correct dimensions
        annotated = cv2.imread(str(expected_detected_img))
        self.assertIsNotNone(annotated)
        self.assertEqual(annotated.shape[0], 100)
        self.assertEqual(annotated.shape[1], 100)

        # Verify JSON contents
        payload = json.loads(expected_detections_json.read_text(encoding="utf-8"))
        self.assertEqual(payload["ship_count"], 1)
        self.assertEqual(payload["image_file"], "test_sar.png")
        self.assertEqual(payload["parameters"]["threshold"], 40)
        self.assertEqual(len(payload["detections"]), 1)
        det_data = payload["detections"][0]
        self.assertEqual(det_data["confidence"], 0.88)
        self.assertEqual(det_data["angle"], 45.0)
        self.assertEqual(det_data["polygon_points"], [[40, 30], [60, 30], [60, 50], [40, 50]])

    def test_save_detection_results_with_ais_correlation(self) -> None:
        detections = [
            {
                "index": 0,
                "x": 10,
                "y": 10,
                "width": 15,
                "height": 15,
                "confidence": 0.95,
                "correlation_status": "inside_box",
                "is_correlated": True,
                "correlated_ais": {"mmsi": "123456789", "name": "CARGO VESSEL"},
            },
            {
                "index": 1,
                "x": 60,
                "y": 60,
                "width": 10,
                "height": 10,
                "confidence": 0.65,
                "correlation_status": "uncorrelated",
                "is_correlated": False,
                "correlated_ais": None,
            },
        ]

        result = save_detection_results(
            image_path=self.image_path,
            detections=detections,
            image_width=100,
            image_height=100,
        )

        self.assertEqual(result["ship_count"], 2)
        self.assertEqual(result["correlated_count"], 1)
        self.assertEqual(result["inside_box_count"], 1)
        self.assertEqual(result["uncorrelated_count"], 1)

        payload = json.loads((self.dir_path / "test_sar_detections.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["inside_box_count"], 1)
        self.assertEqual(payload["correlated_count"], 1)
        self.assertEqual(payload["detections"][0]["correlated_ais"]["name"], "CARGO VESSEL")

    def test_save_empty_detections(self) -> None:
        result = save_detection_results(
            image_path=self.image_path,
            detections=[],
            image_width=100,
            image_height=100,
        )
        self.assertEqual(result["ship_count"], 0)
        self.assertTrue((self.dir_path / "test_sar_detected.png").is_file())
        self.assertTrue((self.dir_path / "test_sar_detections.json").is_file())

    def test_missing_image_file_handled_gracefully(self) -> None:
        missing_path = self.dir_path / "nonexistent.png"
        result = save_detection_results(
            image_path=missing_path,
            detections=[ShipDetection(1, 1, 5, 5, 0.9)],
            image_width=50,
            image_height=50,
        )
        self.assertEqual(result["ship_count"], 1)
        self.assertIsNone(result["detected_image_path"])
        self.assertTrue((self.dir_path / "nonexistent_detections.json").is_file())


if __name__ == "__main__":
    unittest.main()
