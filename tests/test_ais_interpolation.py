"""Unit tests for AIS kinematic interpolation, Cubic Hermite Spline, and uncertainty covariance ellipse."""

import math
import unittest
from datetime import datetime, timezone, timedelta

from sentinel_analysis.application.use_cases.correlate_ais_detections import (
    CorrelateDetectionsWithAIS,
    compute_uncertainty_ellipse,
    interpolate_kinematic_track,
    is_point_in_ellipse,
    dead_reckon_position,
)
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection


class TestAISKinematicInterpolation(unittest.TestCase):
    def test_compute_uncertainty_ellipse_base(self):
        # Zero delta seconds gives base GNSS ellipse
        ellipse = compute_uncertainty_ellipse(0.0, speed_knots=10.0, heading_deg=45.0)
        self.assertIn("semi_major_m", ellipse)
        self.assertIn("semi_minor_m", ellipse)
        self.assertIn("orientation_deg", ellipse)
        self.assertEqual(ellipse["orientation_deg"], 45.0)
        # Base error scaled by ~2.447
        self.assertGreaterEqual(ellipse["semi_major_m"], 10.0)

    def test_compute_uncertainty_ellipse_growth(self):
        # Growth over time: uncertainty at 600s is much larger than at 60s
        e60 = compute_uncertainty_ellipse(60.0, speed_knots=15.0, heading_deg=90.0)
        e600 = compute_uncertainty_ellipse(600.0, speed_knots=15.0, heading_deg=90.0)
        self.assertGreater(e600["semi_major_m"], e60["semi_major_m"])
        self.assertGreater(e600["semi_minor_m"], e60["semi_minor_m"])

    def test_compute_uncertainty_ellipse_interpolation_bridge(self):
        # Interpolated midpoint has reduced uncertainty compared to unbracketed extrapolation
        extrap = compute_uncertainty_ellipse(300.0, speed_knots=12.0, is_interpolated=False)
        interp = compute_uncertainty_ellipse(300.0, speed_knots=12.0, is_interpolated=True, interpolation_ratio=0.5)
        self.assertLess(interp["semi_major_m"], extrap["semi_major_m"])

    def test_is_point_in_ellipse_oriented(self):
        center_lat = 1.3000
        center_lon = 103.8000
        semi_major = 100.0
        semi_minor = 40.0
        # Ellipse pointing North (0 deg)
        # Point 50m North -> inside
        # 1 deg lat ~ 111320m => 50m ~ 0.000449 deg
        pt_north_lat = center_lat + 50.0 / 111320.0
        inside, d = is_point_in_ellipse(pt_north_lat, center_lon, center_lat, center_lon, semi_major, semi_minor, 0.0)
        self.assertTrue(inside)
        self.assertLess(d, 1.0)

        # Point 120m North -> outside
        pt_far_lat = center_lat + 120.0 / 111320.0
        inside_far, d_far = is_point_in_ellipse(pt_far_lat, center_lon, center_lat, center_lon, semi_major, semi_minor, 0.0)
        self.assertFalse(inside_far)
        self.assertGreater(d_far, 1.0)

        # Point 50m East when orientation is 0 deg: 50m > semi_minor (40m) -> outside
        pt_east_lon = center_lon + 50.0 / (111320.0 * math.cos(math.radians(center_lat)))
        inside_east, d_east = is_point_in_ellipse(center_lat, pt_east_lon, center_lat, center_lon, semi_major, semi_minor, 0.0)
        self.assertFalse(inside_east)

        # If ellipse is rotated 90 deg (pointing East), 50m East is along semi-major -> inside!
        inside_rot, d_rot = is_point_in_ellipse(center_lat, pt_east_lon, center_lat, center_lon, semi_major, semi_minor, 90.0)
        self.assertTrue(inside_rot)

    def test_interpolate_kinematic_track_exact_match(self):
        t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        records = [
            {"latitude": 1.25, "longitude": 103.75, "speed": 12.0, "heading": 90.0, "timestamp": t0.isoformat()}
        ]
        res = interpolate_kinematic_track(records, t0)
        self.assertEqual(res["method"], "exact_match")
        self.assertAlmostEqual(res["latitude"], 1.25, places=5)
        self.assertAlmostEqual(res["longitude"], 103.75, places=5)
        self.assertFalse(res["dead_reckoned"])

    def test_interpolate_kinematic_track_cubic_hermite_spline(self):
        # Two records 10 minutes apart (600s)
        # Vessel traveling East at ~15 knots (7.716 m/s)
        # Over 600s, distance is approx 4630m
        t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=600)

        lat0, lon0 = 1.2500, 103.7000
        # East displacement: 4630m / (111320 * cos(1.25)) ~= 0.0416 deg
        lat1, lon1 = 1.2500, 103.7416

        records = [
            {"latitude": lat0, "longitude": lon0, "speed": 15.0, "heading": 90.0, "timestamp": t0.isoformat()},
            {"latitude": lat1, "longitude": lon1, "speed": 15.0, "heading": 90.0, "timestamp": t1.isoformat()},
        ]

        # Target time is exactly at midpoint (t0 + 300s)
        target_t = t0 + timedelta(seconds=300)
        res = interpolate_kinematic_track(records, target_t)

        self.assertEqual(res["method"], "cubic_hermite_spline")
        self.assertTrue(res["dead_reckoned"])
        # Midpoint longitude should be close to halfway between lon0 and lon1
        expected_lon = (lon0 + lon1) / 2.0
        self.assertAlmostEqual(res["longitude"], expected_lon, delta=0.002)
        self.assertAlmostEqual(res["latitude"], 1.2500, places=4)
        self.assertAlmostEqual(res["speed"], 15.0, delta=1.0)
        self.assertAlmostEqual(res["heading"], 90.0, delta=2.0)
        self.assertIn("uncertainty_ellipse", res)
        self.assertGreater(res["uncertainty_ellipse"]["semi_major_m"], 0.0)

    def test_interpolate_kinematic_track_turning_spline(self):
        # Vessel turning from North (heading 0) to East (heading 90)
        t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=300)
        lat0, lon0 = 1.2000, 103.7000
        lat1, lon1 = 1.2150, 103.7150

        records = [
            {"latitude": lat0, "longitude": lon0, "speed": 12.0, "heading": 0.0, "timestamp": t0.isoformat()},
            {"latitude": lat1, "longitude": lon1, "speed": 12.0, "heading": 90.0, "timestamp": t1.isoformat()},
        ]

        target_t = t0 + timedelta(seconds=150)
        res = interpolate_kinematic_track(records, target_t)
        self.assertEqual(res["method"], "cubic_hermite_spline")
        # In a 90 deg turn, intermediate heading should be between 0 and 90
        self.assertGreater(res["heading"], 5.0)
        self.assertLess(res["heading"], 85.0)

    def test_interpolate_kinematic_track_extrapolation(self):
        # Target time after last point -> kinematic extrapolation
        t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=300)
        records = [
            {"latitude": 1.200, "longitude": 103.700, "speed": 10.0, "heading": 90.0, "timestamp": t0.isoformat()},
            {"latitude": 1.200, "longitude": 103.715, "speed": 10.0, "heading": 90.0, "timestamp": t1.isoformat()},
        ]
        target_t = t1 + timedelta(seconds=120)  # 2 minutes in the future
        res = interpolate_kinematic_track(records, target_t)
        self.assertIn(res["method"], ("kinematic_extrapolation", "dead_reckoning"))
        self.assertTrue(res["dead_reckoned"])
        self.assertGreater(res["longitude"], 103.715)


class StubRepo:
    def __init__(self, vessels):
        self._vessels = vessels

    def get_vessel_positions(self, **kwargs):
        return self._vessels


class TestAISCorrelationWithInterpolation(unittest.TestCase):
    def setUp(self):
        self.bbox = BoundingBox(
            min_latitude=1.20,
            max_latitude=1.30,
            min_longitude=103.70,
            max_longitude=103.80,
        )
        self.overpass_time = datetime(2026, 9, 1, 12, 10, 0, tzinfo=timezone.utc)
        self.scan = Scan(
            folder_name="spline_scan",
            image_path="/tmp/spline.png",
            bbox=self.bbox,
            acquisition=Acquisition(
                acquired_at=self.overpass_time,
                satellite="Sentinel-1A",
                product_type="GRD",
            ),
        )
        self.img_w = 1000
        self.img_h = 1000

    def test_multi_point_track_correlates_to_satellite_aperture(self):
        # Ship ping 1 at 12:00:00 (10 min before overpass)
        t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Ship ping 2 at 12:20:00 (10 min after overpass)
        t1 = datetime(2026, 9, 1, 12, 20, 0, tzinfo=timezone.utc)

        # Ship heading East (90 deg) at 15 knots (~7.716 m/s)
        # Total distance over 1200s is ~9259m (~0.0832 deg Lon)
        # At midpoint 12:10:00, ship is halfway
        lon0 = 103.7084
        lon1 = 103.7916
        lat = 1.2500

        vessel_track = [
            {"mmsi": "999888777", "vessel_name": "SPLINE RUNNER", "latitude": lat, "longitude": lon0, "speed": 15.0, "heading": 90.0, "timestamp": t0.isoformat()},
            {"mmsi": "999888777", "vessel_name": "SPLINE RUNNER", "latitude": lat, "longitude": lon1, "speed": 15.0, "heading": 90.0, "timestamp": t1.isoformat()},
        ]

        # Detection at center of scan (pixel 500, 500)
        # Scan bbox center: lat 1.250, lon 103.750
        det = ShipDetection(x=480, y=480, width=40, height=40, confidence=0.92)

        use_case = CorrelateDetectionsWithAIS(StubRepo(vessel_track))
        results = use_case.execute([det], self.scan, self.img_w, self.img_h, tolerance_meters=150.0, enable_kinematics=True)

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r["is_correlated"])
        self.assertIsNotNone(r["correlated_ais"])
        c_ais = r["correlated_ais"]
        self.assertEqual(c_ais["mmsi"], "999888777")
        self.assertEqual(c_ais["interpolation_method"], "cubic_hermite_spline")
        self.assertIn("uncertainty_ellipse", c_ais)
        self.assertGreater(c_ais["uncertainty_ellipse"]["semi_major_m"], 0.0)


if __name__ == "__main__":
    unittest.main()
