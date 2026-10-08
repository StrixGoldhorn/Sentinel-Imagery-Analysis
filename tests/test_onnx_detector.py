"""Tests for Deep Learning OBB Ship Detector and SAR-CNN Classifier."""

import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from sentinel_analysis.domain.entities import ShipDetection
from sentinel_analysis.infrastructure.detection.detection_saver import save_detection_results
from sentinel_analysis.infrastructure.detection.onnx_detector import (
    DeepLearningShipDetector,
    MockONNXBackend,
    OpenCVDNNBackend,
    SARCNNClassifier,
    VESSEL_CLASSES,
)


class TestSARCNNClassifier(unittest.TestCase):
    """Test vessel classification engine and physical heuristic fallbacks."""

    def setUp(self) -> None:
        self.classifier = SARCNNClassifier()

    def test_classify_large_tanker(self) -> None:
        dummy_chip = np.zeros((32, 32), dtype=np.uint8)
        v_class, conf = self.classifier.classify_chip(
            chip=dummy_chip,
            length_m=240.0,
            beam_m=42.0,
            mean_intensity=140.0,
            peak_intensity=230.0,
        )
        self.assertIn(v_class, ("Tanker", "Cargo"))
        self.assertGreater(conf, 0.7)

    def test_classify_large_cargo(self) -> None:
        dummy_chip = np.zeros((32, 32), dtype=np.uint8)
        v_class, conf = self.classifier.classify_chip(
            chip=dummy_chip,
            length_m=320.0,
            beam_m=50.0,
            mean_intensity=150.0,
            peak_intensity=245.0,
        )
        self.assertEqual(v_class, "Cargo")
        self.assertGreaterEqual(conf, 0.85)

    def test_classify_passenger(self) -> None:
        dummy_chip = np.zeros((32, 32), dtype=np.uint8)
        v_class, conf = self.classifier.classify_chip(
            chip=dummy_chip,
            length_m=130.0,
            beam_m=38.0,
            mean_intensity=145.0,
            peak_intensity=220.0,
        )
        self.assertEqual(v_class, "Passenger")
        self.assertGreaterEqual(conf, 0.8)

    def test_classify_military(self) -> None:
        dummy_chip = np.zeros((32, 32), dtype=np.uint8)
        v_class, conf = self.classifier.classify_chip(
            chip=dummy_chip,
            length_m=110.0,
            beam_m=18.0,
            mean_intensity=160.0,
            peak_intensity=240.0,
        )
        self.assertEqual(v_class, "Military")
        self.assertGreaterEqual(conf, 0.75)

    def test_classify_tug(self) -> None:
        dummy_chip = np.zeros((32, 32), dtype=np.uint8)
        v_class, conf = self.classifier.classify_chip(
            chip=dummy_chip,
            length_m=30.0,
            beam_m=12.0,
            mean_intensity=130.0,
            peak_intensity=210.0,
        )
        self.assertEqual(v_class, "Tug")
        self.assertGreaterEqual(conf, 0.8)

    def test_classify_fishing(self) -> None:
        dummy_chip = np.zeros((32, 32), dtype=np.uint8)
        v_class, conf = self.classifier.classify_chip(
            chip=dummy_chip,
            length_m=28.0,
            beam_m=7.0,
            mean_intensity=110.0,
            peak_intensity=190.0,
        )
        self.assertEqual(v_class, "Fishing")
        self.assertGreaterEqual(conf, 0.8)


class TestBackends(unittest.TestCase):
    """Test inference backend abstractions."""

    def test_mock_backend(self) -> None:
        backend = MockONNXBackend()
        dummy_blob = np.zeros((1, 1, 64, 64), dtype=np.float32)
        out = backend.infer(dummy_blob)
        self.assertIsInstance(out, np.ndarray)
        backend.close()

    def test_opencv_backend_nonexistent_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            OpenCVDNNBackend(Path("non_existent_model.onnx"))


class TestDeepLearningShipDetector(unittest.TestCase):
    """Test full DeepLearningShipDetector pipeline."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        # Create synthetic SAR image (200x200) with sea clutter (intensity ~20)
        # and a bright ship hull (intensity ~240, 10x40 pixels rotated at ~30 deg)
        self.image_path = self.tmp_path / "sar_synthetic.png"
        img = np.full((200, 200), 20, dtype=np.uint8)

        # Draw rotated rectangle ship at (100, 100)
        rect = ((100.0, 100.0), (12.0, 45.0), 30.0)
        box = np.int32(cv2.boxPoints(rect))
        cv2.fillPoly(img, [box], 245)
        cv2.imwrite(str(self.image_path), img)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_detect_returns_detection_result_with_obb(self) -> None:
        detector = DeepLearningShipDetector(
            confidence_threshold=0.2,
            pixel_spacing_meters=10.0,
        )
        res = detector.detect(self.image_path, threshold=50)

        self.assertEqual(res.image_width, 200)
        self.assertEqual(res.image_height, 200)
        self.assertGreaterEqual(len(res.detections), 1)

        det = res.detections[0]
        self.assertIsInstance(det, ShipDetection)
        self.assertIsNotNone(det.polygon_points)
        self.assertEqual(len(det.polygon_points), 4)

        # Center should be near (100, 100)
        self.assertAlmostEqual(det.center_x, 100.0, delta=10.0)
        self.assertAlmostEqual(det.center_y, 100.0, delta=10.0)

        # Length and beam
        self.assertGreaterEqual(det.length, 30.0)
        self.assertGreater(det.beam, 5.0)

        # Vessel classification
        self.assertIsNotNone(det.vessel_class)
        self.assertIn(det.vessel_class, VESSEL_CLASSES.values())
        self.assertGreater(det.classification_confidence, 0.0)

    def test_detect_with_wake_integration(self) -> None:
        # Add a wake trail behind the ship
        img = cv2.imread(str(self.image_path), cv2.IMREAD_GRAYSCALE)
        # Draw wake line extending away from ship
        cv2.line(img, (100, 100), (30, 60), 120, 2)
        cv2.imwrite(str(self.image_path), img)

        detector = DeepLearningShipDetector(
            confidence_threshold=0.2,
            enable_wake_detection=True,
            pixel_spacing_meters=10.0,
        )
        res = detector.detect(self.image_path, threshold=50, enable_wake_detection=True)
        self.assertGreaterEqual(len(res.detections), 1)

    def test_rotated_nms_deduplication(self) -> None:
        # Two identical overlapping candidates should be merged by Rotated NMS
        detector = DeepLearningShipDetector()
        dummy_img = np.zeros((100, 100), dtype=np.uint8)
        candidates = [
            {
                "center_x": 50.0,
                "center_y": 50.0,
                "width_px": 15.0,
                "height_px": 50.0,
                "angle": 25.0,
                "confidence": 0.95,
                "vessel_class": "Cargo",
                "classification_confidence": 0.95,
                "length_m": 500.0,
                "beam_m": 150.0,
            },
            {
                "center_x": 51.0,
                "center_y": 50.5,
                "width_px": 14.5,
                "height_px": 49.0,
                "angle": 26.0,
                "confidence": 0.90,
                "vessel_class": "Cargo",
                "classification_confidence": 0.90,
                "length_m": 490.0,
                "beam_m": 145.0,
            },
        ]
        merged = detector._apply_rotated_nms(
            dummy_img,
            candidates,
            conf_thresh=0.3,
            nms_thresh=0.4,
            pixel_spacing=10.0,
            do_wake=False,
        )
        # Expect Rotated NMS to suppress the lower scoring overlapping detection
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].confidence, 0.95)

    def test_detection_saver_includes_vessel_class(self) -> None:
        detector = DeepLearningShipDetector(confidence_threshold=0.2)
        res = detector.detect(self.image_path, threshold=50)
        self.assertGreaterEqual(len(res.detections), 1)

        result_dict = save_detection_results(
            image_path=self.image_path,
            detections=res.detections,
            image_width=res.image_width,
            image_height=res.image_height,
        )

        # Check JSON
        saved_json = json.loads(Path(result_dict["detections_json_path"]).read_text(encoding="utf-8"))
        self.assertIn("detections", saved_json)
        first_det = saved_json["detections"][0]
        self.assertIn("vessel_class", first_det)
        self.assertIn("classification_confidence", first_det)

        # Check GeoJSON
        geojson_path = self.tmp_path / f"{self.image_path.stem}_detections.geojson"
        self.assertTrue(geojson_path.exists())
        gj_data = json.loads(geojson_path.read_text(encoding="utf-8"))
        prop = gj_data["features"][0]["properties"]
        self.assertIn("vessel_class", prop)
        self.assertIn("classification_confidence", prop)


if __name__ == "__main__":
    unittest.main()
