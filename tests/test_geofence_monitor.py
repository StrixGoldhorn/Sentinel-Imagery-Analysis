"""Unit and integration tests for Marine Protected Area (MPA) and EEZ geofencing engine."""

from __future__ import annotations

import unittest
from sentinel_analysis.application.use_cases.geofence_monitor import (
    GeofenceMonitor,
    point_in_polygon,
    polygon_bounding_box,
    DEFAULT_GEOFENCE_ZONES,
)
from sentinel_analysis.domain.entities import BoundingBox, GeofenceZone


class TestGeofenceGeometry(unittest.TestCase):
    def test_point_in_polygon_square(self):
        # Unit square: (0,0), (0,2), (2,2), (2,0)
        square = ((0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0))
        # Point inside
        self.assertTrue(point_in_polygon(1.0, 1.0, square))
        # Point outside
        self.assertFalse(point_in_polygon(3.0, 3.0, square))
        self.assertFalse(point_in_polygon(-1.0, 1.0, square))

    def test_point_in_polygon_degenerate(self):
        # Less than 3 points
        self.assertFalse(point_in_polygon(1.0, 1.0, ((0.0, 0.0), (1.0, 1.0))))

    def test_polygon_bounding_box(self):
        poly = ((10.0, 20.0), (15.0, 25.0), (12.0, 18.0))
        min_lat, min_lon, max_lat, max_lon = polygon_bounding_box(poly)
        self.assertEqual(min_lat, 10.0)
        self.assertEqual(max_lat, 15.0)
        self.assertEqual(min_lon, 18.0)
        self.assertEqual(max_lon, 25.0)


class TestGeofenceMonitor(unittest.TestCase):
    def setUp(self):
        self.monitor = GeofenceMonitor()

    def test_default_zones_loaded(self):
        zones = self.monitor.list_zones()
        zone_ids = [z.zone_id for z in zones]
        self.assertIn("MPA_PELAGOS_SANCTUARY", zone_ids)
        self.assertIn("TSS_STRAIT_OF_GIBRALTAR", zone_ids)
        self.assertIn("TSS_SINGAPORE_STRAIT", zone_ids)
        self.assertIn("TSS_DOVER_STRAIT", zone_ids)
        self.assertIn("MPA_GALAPAGOS_RESERVE", zone_ids)

    def test_dark_vessel_breach_in_pelagos_sanctuary(self):
        # Target inside Pelagos Sanctuary: 43.5N, 8.5E
        dark_target = {
            "index": 1,
            "latitude": 43.5,
            "longitude": 8.5,
            "is_dark_vessel": True,
            "vessel_class": "Cargo",
            "speed_knots": 12.0,
        }
        breaches = self.monitor.evaluate_target(dark_target, vessel_idx=1)
        self.assertTrue(len(breaches) > 0)
        b0 = breaches[0]
        self.assertEqual(b0.zone_id, "MPA_PELAGOS_SANCTUARY")
        self.assertEqual(b0.severity, "CRITICAL")
        self.assertEqual(b0.violation_type, "UNAUTHORIZED_DARK_ENTRY")
        self.assertIn("Dark non-reporting vessel", b0.narrative)

    def test_compliant_cargo_in_pelagos_sanctuary_no_breach(self):
        # AIS-broadcasting cargo vessel inside Pelagos Sanctuary: 43.5N, 8.5E
        coop_target = {
            "index": 2,
            "latitude": 43.5,
            "longitude": 8.5,
            "is_dark_vessel": False,
            "is_correlated": True,
            "vessel_class": "Cargo",
            "speed_knots": 12.0,
        }
        breaches = self.monitor.evaluate_target(coop_target, vessel_idx=2)
        self.assertEqual(len(breaches), 0)

    def test_fishing_vessel_in_galapagos_sanctuary(self):
        # Fishing vessel inside Galapagos Reserve (0.0, -90.5)
        fishing_target = {
            "index": 3,
            "latitude": 0.0,
            "longitude": -90.5,
            "is_dark_vessel": False,
            "vessel_class": "Fishing",
            "speed_knots": 4.5,
        }
        breaches = self.monitor.evaluate_target(fishing_target, vessel_idx=3)
        self.assertTrue(len(breaches) > 0)
        b = breaches[0]
        self.assertEqual(b.zone_id, "MPA_GALAPAGOS_RESERVE")
        self.assertEqual(b.violation_type, "ILLEGAL_FISHING_IN_MPA")
        self.assertEqual(b.severity, "CRITICAL")

    def test_stationary_obstruction_in_singapore_tss(self):
        # Vessel stopped in Singapore Strait TSS: 1.20N, 103.80E, speed 0.2 kn
        stopped_target = {
            "index": 4,
            "latitude": 1.20,
            "longitude": 103.80,
            "is_dark_vessel": False,
            "vessel_class": "Tanker",
            "speed_knots": 0.2,
        }
        breaches = self.monitor.evaluate_target(stopped_target, vessel_idx=4)
        tss_breaches = [b for b in breaches if b.violation_type == "TSS_LANE_OBSTRUCTION"]
        self.assertTrue(len(tss_breaches) > 0)
        self.assertEqual(tss_breaches[0].severity, "WARNING")

    def test_evaluate_detections_batch_enrichment(self):
        detections = [
            {
                "index": 0,
                "latitude": 43.5,
                "longitude": 8.5,
                "is_dark_vessel": True,
                "vessel_class": "Cargo",
            },
            {
                "index": 1,
                "latitude": 0.0,
                "longitude": 0.0,  # Middle of Atlantic, no zone
                "is_dark_vessel": True,
                "vessel_class": "Cargo",
            },
        ]
        result = self.monitor.evaluate_detections(detections)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_breaches"], 1)
        self.assertEqual(result["critical_breaches"], 1)

        # Check in-place enrichment on detection 0
        self.assertTrue(detections[0]["in_protected_area"])
        self.assertEqual(len(detections[0]["geofence_breaches"]), 1)
        self.assertIn("Pelagos Marine Mammal Sanctuary", detections[0]["protected_areas"])

        # Detection 1 has no breach
        self.assertFalse(detections[1]["in_protected_area"])
        self.assertEqual(len(detections[1]["geofence_breaches"]), 0)

        # Verify GeoJSON
        geojson = result["geojson"]
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertTrue(len(geojson["features"]) > 0)

    def test_custom_zone_registration(self):
        custom_zone = GeofenceZone(
            zone_id="CUSTOM_EEZ_ZONE",
            name="Custom Territorial Zone",
            zone_type="EEZ",
            polygon=((10.0, 10.0), (10.0, 12.0), (12.0, 12.0), (12.0, 10.0)),
            restrictions=("NO_DARK_VESSEL",),
        )
        self.monitor.register_zone(custom_zone)
        target = {
            "latitude": 11.0,
            "longitude": 11.0,
            "is_dark_vessel": True,
        }
        breaches = self.monitor.evaluate_target(target)
        self.assertEqual(len(breaches), 1)
        self.assertEqual(breaches[0].zone_id, "CUSTOM_EEZ_ZONE")
        self.assertEqual(breaches[0].severity, "HIGH")


if __name__ == "__main__":
    unittest.main()
