import unittest
from datetime import datetime, timezone

from sentinel_analysis.application.use_cases.correlate_ais_detections import CorrelateDetectionsWithAIS
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection
from sentinel_analysis.domain.uncertainty import (
    calculate_association_likelihood,
    calculate_competing_candidate_probabilities,
    calculate_mahalanobis_distance,
    compute_joint_covariance_ellipse,
)


class StubAISRepo:
    def __init__(self, positions=None):
        self._positions = positions or []

    def get_vessel_positions(self, bbox=None, time_range=None, limit=None, latest_only=False):
        return list(self._positions)


class TestProbabilisticAssociation(unittest.TestCase):
    def setUp(self):
        self.bbox = BoundingBox(
            min_latitude=1.20,
            max_latitude=1.30,
            min_longitude=103.70,
            max_longitude=103.80,
        )
        self.scan = Scan(
            folder_name="test_scan_prob",
            image_path="/tmp/test_scan.png",
            bbox=self.bbox,
            acquisition=Acquisition(
                acquired_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
                satellite="Sentinel-1A",
                product_type="GRD",
            ),
        )
        self.image_width = 1000
        self.image_height = 1000

    def test_mahalanobis_distance_on_axes(self):
        # Semi-major 20m along North (orientation 0), Semi-minor 10m along East
        # Point at dy=20, dx=0: exactly 1.0 standard deviations
        d_m = calculate_mahalanobis_distance(
            dx_meters=0.0,
            dy_meters=20.0,
            semi_major_m=20.0,
            semi_minor_m=10.0,
            orientation_deg=0.0,
        )
        self.assertAlmostEqual(d_m, 1.0, places=2)

        # Point at dy=0, dx=10: exactly 1.0 standard deviations
        d_m2 = calculate_mahalanobis_distance(
            dx_meters=10.0,
            dy_meters=0.0,
            semi_major_m=20.0,
            semi_minor_m=10.0,
            orientation_deg=0.0,
        )
        self.assertAlmostEqual(d_m2, 1.0, places=2)

        # Point at origin
        d_m0 = calculate_mahalanobis_distance(
            dx_meters=0.0,
            dy_meters=0.0,
            semi_major_m=20.0,
            semi_minor_m=10.0,
            orientation_deg=0.0,
        )
        self.assertEqual(d_m0, 0.0)

    def test_mahalanobis_rotated_ellipse(self):
        # Orientation 90 degrees clockwise (major axis points East)
        d_east = calculate_mahalanobis_distance(
            dx_meters=30.0,
            dy_meters=0.0,
            semi_major_m=30.0,
            semi_minor_m=15.0,
            orientation_deg=90.0,
        )
        self.assertAlmostEqual(d_east, 1.0, places=2)

    def test_compute_joint_covariance_ellipse(self):
        radar = {
            "semi_major_axis_meters": 20.0,
            "semi_minor_axis_meters": 10.0,
            "orientation_deg": 45.0,
        }
        ais = {
            "semi_major_m": 15.0,
            "semi_minor_m": 8.0,
            "orientation_deg": 45.0,
        }
        joint = compute_joint_covariance_ellipse(radar, ais)
        # Joint semi-major must be strictly greater than either individual semi-major
        self.assertGreater(joint.semi_major_axis_meters, 20.0)
        self.assertGreater(joint.semi_minor_axis_meters, 10.0)
        self.assertGreater(joint.cep_meters, 0.0)

    def test_competing_candidate_probabilities_division(self):
        # Two equally competitive vessels
        likelihoods = {"vessel_A": 0.9, "vessel_B": 0.9}
        probs = calculate_competing_candidate_probabilities(likelihoods, null_hypothesis_likelihood=0.1)
        # Both share probability mass equally below 0.5 due to competition + null hypothesis
        self.assertAlmostEqual(probs["vessel_A"], probs["vessel_B"], places=2)
        self.assertLess(probs["vessel_A"], 0.5)
        self.assertGreater(probs["vessel_A"], 0.4)

        # Single dominant candidate
        single_lh = {"vessel_A": 0.95}
        single_prob = calculate_competing_candidate_probabilities(single_lh, null_hypothesis_likelihood=0.05)
        self.assertGreater(single_prob["vessel_A"], 0.90)

    def test_use_case_correlation_populates_probabilistic_fields(self):
        # Detection at center: x=500, y=500 (Lat: 1.25, Lon: 103.75)
        det = ShipDetection(x=490, y=490, width=20, height=20, confidence=0.92, length=80.0, beam=18.0)

        # Two candidate vessels near the same detection
        vessel1 = {
            "mmsi": "111222333",
            "vessel_name": "CARGO ALPHA",
            "latitude": 1.25005,
            "longitude": 103.75005,
            "timestamp": self.scan.acquisition.acquired_at,
            "speed": 12.0,
            "course": 45.0,
            "length": 82.0,
        }
        vessel2 = {
            "mmsi": "444555666",
            "vessel_name": "CARGO BETA",
            "latitude": 1.25020,
            "longitude": 103.75020,
            "timestamp": self.scan.acquisition.acquired_at,
            "speed": 11.5,
            "course": 42.0,
            "length": 78.0,
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel1, vessel2]))
        correlated = use_case.execute(
            detections=[det],
            scan=self.scan,
            image_width=self.image_width,
            image_height=self.image_height,
            tolerance_meters=300.0,
        )

        self.assertEqual(len(correlated), 1)
        res = correlated[0]
        self.assertTrue(res["is_correlated"])
        self.assertIn("association_probability", res)
        self.assertIsNotNone(res["association_probability"])
        self.assertGreater(res["association_probability"], 0.0)
        self.assertLessEqual(res["association_probability"], 1.0)

        # Competing candidates list should contain both candidates
        competing = res.get("competing_candidates", [])
        self.assertEqual(len(competing), 2)
        self.assertEqual(competing[0]["mmsi"], res["correlated_ais"]["mmsi"])
        self.assertIn("mahalanobis_distance", res["correlated_ais"])
        self.assertIn("association_probability", res["correlated_ais"])
        self.assertIn("spatial_uncertainty", res["correlated_ais"])


if __name__ == "__main__":
    unittest.main()
