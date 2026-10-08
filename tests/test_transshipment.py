"""Unit and integration tests for Ship-to-Ship (STS) transshipment and loitering anomaly detection."""

from datetime import datetime, timezone
import json
import tempfile
import unittest
from pathlib import Path

from sentinel_analysis.application.use_cases.detect_transshipment import (
    DetectTransshipmentAnomalies,
    haversine_distance_meters,
    initial_compass_bearing,
)
from sentinel_analysis.domain.entities import (
    Acquisition,
    BoundingBox,
    LoiteringAnomaly,
    Scan,
    TransshipmentRendezvous,
)


class DummyScanRepo:
    """Mock repository returning a prepared Scan entity or None."""

    def __init__(self, scan=None):
        self._scan = scan

    def get(self, folder_name: str):
        return self._scan

    def get_scan(self, folder_name: str):
        return self._scan


class TestTransshipmentGeometry(unittest.TestCase):
    def test_haversine_distance_known_points(self):
        # London (51.5074, -0.1278) to Paris (48.8566, 2.3522) is approx 343-344 km
        dist = haversine_distance_meters(51.5074, -0.1278, 48.8566, 2.3522)
        self.assertAlmostEqual(dist, 343500, delta=5000)

        # Same point distance is 0
        dist_zero = haversine_distance_meters(10.0, 20.0, 10.0, 20.0)
        self.assertEqual(dist_zero, 0.0)

        # Close proximity in Malacca Strait (approx 500m apart)
        lat1, lon1 = 2.5000, 101.5000
        lat2, lon2 = 2.5045, 101.5000  # ~0.0045 deg lat is ~500m
        dist_local = haversine_distance_meters(lat1, lon1, lat2, lon2)
        self.assertTrue(450 < dist_local < 550)

    def test_compass_bearing(self):
        # Due north
        bearing_n = initial_compass_bearing(10.0, 20.0, 11.0, 20.0)
        self.assertAlmostEqual(bearing_n, 0.0, delta=1.0)

        # Due east
        bearing_e = initial_compass_bearing(0.0, 20.0, 0.0, 21.0)
        self.assertAlmostEqual(bearing_e, 90.0, delta=1.0)


class TestDetectTransshipmentAnomalies(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.scan_dir = Path(self.temp_dir.name) / "scan_sts_test"
        self.scan_dir.mkdir(parents=True, exist_ok=True)

        self.bbox = BoundingBox(
            min_longitude=103.0,
            min_latitude=1.0,
            max_longitude=103.5,
            max_latitude=1.5,
        )
        self.acquisition = Acquisition(
            acquired_at=datetime(2026, 3, 1, 4, 0, 0, tzinfo=timezone.utc),
            satellite="Sentinel-1A",
            product_type="GRD",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_detections_json(self, detections: list[dict]):
        det_file = self.scan_dir / "detections.json"
        with open(det_file, "w", encoding="utf-8") as f:
            json.dump(detections, f)

    def _create_scan(self) -> Scan:
        img_path = self.scan_dir / "preview.png"
        img_path.write_bytes(b"fake image data")
        return Scan(
            folder_name="scan_sts_test",
            bbox=self.bbox,
            acquisition=self.acquisition,
            image_path=str(img_path),
            metadata={"source": "test"},
        )

    def test_dark_to_dark_rendezvous_detection(self):
        # Two dark vessels within 300m, both near zero speed
        detections = [
            {
                "index": 0,
                "label": "vessel",
                "confidence": 0.94,
                "is_dark_vessel": True,
                "lat": 1.2500,
                "lon": 103.2500,
                "wake_speed_knots": 0.5,
                "length": 120.0,
            },
            {
                "index": 1,
                "label": "vessel",
                "confidence": 0.91,
                "is_dark_vessel": True,
                "lat": 1.2520,
                "lon": 103.2515,  # ~270m away
                "wake_speed_knots": 0.8,
                "length": 145.0,
            },
        ]
        self._write_detections_json(detections)

        scan = self._create_scan()
        repo = DummyScanRepo(scan)
        use_case = DetectTransshipmentAnomalies(scan_repository=repo)

        result = use_case.execute(
            "scan_sts_test",
            max_rendezvous_distance_meters=1000.0,
            max_rendezvous_speed_knots=2.5,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rendezvous_count"], 1)
        self.assertEqual(result["overall_threat_level"], "CRITICAL")
        
        rdv = result["rendezvous_events"][0]
        self.assertEqual(rdv["risk_level"], "CRITICAL")
        self.assertTrue(rdv["vessel_a_is_dark"])
        self.assertTrue(rdv["vessel_b_is_dark"])
        self.assertTrue(200 < rdv["distance_meters"] < 350)

        # Check GeoJSON
        geojson = result["geojson"]
        self.assertEqual(geojson["type"], "FeatureCollection")
        line_features = [f for f in geojson["features"] if f["geometry"]["type"] == "LineString"]
        self.assertEqual(len(line_features), 1)
        self.assertEqual(line_features[0]["properties"]["risk_level"], "CRITICAL")

    def test_dark_to_compliant_rendezvous_detection(self):
        detections = [
            {
                "index": 0,
                "label": "vessel",
                "is_dark_vessel": True,
                "lat": 1.2000,
                "lon": 103.2000,
                "wake_speed_knots": 1.0,
                "length": 80.0,
            },
            {
                "index": 1,
                "label": "vessel",
                "is_dark_vessel": False,
                "lat": 1.2030,
                "lon": 103.2020,  # ~400m
                "correlated_ais": {"mmsi": 123456789, "speed": 1.2},
                "length": 110.0,
            },
        ]
        self._write_detections_json(detections)

        scan = self._create_scan()
        repo = DummyScanRepo(scan)
        use_case = DetectTransshipmentAnomalies(scan_repository=repo)

        result = use_case.execute(
            "scan_sts_test",
            max_rendezvous_distance_meters=800.0,
        )

        self.assertEqual(result["rendezvous_count"], 1)
        self.assertEqual(result["rendezvous_events"][0]["risk_level"], "HIGH")

    def test_far_apart_vessels_not_rendezvous(self):
        detections = [
            {
                "index": 0,
                "is_dark_vessel": True,
                "lat": 1.1000,
                "lon": 103.1000,
            },
            {
                "index": 1,
                "is_dark_vessel": True,
                "lat": 1.4500,
                "lon": 103.4500,  # Tens of kilometers apart
            },
        ]
        self._write_detections_json(detections)

        scan = self._create_scan()
        repo = DummyScanRepo(scan)
        use_case = DetectTransshipmentAnomalies(scan_repository=repo)

        result = use_case.execute(
            "scan_sts_test",
            max_rendezvous_distance_meters=1500.0,
        )

        self.assertEqual(result["rendezvous_count"], 0)

    def test_loitering_anomaly_detection(self):
        detections = [
            {
                "index": 0,
                "label": "vessel",
                "is_dark_vessel": True,
                "lat": 1.3000,
                "lon": 103.3000,
                "wake_speed_knots": 0.4,
                "length": 120.0,
            }
        ]
        self._write_detections_json(detections)

        scan = self._create_scan()
        repo = DummyScanRepo(scan)
        use_case = DetectTransshipmentAnomalies(scan_repository=repo)

        result = use_case.execute(
            "scan_sts_test",
            min_loiter_length_meters=25.0,
        )

        self.assertEqual(result["loitering_count"], 1)
        loiter = result["loitering_events"][0]
        self.assertTrue(loiter["is_dark"])
        self.assertTrue(loiter["risk_score"] >= 75.0)

        # Ensure GeoJSON includes loitering anomaly point
        loitering_features = [
            f for f in result["geojson"]["features"]
            if f["properties"].get("feature_type") == "LOITERING_ANOMALY"
        ]
        self.assertEqual(len(loitering_features), 1)


if __name__ == "__main__":
    unittest.main()
