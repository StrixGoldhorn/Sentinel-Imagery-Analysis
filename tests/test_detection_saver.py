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

    def test_save_detection_results_with_vessel_name_in_ais(self) -> None:
        detections = [
            {
                "index": 0,
                "x": 20,
                "y": 20,
                "width": 25,
                "height": 25,
                "confidence": 0.92,
                "correlation_status": "inside_box",
                "is_correlated": True,
                "correlated_ais": {"mmsi": "987654321", "vessel_name": "PACIFIC EXPLORER"},
            }
        ]

        result = save_detection_results(
            image_path=self.image_path,
            detections=detections,
            image_width=100,
            image_height=100,
        )

        self.assertEqual(result["ship_count"], 1)
        self.assertEqual(result["inside_box_count"], 1)
        payload = json.loads((self.dir_path / "test_sar_detections.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["detections"][0]["correlated_ais"]["vessel_name"], "PACIFIC EXPLORER")

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
        self.assertTrue((self.dir_path / "nonexistent_detections.geojson").is_file())

    def test_save_detection_results_generates_geojson(self) -> None:
        detections = [
            {
                "index": 0,
                "x": 20,
                "y": 20,
                "width": 25,
                "height": 25,
                "confidence": 0.88,
                "length": 85.0,
                "beam": 18.0,
                "angle": 45.0,
                "lat": 1.250,
                "lng": 103.750,
                "geo_polygon": [
                    (1.2505, 103.7495),
                    (1.2505, 103.7505),
                    (1.2495, 103.7505),
                    (1.2495, 103.7495),
                ],
                "correlation_status": "inside_box",
                "is_correlated": True,
                "correlated_ais": {
                    "mmsi": "123456789",
                    "vessel_name": "SEA GLORY",
                    "vessel_type": "Tanker",
                    "speed": 12.5,
                },
            }
        ]

        result = save_detection_results(
            image_path=self.image_path,
            detections=detections,
            image_width=100,
            image_height=100,
        )

        geojson_file = self.dir_path / "test_sar_detections.geojson"
        std_geojson_file = self.dir_path / "detections.geojson"
        self.assertTrue(geojson_file.is_file())
        self.assertTrue(std_geojson_file.is_file())

        gj = json.loads(geojson_file.read_text(encoding="utf-8"))
        self.assertEqual(gj["type"], "FeatureCollection")
        self.assertEqual(len(gj["features"]), 1)

        feat = gj["features"][0]
        self.assertEqual(feat["type"], "Feature")
        self.assertEqual(feat["geometry"]["type"], "Polygon")
        # In GeoJSON coordinates are [lon, lat]
        coords = feat["geometry"]["coordinates"][0]
        self.assertEqual(len(coords), 5)  # closed ring (4 points + first point repeated)
        self.assertAlmostEqual(coords[0][0], 103.7495, places=4)
        self.assertAlmostEqual(coords[0][1], 1.2505, places=4)
        self.assertEqual(feat["properties"]["vessel_name"], "SEA GLORY")
        self.assertEqual(feat["properties"]["vessel_type"], "Tanker")
        self.assertEqual(feat["properties"]["is_correlated"], True)

    def test_save_detection_results_generates_esri_world_files(self) -> None:
        bbox = {
            "min_latitude": 1.20,
            "max_latitude": 1.30,
            "min_longitude": 103.70,
            "max_longitude": 103.80,
        }

        result = save_detection_results(
            image_path=self.image_path,
            detections=[],
            image_width=100,
            image_height=100,
            bbox=bbox,
        )

        self.assertTrue(result["world_files_created"])
        detected_pgw = self.dir_path / "test_sar_detected.pgw"
        detected_prj = self.dir_path / "test_sar_detected.prj"
        std_pgw = self.dir_path / "detected_ships.pgw"
        std_prj = self.dir_path / "detected_ships.prj"

        self.assertTrue(detected_pgw.is_file())
        self.assertTrue(detected_prj.is_file())
        self.assertTrue(std_pgw.is_file())
        self.assertTrue(std_prj.is_file())

        lines = [line.strip() for line in detected_pgw.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 6)
        dx = float(lines[0])
        dy = float(lines[3])
        x_center = float(lines[4])
        y_center = float(lines[5])

        self.assertAlmostEqual(dx, 0.001, places=5)
        self.assertAlmostEqual(dy, -0.001, places=5)
        self.assertAlmostEqual(x_center, 103.7005, places=5)
        self.assertAlmostEqual(y_center, 1.2995, places=5)
        self.assertIn("GCS_WGS_1984", detected_prj.read_text(encoding="utf-8"))

    def test_save_detection_results_includes_dark_and_ghost_vessels(self):
        dark_det = {
            "x": 30,
            "y": 30,
            "width": 20,
            "height": 10,
            "confidence": 0.94,
            "length": 150.0,
            "beam": 25.0,
            "lat": 1.25,
            "lng": 103.75,
            "correlation_status": "uncorrelated",
            "is_correlated": False,
            "is_dark_vessel": True,
            "dark_vessel_risk": "CRITICAL",
            "dark_vessel_score": 92.5,
            "estimated_class": "Large Commercial / Cargo / Tanker",
            "dark_vessel_reasons": ["Uncorrelated large vessel (150m >= 100m)"],
        }
        ghost_vessel = {
            "mmsi": "999000111",
            "vessel_name": "PHANTOM GHOST",
            "vessel_type": "Fishing",
            "latitude": 1.26,
            "longitude": 103.76,
            "speed": 8.5,
            "heading": 180.0,
            "dead_reckoned": True,
            "reason": "No corresponding radar return detected within SAR footprint",
        }

        result = save_detection_results(
            image_path=self.image_path,
            detections=[dark_det],
            image_width=100,
            image_height=100,
            ghost_vessels=[ghost_vessel],
        )

        self.assertEqual(result["dark_vessel_count"], 1)
        self.assertEqual(result["critical_dark_count"], 1)
        self.assertEqual(result["ghost_vessel_count"], 1)
        self.assertEqual(len(result["ghost_vessels"]), 1)

        # Verify JSON
        json_path = Path(result["detections_json_path"])
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(json_data["dark_vessel_count"], 1)
        self.assertEqual(json_data["critical_dark_count"], 1)
        self.assertEqual(json_data["ghost_vessel_count"], 1)
        self.assertEqual(len(json_data["ghost_vessels"]), 1)
        self.assertEqual(json_data["ghost_vessels"][0]["mmsi"], "999000111")

        # Verify GeoJSON
        geojson_path = Path(result["detections_geojson_path"])
        geojson_data = json.loads(geojson_path.read_text(encoding="utf-8"))
        features = geojson_data["features"]
        self.assertEqual(len(features), 2)

        det_feat = features[0]
        self.assertTrue(det_feat["properties"]["is_dark_vessel"])
        self.assertEqual(det_feat["properties"]["dark_vessel_risk"], "CRITICAL")
        self.assertEqual(det_feat["properties"]["dark_vessel_score"], 92.5)

        ghost_feat = features[1]
        self.assertEqual(ghost_feat["geometry"]["type"], "Point")
        self.assertEqual(ghost_feat["geometry"]["coordinates"], [103.76, 1.26])
        self.assertEqual(ghost_feat["properties"]["feature_type"], "ghost_vessel")
        self.assertEqual(ghost_feat["properties"]["mmsi"], "999000111")
        self.assertEqual(ghost_feat["properties"]["vessel_name"], "PHANTOM GHOST")


if __name__ == "__main__":
    unittest.main()
