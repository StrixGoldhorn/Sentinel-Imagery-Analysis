"""Unit tests for ship wake detection, 180-degree heading ambiguity resolution, and AIS speed spoofing cross-validation."""

import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from sentinel_analysis.application.use_cases.correlate_ais_detections import CorrelateDetectionsWithAIS
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.detection.wake import (
    ShipWakeDetector,
    WakeAnalysisResult,
    compute_radon_transform,
    extract_chip,
    mask_ship_hull,
)


class TestWakeDetection(unittest.TestCase):
    def setUp(self):
        self.detector = ShipWakeDetector(pixel_spacing_meters=10.0, chip_size=128)

    def test_compute_radon_transform(self):
        # Create image with horizontal line across center
        img = np.zeros((100, 100), dtype=np.uint8)
        img[50, :] = 255
        sinogram = compute_radon_transform(img, angles_deg=np.array([0, 45, 90, 135], dtype=np.float32))
        self.assertEqual(sinogram.shape[0], 4)
        self.assertGreater(sinogram.shape[1], 100)
        # Horizontal line has highest contrast / projection sum variance at specific angle
        self.assertTrue(np.max(sinogram) > 0)

    def test_extract_chip_and_mask_hull(self):
        img = np.full((200, 200), 50, dtype=np.uint8)
        # Ship hull bright spot
        cv2.rectangle(img, (90, 95), (110, 105), 240, -1)
        chip, x0, y0 = extract_chip(img, 100, 100, chip_size=64)
        self.assertEqual(chip.shape, (64, 64))

        masked = mask_ship_hull(chip, 32, 32, length_px=20, beam_px=10, angle_deg=0)
        # Center of masked ship should now be filled with sea clutter median (~50) instead of 240
        self.assertLess(masked[32, 32], 100)

    def test_turbulent_wake_and_heading_disambiguation(self):
        # Ship moving East (+x direction, bearing 90 deg True North)
        # Hull is at (100, 100).
        # Wake trails BEHIND the ship to the West (left: x from 20 to 90, y=100).
        img = np.full((200, 200), 40, dtype=np.uint8)
        # Ship hull
        cv2.ellipse(img, (100, 100), (15, 6), 0, 0, 360, 230, -1)
        # Wake line trailing to the West (x < 100)
        cv2.line(img, (25, 100), (85, 100), 180, 2)

        det = ShipDetection(
            x=85,
            y=94,
            width=30,
            height=12,
            confidence=0.9,
            angle=0.0,
            length=300.0,  # 30 px * 10m
            beam=60.0,
            center_x=100.0,
            center_y=100.0,
        )

        res = self.detector.analyze_detection(img, det, pixel_spacing_m=10.0)
        self.assertTrue(res.wake_detected)
        self.assertIn(res.wake_type, ("turbulent", "composite"))
        # Wake extends West, so ship heading must be East (around 90 deg)
        self.assertIsNotNone(res.true_heading_deg)
        self.assertAlmostEqual(res.true_heading_deg, 90.0, delta=15.0)
        self.assertIsNotNone(res.estimated_speed_knots)
        self.assertGreater(res.estimated_speed_knots, 5.0)

    def test_kelvin_arms_wake_detection(self):
        # Ship heading South (+y direction, bearing 180 deg)
        # Hull is at (100, 80).
        # Kelvin V-arms trail to the North (upwards, y < 80)
        img = np.full((200, 200), 35, dtype=np.uint8)
        cv2.ellipse(img, (100, 90), (6, 16), 0, 0, 360, 240, -1)
        # Two Kelvin arms opening upwards at ~19 deg half angle
        # Left arm
        cv2.line(img, (100, 75), (75, 25), 170, 2)
        # Right arm
        cv2.line(img, (100, 75), (125, 25), 170, 2)

        det = ShipDetection(
            x=94,
            y=74,
            width=12,
            height=32,
            confidence=0.88,
            angle=90.0,
            length=160.0,
            beam=60.0,
            center_x=100.0,
            center_y=90.0,
        )

        res = self.detector.analyze_detection(img, det, pixel_spacing_m=10.0)
        self.assertTrue(res.wake_detected)
        self.assertIsNotNone(res.true_heading_deg)
        # Wake trails North, heading is South (~180 deg)
        self.assertAlmostEqual(res.true_heading_deg, 180.0, delta=25.0)

    def test_ais_speed_spoofing_cross_validation(self):
        # Simulated wake analysis with 18.5 kn transit speed
        wake_result = WakeAnalysisResult(
            wake_detected=True,
            wake_confidence=0.85,
            true_heading_deg=90.0,
            estimated_speed_knots=18.5,
        )

        # AIS reporting loitering / stationary at 1.2 knots (e.g. spoofed loitering)
        ais_spoofed = {"mmsi": "111222333", "speed": 1.2, "heading": 92.0}
        det = ShipDetection(x=50, y=50, width=20, height=10, center_x=60, center_y=55)

        img = np.full((120, 120), 40, dtype=np.uint8)
        # Draw ship and wake
        cv2.ellipse(img, (60, 55), (10, 4), 0, 0, 360, 220, -1)
        cv2.line(img, (15, 55), (48, 55), 190, 2)

        res = self.detector.analyze_detection(img, det, ais_record=ais_spoofed)
        self.assertTrue(res.wake_detected)
        self.assertTrue(res.is_speed_spoofed)
        self.assertGreaterEqual(res.speed_discrepancy_knots, 4.0)

        # Legitimate AIS reporting matching speed 18.0 kn
        ais_legit = {"mmsi": "111222333", "speed": 18.0, "heading": 90.0}
        res_legit = self.detector.analyze_detection(img, det, ais_record=ais_legit)
        self.assertFalse(res_legit.is_speed_spoofed)


class TestClassicalShipDetectorWakeIntegration(unittest.TestCase):
    def test_classical_detector_with_wake(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            img_path = Path(tmp_dir) / "wake_ship.png"
            img = np.full((250, 250), 30, dtype=np.uint8)
            # Ship 1: bright hull at (120, 120)
            cv2.ellipse(img, (120, 120), (16, 6), 0, 0, 360, 245, -1)
            # Wake line trailing to the left (intensity 120, below hull detection threshold)
            cv2.line(img, (40, 120), (95, 120), 120, 2)
            cv2.imwrite(str(img_path), img)

            detector = ClassicalShipDetector(
                minimum_area=20,
                maximum_area=2000,
                enable_wake_detection=True,
            )
            result = detector.detect(img_path, threshold=180, enable_wake_detection=True)

            self.assertGreaterEqual(len(result.detections), 1)
            det = result.detections[0]
            self.assertTrue(det.wake_detected)
            self.assertIsNotNone(det.wake_heading)
            self.assertIsNotNone(det.wake_speed_knots)


class TestAISCorrelationWakeSpoofing(unittest.TestCase):
    def setUp(self):
        self.bbox = BoundingBox(
            min_latitude=1.20,
            max_latitude=1.30,
            min_longitude=103.70,
            max_longitude=103.80,
        )
        self.scan = Scan(
            folder_name="wake_scan",
            image_path="/tmp/wake.png",
            bbox=self.bbox,
            acquisition=Acquisition(
                acquired_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
                satellite="Sentinel-1A",
                product_type="GRD",
            ),
        )

    def test_ais_correlation_flags_speed_spoofing(self):
        # Detection at pixel (500, 500) with wake speed = 20.0 knots
        det = ShipDetection(
            x=480,
            y=480,
            width=40,
            height=40,
            confidence=0.95,
            length=180.0,
            beam=30.0,
            wake_detected=True,
            wake_heading=90.0,
            wake_speed_knots=20.0,
            wake_confidence=0.88,
        )

        # Candidate AIS reporting stationary loitering speed = 1.0 knot
        vessel = {
            "mmsi": "999111222",
            "vessel_name": "SPOOFER 1",
            "latitude": 1.250,
            "longitude": 103.750,
            "speed": 1.0,
            "heading": 90.0,
            "timestamp": "2026-09-01T12:00:00Z",
        }

        class MockAISRepo:
            def get_vessel_positions(self, **kwargs):
                return [vessel]

        use_case = CorrelateDetectionsWithAIS(MockAISRepo())
        results = use_case.execute([det], self.scan, 1000, 1000, tolerance_meters=100.0)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r["is_correlated"])
        self.assertTrue(r["is_speed_spoofed"])
        self.assertAlmostEqual(r["speed_discrepancy_knots"], 19.0, delta=0.5)
        self.assertIn("AIS speed spoofing detected", " ".join(r.get("dark_vessel_reasons", [])))
        self.assertTrue(r["correlated_ais"]["is_speed_spoofed"])


if __name__ == "__main__":
    unittest.main()
