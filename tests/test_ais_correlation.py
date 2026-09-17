"""Unit tests for CorrelateDetectionsWithAIS use case and geographic distance calculations."""

import unittest
from datetime import datetime, timezone

from sentinel_analysis.application.use_cases.correlate_ais_detections import (
    CorrelateDetectionsWithAIS,
    distance_to_bbox_meters,
    haversine_distance_meters,
)
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection


class StubAISRepo:
    def __init__(self, positions=None):
        self._positions = positions or []

    def get_vessel_positions(self, bbox=None, time_range=None, limit=None, latest_only=False):
        return list(self._positions)


class TestAISCorrelation(unittest.TestCase):
    def setUp(self):
        # Scan bounding box: Singapore Strait area (roughly 1.20 to 1.30 Lat, 103.70 to 103.80 Lon)
        # That's approx 11.1km high by 11.1km wide.
        self.bbox = BoundingBox(
            min_latitude=1.20,
            max_latitude=1.30,
            min_longitude=103.70,
            max_longitude=103.80,
        )
        self.scan = Scan(
            folder_name="test_scan_001",
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

    def test_haversine_distance(self):
        # Known distance: 1 deg latitude is approx 111,195m
        d = haversine_distance_meters(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(d, 111195.0, delta=500.0)

        # Same point distance is 0
        d_zero = haversine_distance_meters(1.25, 103.75, 1.25, 103.75)
        self.assertAlmostEqual(d_zero, 0.0, delta=0.01)

    def test_distance_to_bbox_meters(self):
        min_lat, max_lat = 1.20, 1.30
        min_lon, max_lon = 103.70, 103.80

        # Point inside bbox -> distance is 0.0
        inside_d = distance_to_bbox_meters(1.25, 103.75, min_lat, max_lat, min_lon, max_lon)
        self.assertEqual(inside_d, 0.0)

        # Point strictly on boundary -> distance is 0.0
        boundary_d = distance_to_bbox_meters(1.20, 103.70, min_lat, max_lat, min_lon, max_lon)
        self.assertEqual(boundary_d, 0.0)

        # Point ~100m north of box:
        # 1 deg lat ~ 111,195m => 100m ~ 0.0009 degrees
        d_north = distance_to_bbox_meters(1.3009, 103.75, min_lat, max_lat, min_lon, max_lon)
        self.assertAlmostEqual(d_north, 100.0, delta=5.0)

    def test_inside_bounding_box_correlation(self):
        # Detection at pixel (450, 450, 100, 100)
        # Lat range: y=450 to 550 => Lat: max_lat - 550 * 0.0001 = 1.245 to 1.255
        # Lon range: x=450 to 550 => Lon: min_lon + 450 * 0.0001 = 103.745 to 103.755
        # Centroid: (1.250, 103.750)
        det = ShipDetection(x=450, y=450, width=100, height=100, confidence=0.95)

        # AIS ping strictly inside detection box
        vessel = {
            "mmsi": "123456789",
            "vessel_name": "SEA TRADER",
            "vessel_type": "Cargo",
            "latitude": 1.250,
            "longitude": 103.750,
            "speed": 12.5,
            "heading": 90,
            "timestamp": "2026-09-01T12:00:00Z",
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel]))
        results = use_case.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["correlation_status"], "inside_box")
        self.assertTrue(r["is_correlated"])
        self.assertIsNotNone(r["correlated_ais"])
        self.assertEqual(r["correlated_ais"]["mmsi"], "123456789")
        self.assertEqual(r["correlated_ais"]["distance_to_box_meters"], 0.0)
        self.assertEqual(r["correlated_ais"]["match_type"], "inside_box")

    def test_outside_bounding_box_within_buffer(self):
        # Detection at pixel (450, 450, 100, 100) -> Box Lat [1.245, 1.255], Lon [103.745, 103.755]
        det = ShipDetection(x=450, y=450, width=100, height=100, confidence=0.90)

        # AIS ping ~45 meters north of detection box: Lat 1.2554, Lon 103.750
        # 0.0004 deg * 111,195 m/deg ~= 44.5 meters
        vessel = {
            "mmsi": "987654321",
            "vessel_name": "NORDIC STAR",
            "vessel_type": "Tanker",
            "latitude": 1.2554,
            "longitude": 103.750,
            "speed": 10.0,
            "heading": 180,
            "timestamp": "2026-09-01T12:00:00Z",
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel]))
        results = use_case.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["correlation_status"], "outside_box")
        self.assertTrue(r["is_correlated"])
        self.assertIsNotNone(r["correlated_ais"])
        self.assertGreater(r["correlated_ais"]["distance_to_box_meters"], 0.0)
        self.assertLessEqual(r["correlated_ais"]["distance_to_box_meters"], 100.0)
        self.assertEqual(r["correlated_ais"]["match_type"], "outside_box")

    def test_uncorrelated_when_beyond_buffer(self):
        # Detection at pixel (450, 450, 100, 100)
        det = ShipDetection(x=450, y=450, width=100, height=100, confidence=0.85)

        # AIS ping ~300 meters north of detection box: Lat 1.258, Lon 103.750
        # (1.258 - 1.255) = 0.003 deg * 111,195 m/deg ~= 333 meters (> 100m tolerance)
        vessel = {
            "mmsi": "555555555",
            "vessel_name": "FAR AWAY",
            "vessel_type": "Fishing",
            "latitude": 1.258,
            "longitude": 103.750,
            "speed": 5.0,
            "heading": 45,
            "timestamp": "2026-09-01T12:00:00Z",
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel]))
        results = use_case.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["correlation_status"], "uncorrelated")
        self.assertFalse(r["is_correlated"])
        self.assertIsNone(r["correlated_ais"])

    def test_closest_vessel_selected_when_multiple_in_ok_region(self):
        # Detection at pixel (450, 450, 100, 100) -> Box Lat [1.245, 1.255], Lon [103.745, 103.755]
        det = ShipDetection(x=450, y=450, width=100, height=100, confidence=0.90)

        # Vessel 1: 80m outside box
        # 0.00072 deg ~= 80m
        vessel1 = {
            "mmsi": "111111111",
            "vessel_name": "FURTHER VESSEL",
            "latitude": 1.25572,
            "longitude": 103.750,
        }

        # Vessel 2: 25m outside box
        # 0.00022 deg ~= 25m
        vessel2 = {
            "mmsi": "222222222",
            "vessel_name": "CLOSER VESSEL",
            "latitude": 1.25522,
            "longitude": 103.750,
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel1, vessel2]))
        results = use_case.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["correlation_status"], "outside_box")
        self.assertEqual(r["correlated_ais"]["mmsi"], "222222222")
        self.assertEqual(r["correlated_ais"]["vessel_name"], "CLOSER VESSEL")

    def test_inside_box_takes_precedence_over_outside_box(self):
        det = ShipDetection(x=450, y=450, width=100, height=100, confidence=0.90)

        vessel_outside = {
            "mmsi": "111111111",
            "vessel_name": "OUTSIDE BOX",
            "latitude": 1.2552,  # 20m outside box
            "longitude": 103.750,
        }
        vessel_inside = {
            "mmsi": "222222222",
            "vessel_name": "INSIDE BOX",
            "latitude": 1.250,   # inside box
            "longitude": 103.750,
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel_outside, vessel_inside]))
        results = use_case.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)

        self.assertEqual(results[0]["correlation_status"], "inside_box")
        self.assertEqual(results[0]["correlated_ais"]["mmsi"], "222222222")

    def test_one_to_one_greedy_assignment(self):
        # Two detections near each other
        det1 = ShipDetection(x=100, y=100, width=50, height=50, confidence=0.90)
        det2 = ShipDetection(x=120, y=100, width=50, height=50, confidence=0.90)

        # Single vessel that is closer to det1 than det2
        # det1 center x=125, y=125
        # det2 center x=145, y=125
        # Vessel at lon matching x=125
        v_lon = 103.70 + 125 * 0.0001
        v_lat = 1.30 - 125 * 0.0001
        vessel = {
            "mmsi": "333333333",
            "vessel_name": "SHARED CANDIDATE",
            "latitude": v_lat,
            "longitude": v_lon,
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel]))
        results = use_case.execute([det1, det2], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)

        # det1 gets the vessel, det2 remains uncorrelated
        self.assertEqual(results[0]["correlation_status"], "inside_box")
        self.assertEqual(results[0]["correlated_ais"]["mmsi"], "333333333")
        self.assertEqual(results[1]["correlation_status"], "uncorrelated")
        self.assertIsNone(results[1]["correlated_ais"])


if __name__ == "__main__":
    unittest.main()
