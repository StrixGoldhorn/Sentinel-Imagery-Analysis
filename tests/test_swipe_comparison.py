"""Unit tests for repeat SAR pass swipe comparison."""

import unittest
from datetime import datetime, timedelta, timezone

from sentinel_analysis.application.use_cases.compare_repeat_passes import CompareRepeatPasses
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.swipe_comparison import (
    RepeatPassCandidate,
    SwipeComparisonReport,
    TargetChangeStatus,
    compute_bbox_intersection,
    haversine_m,
)


class TestSwipeComparisonDomain(unittest.TestCase):
    def test_haversine_distance(self):
        # Distance between (1.0, 103.0) and (1.0, 103.001) ~111 meters
        d = haversine_m(1.0, 103.0, 1.0, 103.001)
        self.assertAlmostEqual(d, 111.19, delta=5.0)

    def test_bbox_intersection_and_overlap(self):
        b1 = BoundingBox(103.0, 1.0, 104.0, 2.0)
        b2 = BoundingBox(103.5, 1.5, 104.5, 2.5)
        inter, ratio = compute_bbox_intersection(b1, b2)
        self.assertIsNotNone(inter)
        self.assertEqual(inter.min_longitude, 103.5)
        self.assertEqual(inter.min_latitude, 1.5)
        self.assertEqual(inter.max_longitude, 104.0)
        self.assertEqual(inter.max_latitude, 2.0)
        self.assertGreater(ratio, 0.2)

        # Disjoint boxes
        b3 = BoundingBox(105.0, 5.0, 106.0, 6.0)
        inter3, ratio3 = compute_bbox_intersection(b1, b3)
        self.assertIsNone(inter3)
        self.assertEqual(ratio3, 0.0)

    def test_target_change_status_geojson(self):
        target = TargetChangeStatus(
            target_id="tgt_1",
            status="ARRIVED",
            latitude=1.35,
            longitude=103.85,
            confidence=0.9,
            length_m=120.0,
        )
        feat = target.to_geojson_feature()
        self.assertEqual(feat["type"], "Feature")
        self.assertEqual(feat["geometry"]["coordinates"], [103.85, 1.35])
        self.assertEqual(feat["properties"]["status"], "ARRIVED")


class MockScanRepository:
    def __init__(self, scans=None):
        self._scans = {s.folder_name: s for s in (scans or [])}

    def get(self, scan_id):
        return self._scans.get(scan_id)

    def list_all(self):
        return list(self._scans.values())


class TestCompareRepeatPassesUseCase(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
        self.t_prior = self.t0 - timedelta(days=12)

        # Target scan (T2)
        self.target_scan = Scan(
            folder_name="scan_t2",
            bbox=BoundingBox(103.0, 1.0, 104.0, 2.0),
            acquisition=Acquisition(self.t0, "Sentinel-1", "sentinel-1-grd"),
            image_path="scans/scan_t2/stitched.png",
            metadata={
                "detections": [
                    {"latitude": 1.30, "longitude": 103.50, "confidence": 0.95, "length_m": 150.0},  # Persistent
                    {"latitude": 1.70, "longitude": 103.80, "confidence": 0.88, "length_m": 80.0},   # Arrived
                ]
            },
        )

        # Baseline repeat scan (T1 - 12 days prior, overlapping AOI)
        self.baseline_scan = Scan(
            folder_name="scan_t1",
            bbox=BoundingBox(103.1, 1.05, 104.1, 2.05),
            acquisition=Acquisition(self.t_prior, "Sentinel-1", "sentinel-1-grd"),
            image_path="scans/scan_t1/stitched.png",
            metadata={
                "detections": [
                    {"latitude": 1.3005, "longitude": 103.5005, "confidence": 0.92, "length_m": 145.0}, # Matched to persistent
                    {"latitude": 1.15, "longitude": 103.20, "confidence": 0.85, "length_m": 200.0},     # Departed
                ]
            },
        )

        # Disjoint scan in different region
        self.other_scan = Scan(
            folder_name="scan_europe",
            bbox=BoundingBox(4.0, 51.0, 5.0, 52.0),
            acquisition=Acquisition(self.t_prior, "Sentinel-1", "sentinel-1-grd"),
            image_path="scans/scan_europe/stitched.png",
            metadata={},
        )

        self.repo = MockScanRepository([self.target_scan, self.baseline_scan, self.other_scan])
        self.use_case = CompareRepeatPasses(self.repo)

    def test_auto_detect_repeat_baseline_and_diff_targets(self):
        result = self.use_case.execute(target_scan_id="scan_t2")
        self.assertTrue(result["success"])
        self.assertEqual(result["target_scan_id"], "scan_t2")
        self.assertEqual(result["baseline_scan_id"], "scan_t1")

        # Temporal delta ~12 days
        self.assertAlmostEqual(result["temporal_delta"]["days"], 12.0, delta=0.5)

        # Spatial overlap
        self.assertGreater(result["spatial_overlap"]["overlap_pct"], 70.0)

        # Candidates should contain scan_t1 but NOT scan_europe
        candidate_ids = [c["scan_id"] for c in result["candidates"]]
        self.assertIn("scan_t1", candidate_ids)
        self.assertNotIn("scan_europe", candidate_ids)

        # Diff summary
        summary = result["diff_summary"]
        self.assertEqual(summary["persistent_count"], 1)
        self.assertEqual(summary["arrived_count"], 1)
        self.assertEqual(summary["departed_count"], 1)

    def test_explicit_baseline_selection(self):
        result = self.use_case.execute(target_scan_id="scan_t2", baseline_scan_id="scan_t1")
        self.assertEqual(result["baseline_scan_id"], "scan_t1")

    def test_invalid_target_scan_raises_error(self):
        with self.assertRaises(ValueError):
            self.use_case.execute(target_scan_id="nonexistent_scan")

    def test_invalid_baseline_scan_raises_error(self):
        with self.assertRaises(ValueError):
            self.use_case.execute(target_scan_id="scan_t2", baseline_scan_id="nonexistent_baseline")


if __name__ == "__main__":
    unittest.main()
