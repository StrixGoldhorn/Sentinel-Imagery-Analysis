"""Unit tests for CorrelateDetectionsWithAIS use case and geographic distance calculations."""

import unittest
from datetime import datetime, timezone

from sentinel_analysis.application.use_cases.correlate_ais_detections import (
    CorrelateDetectionsWithAIS,
    assess_dark_vessel,
    dead_reckon_position,
    distance_to_bbox_meters,
    extract_ghost_vessels,
    haversine_distance_meters,
    point_in_polygon_and_distance,
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

    def test_dead_reckon_position_pure_function(self):
        # 10 knots = 5.14444 m/s. In 3600s => 18,520m North (~0.1665 deg lat)
        new_lat, new_lon, dist = dead_reckon_position(0.0, 0.0, 10.0, 0.0, 3600.0)
        self.assertAlmostEqual(new_lat, 0.1665, places=3)
        self.assertEqual(new_lon, 0.0)
        self.assertAlmostEqual(dist, 18520.0, delta=10.0)

        # 0 time delta => exact coordinates
        lat0, lon0, dist0 = dead_reckon_position(1.25, 103.75, 12.0, 90.0, 0.0)
        self.assertEqual(lat0, 1.25)
        self.assertEqual(lon0, 103.75)
        self.assertEqual(dist0, 0.0)

        # Missing speed or heading => exact coordinates
        lat_none, lon_none, dist_none = dead_reckon_position(1.25, 103.75, None, 90.0, 500.0)
        self.assertEqual(lat_none, 1.25)
        self.assertEqual(dist_none, 0.0)

    def test_kinematic_correlation_matches_moving_vessel_that_would_otherwise_miss(self):
        # Detection box: Lat [1.245, 1.255], Lon [103.745, 103.755], Centroid (1.250, 103.750)
        det = ShipDetection(x=450, y=450, width=100, height=100, confidence=0.92)

        # Vessel reported 10 minutes prior to scan (11:50:00 vs scan at 12:00:00 = 600s)
        # Position: Lat 1.222, Lon 103.750.
        # Distance to box is ~2,500 meters South (far outside 100m tolerance).
        # SOG: ~29.15 knots (~15 m/s). Heading: 0 (True North).
        # In 600s, vessel travels 9000 meters North (~0.081 deg).
        # Projected Lat: 1.222 + 9000/111195 ~= 1.222 + 0.0809 = 1.3029... wait,
        # Let's calibrate distance precisely:
        # Distance from 1.242 to 1.250 is 0.008 deg * 111,195 m/deg = 889.5 meters.
        # At speed 10.0 knots = 5.1444 m/s for 172.9 seconds => ~889 meters.
        # Let's use dt = 300 seconds (5 minutes prior):
        # Speed: 10.0 knots (5.1444 m/s). In 300s, travels 1543 meters (~0.01387 deg lat).
        # Base Lat: 1.250 - 0.01387 = 1.23613.
        # Base position is 1.23613, which is ~985 meters south of the detection box edge (1.245).
        # Without dead reckoning, it would miss completely (uncorrelated).
        # With dead reckoning, it projects exactly to Lat 1.250 (inside_box).
        vessel = {
            "mmsi": "777888999",
            "vessel_name": "FAST COMMUTER",
            "latitude": 1.23613,
            "longitude": 103.750,
            "speed": 10.0,
            "heading": 0.0,
            "timestamp": "2026-09-01T11:55:00Z",  # 300s before scan
        }

        # 1. With kinematics enabled (default)
        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel]))
        results = use_case.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res["correlation_status"], "inside_box")
        self.assertTrue(res["is_correlated"])
        self.assertTrue(res["correlated_ais"]["dead_reckoned"])
        self.assertAlmostEqual(res["correlated_ais"]["propagation_delta_seconds"], 300.0, places=0)
        self.assertAlmostEqual(res["correlated_ais"]["raw_latitude"], 1.23613, places=4)
        self.assertAlmostEqual(res["correlated_ais"]["latitude"], 1.250, places=3)

        # 2. With kinematics explicitly disabled
        results_no_kin = use_case.execute(
            [det],
            self.scan,
            self.image_width,
            self.image_height,
            tolerance_meters=100.0,
            enable_kinematics=False,
        )
        self.assertEqual(results_no_kin[0]["correlation_status"], "uncorrelated")
        self.assertFalse(results_no_kin[0]["is_correlated"])
        self.assertIsNone(results_no_kin[0]["correlated_ais"])

    def test_point_in_polygon_and_distance_pure_math(self):
        m_lat = 111195.0
        m_lon = 111195.0

        # Square polygon 100m x 100m around (1.25, 103.75)
        poly = [
            (1.25 - 50 / m_lat, 103.75 - 50 / m_lon),
            (1.25 - 50 / m_lat, 103.75 + 50 / m_lon),
            (1.25 + 50 / m_lat, 103.75 + 50 / m_lon),
            (1.25 + 50 / m_lat, 103.75 - 50 / m_lon),
        ]

        # Inside center
        inside, dist = point_in_polygon_and_distance(1.25, 103.75, poly)
        self.assertTrue(inside)
        self.assertEqual(dist, 0.0)

        # 20m East of edge (dx = +70m from center)
        inside_e, dist_e = point_in_polygon_and_distance(1.25, 103.75 + 70 / m_lon, poly)
        self.assertFalse(inside_e)
        self.assertAlmostEqual(dist_e, 20.0, delta=1.0)

        # Degenerate polygon (< 3 points)
        inside_deg, dist_deg = point_in_polygon_and_distance(1.25, 103.75, [(1.25, 103.75)])
        self.assertFalse(inside_deg)
        self.assertEqual(dist_deg, float("inf"))

    def test_correlation_with_rotated_obb_polygon(self):
        # Rotated diamond ship hull inside AABB [400..600, 400..600]
        # Pixel vertices: (500, 420), (580, 500), (500, 580), (420, 500)
        obb_vertices = ((500.0, 420.0), (580.0, 500.0), (500.0, 580.0), (420.0, 500.0))
        det = ShipDetection(
            x=400,
            y=400,
            width=200,
            height=200,
            confidence=0.95,
            polygon_points=obb_vertices,
        )

        # Point at pixel (405, 405) is inside the AABB [400..600], but > 500m away from the rotated diamond hull
        # 1 pixel = 0.0001 deg lat/lon ~= 11.1 meters
        # Pixel (405, 405) in lat/lon:
        corner_lat = 1.30 - 405 * 0.0001
        corner_lon = 103.70 + 405 * 0.0001

        vessel_at_empty_corner = {
            "mmsi": "111222333",
            "vessel_name": "CORNER VESSEL",
            "latitude": corner_lat,
            "longitude": corner_lon,
        }

        use_case = CorrelateDetectionsWithAIS(StubAISRepo([vessel_at_empty_corner]))

        # With rotated OBB polygon, it correctly determines the vessel is outside tolerance from the true hull
        results = use_case.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)
        self.assertEqual(results[0]["correlation_status"], "uncorrelated")
        self.assertFalse(results[0]["is_correlated"])

        # Now test a vessel directly on the ship center (500, 500)
        center_lat = 1.30 - 500 * 0.0001
        center_lon = 103.70 + 500 * 0.0001
        vessel_at_center = {
            "mmsi": "444555666",
            "vessel_name": "CENTER VESSEL",
            "latitude": center_lat,
            "longitude": center_lon,
        }

        use_case_center = CorrelateDetectionsWithAIS(StubAISRepo([vessel_at_center]))
        results_center = use_case_center.execute([det], self.scan, self.image_width, self.image_height, tolerance_meters=100.0)
        self.assertEqual(results_center[0]["correlation_status"], "inside_box")
        self.assertTrue(results_center[0]["is_correlated"])
        self.assertEqual(results_center[0]["correlated_ais"]["mmsi"], "444555666")

    def test_assess_dark_vessel_correlated(self):
        det = {
            "is_correlated": True,
            "correlation_status": "inside_box",
            "length": 150.0,
            "beam": 25.0,
            "confidence": 0.95,
        }
        res = assess_dark_vessel(det)
        self.assertFalse(res["is_dark_vessel"])
        self.assertEqual(res["dark_vessel_risk"], "NOMINAL")
        self.assertEqual(res["dark_vessel_score"], 0.0)

    def test_assess_dark_vessel_critical_solas(self):
        # 140m vessel without AIS, realistic naval architecture aspect ratio 5.6
        det = {
            "is_correlated": False,
            "correlation_status": "uncorrelated",
            "length": 140.0,
            "beam": 25.0,
            "confidence": 0.92,
        }
        res = assess_dark_vessel(det)
        self.assertTrue(res["is_dark_vessel"])
        self.assertEqual(res["dark_vessel_risk"], "CRITICAL")
        self.assertGreaterEqual(res["dark_vessel_score"], 0.80)
        self.assertEqual(res["estimated_class"], "Large Commercial / Cargo / Tanker")
        self.assertTrue(any("SOLAS" in r for r in res["dark_vessel_reasons"]))

    def test_assess_dark_vessel_high_solas(self):
        # 45m vessel without AIS
        det = {
            "is_correlated": False,
            "correlation_status": "uncorrelated",
            "length": 45.0,
            "beam": 10.0,
            "confidence": 0.85,
        }
        res = assess_dark_vessel(det)
        self.assertTrue(res["is_dark_vessel"])
        self.assertIn(res["dark_vessel_risk"], ("HIGH", "CRITICAL"))
        self.assertGreaterEqual(res["dark_vessel_score"], 0.50)

    def test_assess_dark_vessel_small_craft_or_clutter(self):
        # 12m vessel with low confidence
        det = {
            "is_correlated": False,
            "correlation_status": "uncorrelated",
            "length": 12.0,
            "beam": 6.0,
            "confidence": 0.40,
        }
        res = assess_dark_vessel(det)
        self.assertFalse(res["is_dark_vessel"])
        self.assertIn(res["dark_vessel_risk"], ("LOW", "NOMINAL"))

    def test_extract_ghost_vessels(self):
        # Scan bbox is lat 1.20..1.30, lon 103.70..103.80
        # Vessel 1: inside bbox, matched
        # Vessel 2: inside bbox, NOT matched -> Ghost vessel anomaly!
        # Vessel 3: outside bbox, NOT matched -> Ignored
        vessels = [
            {
                "mmsi": "111",
                "vessel_name": "MATCHED VESSEL",
                "latitude": 1.25,
                "longitude": 103.75,
                "speed": 10.0,
            },
            {
                "mmsi": "222",
                "vessel_name": "GHOST VESSEL",
                "latitude": 1.26,
                "longitude": 103.76,
                "speed": 12.5,
            },
            {
                "mmsi": "333",
                "vessel_name": "DISTANT VESSEL",
                "latitude": 2.50,
                "longitude": 105.00,
                "speed": 5.0,
            },
        ]
        matched_mmsis = {"111"}
        ghosts = extract_ghost_vessels(vessels, matched_mmsis, self.scan.bbox)
        self.assertEqual(len(ghosts), 1)
        self.assertEqual(ghosts[0]["mmsi"], "222")
        self.assertEqual(ghosts[0]["vessel_name"], "GHOST VESSEL")
        self.assertIn("no corresponding radar detection found", ghosts[0]["reason"].lower())

    def test_execute_with_intelligence(self):
        # 1 radar detection (120m long, uncorrelated) and 1 candidate AIS inside footprint not matched
        det = ShipDetection(
            x=200,
            y=200,
            width=80,
            height=30,
            confidence=0.90,
            length=120.0,
            beam=25.0,
        )
        ghost_ais = {
            "mmsi": "999888777",
            "vessel_name": "PHANTOM SHIP",
            "latitude": 1.22,
            "longitude": 103.72,
            "speed": 14.0,
        }
        use_case = CorrelateDetectionsWithAIS(StubAISRepo([ghost_ais]))
        intelligence = use_case.execute_with_intelligence(
            [det],
            self.scan,
            self.image_width,
            self.image_height,
            tolerance_meters=50.0,
        )
        self.assertEqual(intelligence["ship_count"], 1)
        self.assertEqual(intelligence["correlated_count"], 0)
        self.assertEqual(intelligence["dark_vessel_count"], 1)
        self.assertEqual(intelligence["ghost_vessel_count"], 1)
        self.assertEqual(len(intelligence["dark_vessels"]), 1)
        self.assertEqual(intelligence["dark_vessels"][0]["dark_vessel_risk"], "CRITICAL")
        self.assertEqual(len(intelligence["ghost_vessels"]), 1)
        self.assertEqual(intelligence["ghost_vessels"][0]["mmsi"], "999888777")


if __name__ == "__main__":
    unittest.main()
