import unittest
from datetime import datetime, timedelta, timezone

from sentinel_analysis.application.use_cases.reidentify_vessels import ReidentifyVessels
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.reidentification import (
    PassDetectionCandidate,
    ReIDMatchStatus,
    calculate_reid_similarity,
)


class MockScanRepo:
    def __init__(self, scans=None):
        self._scans = {s.folder_name: s for s in (scans or [])}

    def get(self, folder_name: str):
        return self._scans.get(folder_name)

    def list(self):
        return list(self._scans.values())


class TestVesselReidentification(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
        self.t1 = self.t0 + timedelta(hours=2)

    def test_calculate_reid_similarity_high_match(self):
        cand_a = PassDetectionCandidate(
            detection_index=0,
            latitude=1.25,
            longitude=103.75,
            length_m=120.0,
            width_m=22.0,
            heading=90.0,
            speed_knots=12.0,
        )
        # In 2 hours at 12 knots (~22.2 km/h), ship moves ~44 km eastward (~0.4 deg lon)
        cand_b = PassDetectionCandidate(
            detection_index=0,
            latitude=1.25,
            longitude=104.14,
            length_m=122.0,
            width_m=21.5,
            heading=92.0,
            speed_knots=11.8,
        )
        score = calculate_reid_similarity(cand_a, cand_b, time_delta_hours=2.0)
        self.assertGreater(score.composite_score, 0.70)
        self.assertGreater(score.dimension_similarity, 0.85)
        self.assertGreater(score.heading_similarity, 0.85)
        self.assertGreater(score.kinematic_feasibility, 0.75)

    def test_calculate_reid_similarity_impossible_speed_penalty(self):
        cand_a = PassDetectionCandidate(
            detection_index=0,
            latitude=1.25,
            longitude=103.75,
            length_m=100.0,
            width_m=20.0,
        )
        # 300 km away in 1 hour implies >150 knots
        cand_b = PassDetectionCandidate(
            detection_index=0,
            latitude=3.95,
            longitude=103.75,
            length_m=100.0,
            width_m=20.0,
        )
        score = calculate_reid_similarity(cand_a, cand_b, time_delta_hours=1.0)
        self.assertLessEqual(score.kinematic_feasibility, 0.15)
        self.assertLess(score.composite_score, 0.40)

    def test_calculate_reid_similarity_dimension_mismatch(self):
        cand_a = PassDetectionCandidate(
            detection_index=0,
            latitude=1.25,
            longitude=103.75,
            length_m=50.0,
            width_m=10.0,
        )
        cand_b = PassDetectionCandidate(
            detection_index=0,
            latitude=1.25,
            longitude=103.76,
            length_m=280.0,
            width_m=45.0,
        )
        score = calculate_reid_similarity(cand_a, cand_b, time_delta_hours=1.0)
        self.assertLess(score.dimension_similarity, 0.30)

    def test_reidentify_vessels_use_case_bipartite_matching(self):
        scan_a = Scan(
            folder_name="scan_current",
            bbox=BoundingBox(103.0, 1.0, 105.0, 2.0),
            acquisition=Acquisition(acquired_at=self.t1, satellite="SENTINEL-1A", product_type="GRD"),
            image_path="test_a.png",
        )
        scan_b = Scan(
            folder_name="scan_prior",
            bbox=BoundingBox(103.0, 1.0, 105.0, 2.0),
            acquisition=Acquisition(acquired_at=self.t0, satellite="SENTINEL-1A", product_type="GRD"),
            image_path="test_b.png",
        )
        repo = MockScanRepo([scan_a, scan_b])
        use_case = ReidentifyVessels(repo)

        # Candidate 0 matches prior candidate 1; candidate 1 is a new arrival
        current_candidates = [
            {
                "latitude": 1.25,
                "longitude": 104.14,
                "length_m": 120.0,
                "width_m": 22.0,
                "heading": 90.0,
            },
            {
                "latitude": 1.80,
                "longitude": 103.30,
                "length_m": 45.0,
                "width_m": 8.0,
            },
        ]
        prior_candidates = [
            {
                "latitude": 1.70,
                "longitude": 104.50,
                "length_m": 250.0,
                "width_m": 38.0,
            },
            {
                "latitude": 1.25,
                "longitude": 103.75,
                "length_m": 122.0,
                "width_m": 21.5,
                "heading": 92.0,
            },
        ]

        result = use_case.execute(
            scan_id="scan_current",
            prior_scan_id="scan_prior",
            current_candidates=current_candidates,
            prior_candidates=prior_candidates,
            min_similarity=0.45,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["current_scan_id"], "scan_current")
        self.assertEqual(result["prior_scan_id"], "scan_prior")
        self.assertEqual(result["total_current_candidates"], 2)
        self.assertEqual(result["total_prior_candidates"], 2)

        # Match check
        matched = [m for m in result["matches"] if m["status"] in ("CONFIRMED", "PROBABLE")]
        self.assertGreaterEqual(len(matched), 1)
        self.assertEqual(matched[0]["current_detection_index"], 0)
        self.assertEqual(matched[0]["prior_detection_index"], 1)
        self.assertGreater(matched[0]["similarity_score"], 0.70)

        # GeoJSON track validation
        geojson = result["geojson_tracks"]
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertGreater(len(geojson["features"]), 0)


if __name__ == "__main__":
    unittest.main()
