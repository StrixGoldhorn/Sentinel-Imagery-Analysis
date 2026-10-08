"""Unit tests for SAR detection tagging along traced and predicted vessel routes."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from sentinel_analysis.application.use_cases.tag_route_detections import (
    TagRouteDetections,
    build_traced_and_predicted_route,
    point_to_line_segment_distance,
    point_to_route_distance,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.interfaces.web.application import create_app


class TestPointToSegmentGeometry(unittest.TestCase):
    def test_point_on_segment(self):
        # East-west segment along equator
        dist, c_lat, c_lon, t = point_to_line_segment_distance(
            lat=0.0, lon=1.5,
            lat1=0.0, lon1=1.0,
            lat2=0.0, lon2=2.0,
        )
        self.assertAlmostEqual(dist, 0.0, delta=1.0)
        self.assertAlmostEqual(c_lat, 0.0, delta=1e-5)
        self.assertAlmostEqual(c_lon, 1.5, delta=1e-5)
        self.assertAlmostEqual(t, 0.5, delta=1e-5)

    def test_point_orthogonal_to_segment(self):
        # Point 0.001 deg North of segment midpoint
        dist, c_lat, c_lon, t = point_to_line_segment_distance(
            lat=0.001, lon=1.5,
            lat1=0.0, lon1=1.0,
            lat2=0.0, lon2=2.0,
        )
        # 0.001 deg lat is approximately 111.32 meters
        self.assertAlmostEqual(dist, 111.32, delta=5.0)
        self.assertAlmostEqual(c_lat, 0.0, delta=1e-5)
        self.assertAlmostEqual(c_lon, 1.5, delta=1e-5)
        self.assertAlmostEqual(t, 0.5, delta=1e-5)

    def test_point_clamped_to_start_vertex(self):
        # Point west of start vertex
        dist, c_lat, c_lon, t = point_to_line_segment_distance(
            lat=0.0, lon=0.5,
            lat1=0.0, lon1=1.0,
            lat2=0.0, lon2=2.0,
        )
        self.assertEqual(t, 0.0)
        self.assertAlmostEqual(c_lat, 0.0, delta=1e-5)
        self.assertAlmostEqual(c_lon, 1.0, delta=1e-5)
        # 0.5 deg longitude ~ 55,660m
        self.assertGreater(dist, 50000)

    def test_point_clamped_to_end_vertex(self):
        # Point east of end vertex
        dist, c_lat, c_lon, t = point_to_line_segment_distance(
            lat=0.0, lon=2.5,
            lat1=0.0, lon1=1.0,
            lat2=0.0, lon2=2.0,
        )
        self.assertEqual(t, 1.0)
        self.assertAlmostEqual(c_lat, 0.0, delta=1e-5)
        self.assertAlmostEqual(c_lon, 2.0, delta=1e-5)

    def test_degenerate_zero_length_segment(self):
        dist, c_lat, c_lon, t = point_to_line_segment_distance(
            lat=0.001, lon=1.0,
            lat1=0.0, lon1=1.0,
            lat2=0.0, lon2=1.0,
        )
        self.assertEqual(t, 0.0)
        self.assertAlmostEqual(c_lat, 0.0, delta=1e-5)
        self.assertAlmostEqual(c_lon, 1.0, delta=1e-5)
        self.assertAlmostEqual(dist, 111.32, delta=5.0)


class TestPointToRouteDistance(unittest.TestCase):
    def test_empty_route(self):
        dist, lat, lon, seg, t = point_to_route_distance(1.0, 103.0, [])
        self.assertEqual(dist, float("inf"))
        self.assertEqual(seg, -1)

    def test_single_point_route(self):
        dist, lat, lon, seg, t = point_to_route_distance(1.0, 103.0, [(1.0, 103.0)])
        self.assertAlmostEqual(dist, 0.0, delta=1.0)
        self.assertEqual(seg, 0)

    def test_multi_segment_route(self):
        route = [
            (1.0, 103.0),
            (1.0, 103.5),
            (1.5, 103.5),
        ]
        # Point near second segment (vertical segment)
        dist, c_lat, c_lon, seg, t = point_to_route_distance(1.25, 103.501, route)
        self.assertEqual(seg, 1)
        self.assertAlmostEqual(c_lat, 1.25, delta=1e-3)
        self.assertAlmostEqual(c_lon, 103.5, delta=1e-3)
        self.assertLess(dist, 200.0)


class TestBuildTracedAndPredictedRoute(unittest.TestCase):
    def test_build_route_chronological_and_extrapolated(self):
        t0 = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 8, 1, 10, 30, 0, tzinfo=timezone.utc)
        points = [
            {"latitude": 1.0, "longitude": 103.0, "timestamp": t0.isoformat(), "speed": 10.0, "heading": 90.0},
            {"latitude": 1.0, "longitude": 103.1, "timestamp": t1.isoformat(), "speed": 10.0, "heading": 90.0},
        ]
        pass_epoch = datetime(2026, 8, 1, 11, 0, 0, tzinfo=timezone.utc)
        waypoints = build_traced_and_predicted_route(
            points,
            target_time=pass_epoch,
            predict_forward_seconds=3600.0,
        )
        self.assertGreaterEqual(len(waypoints), 2)
        # First points should be traced
        self.assertFalse(waypoints[0]["is_predicted"])
        self.assertFalse(waypoints[1]["is_predicted"])
        # Later points should be predicted
        has_pred = any(w["is_predicted"] for w in waypoints)
        self.assertTrue(has_pred)


class TestTagRouteDetectionsUseCase(unittest.TestCase):
    def setUp(self):
        self.use_case = TagRouteDetections()
        self.vessels = [
            {
                "mmsi": "111222333",
                "name": "MV PACIFIC VOYAGER",
                "ship_type": "Cargo",
                "route": [
                    {"latitude": 1.2000, "longitude": 103.8000, "is_predicted": False},
                    {"latitude": 1.2000, "longitude": 103.8500, "is_predicted": False},
                    {"latitude": 1.2000, "longitude": 103.9000, "is_predicted": True},
                ],
            },
            {
                "mmsi": "999888777",
                "name": "MT OCEAN TITAN",
                "ship_type": "Tanker",
                "route": [
                    {"latitude": 1.1000, "longitude": 103.8000, "is_predicted": False},
                    {"latitude": 1.1000, "longitude": 103.8500, "is_predicted": False},
                ],
            },
        ]

    def test_detection_inside_buffer_is_tagged(self):
        # Point 100m North of MV PACIFIC VOYAGER's route (0.0009 deg lat ~ 100m)
        detections = [
            {"index": 0, "lat": 1.2009, "lon": 103.8250, "confidence": 0.95, "length": 180.0},
        ]
        res = self.use_case.execute(detections, self.vessels, buffer_meters=250.0)

        self.assertEqual(res["tagged_detections_count"], 1)
        self.assertEqual(res["vessels_with_tags_count"], 1)

        det = res["detections"][0]
        self.assertTrue(det["tagged_to_vessel"])
        tag = det["route_tagged_vessel"]
        self.assertEqual(tag["mmsi"], "111222333")
        self.assertEqual(tag["vessel_name"], "MV PACIFIC VOYAGER")
        self.assertLess(tag["distance_to_route_m"], 120.0)
        self.assertEqual(tag["buffer_meters"], 250.0)

        # Check vessel has record of tagged detection
        vessel = next(v for v in res["vessels"] if v["mmsi"] == "111222333")
        self.assertEqual(len(vessel["tagged_detections"]), 1)
        self.assertEqual(vessel["tagged_detections"][0]["detection_index"], 0)

    def test_detection_outside_buffer_is_not_tagged(self):
        # Point 600m away with buffer 250m
        detections = [
            {"index": 0, "lat": 1.2055, "lon": 103.8250, "confidence": 0.88, "length": 120.0},
        ]
        res = self.use_case.execute(detections, self.vessels, buffer_meters=250.0)
        self.assertEqual(res["tagged_detections_count"], 0)
        self.assertEqual(res["vessels_with_tags_count"], 0)
        self.assertFalse(res["detections"][0]["tagged_to_vessel"])
        self.assertIsNone(res["detections"][0]["route_tagged_vessel"])

    def test_dynamic_user_buffer_expansion(self):
        # Point 600m away: fails with 250m buffer, passes with 800m buffer
        detections = [
            {"index": 0, "lat": 1.2055, "lon": 103.8250, "confidence": 0.88, "length": 120.0},
        ]
        res_small = self.use_case.execute(detections, self.vessels, buffer_meters=250.0)
        self.assertEqual(res_small["tagged_detections_count"], 0)

        res_large = self.use_case.execute(detections, self.vessels, buffer_meters=800.0)
        self.assertEqual(res_large["tagged_detections_count"], 1)
        self.assertTrue(res_large["detections"][0]["tagged_to_vessel"])

    def test_closest_vessel_selected_when_multiple_routes_in_buffer(self):
        # Point between the two routes but closer to Pacific Voyager (lat 1.2) than Ocean Titan (lat 1.1)
        detections = [
            {"index": 0, "lat": 1.1990, "lon": 103.8250, "confidence": 0.91},
        ]
        res = self.use_case.execute(detections, self.vessels, buffer_meters=15000.0)
        self.assertEqual(res["tagged_detections_count"], 1)
        self.assertEqual(res["detections"][0]["route_tagged_vessel"]["mmsi"], "111222333")

    def test_predicted_segment_tagging_flag(self):
        # Point near predicted segment (lon 103.88)
        detections = [
            {"index": 0, "lat": 1.2002, "lon": 103.8800, "confidence": 0.94},
        ]
        res = self.use_case.execute(detections, self.vessels, buffer_meters=300.0)
        self.assertEqual(res["tagged_detections_count"], 1)
        tag = res["detections"][0]["route_tagged_vessel"]
        self.assertTrue(tag["is_predicted_segment"])


class TestRouteTaggingWebEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name)
        scan_dir = self.output_root / "test_scan_01"
        scan_dir.mkdir(parents=True, exist_ok=True)
        img_path = scan_dir / "sar_image.png"
        img_path.write_bytes(b"dummy")

        det_data = {
            "timestamp": "2026-08-01T12:00:00Z",
            "ship_count": 1,
            "detections": [
                {
                    "index": 0,
                    "lat": 1.2005,
                    "lon": 103.8200,
                    "confidence": 0.92,
                    "length": 150.0,
                    "correlation_status": "uncorrelated",
                }
            ],
        }
        (scan_dir / "detection_results.json").write_text(json.dumps(det_data), encoding="utf-8")

        self.mock_scan = Scan(
            folder_name="test_scan_01",
            image_path=str(img_path),
            bbox=BoundingBox(103.7, 1.1, 103.9, 1.3),
            acquisition=Acquisition(datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc), "Sentinel-1", "GRD"),
            metadata={"acquired_at": "2026-08-01T12:00:00Z"},
        )

        settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=self.output_root / "test.db",
            output_root=self.output_root,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
        )

        self.container = MagicMock()
        self.container.settings = settings
        self.container.get_scan.execute.return_value = self.mock_scan
        self.container.tag_route_detections = TagRouteDetections()

        # Mock AIS repository returning vessel fixes
        self.container.ais_repository.get_vessel_positions.return_value = [
            {
                "mmsi": "333444555",
                "name": "TEST CARRIER",
                "type": "Cargo",
                "latitude": 1.2000,
                "longitude": 103.8000,
                "timestamp": "2026-08-01T11:45:00Z",
                "speed": 12.0,
                "heading": 90.0,
            },
            {
                "mmsi": "333444555",
                "name": "TEST CARRIER",
                "type": "Cargo",
                "latitude": 1.2000,
                "longitude": 103.8500,
                "timestamp": "2026-08-01T12:15:00Z",
                "speed": 12.0,
                "heading": 90.0,
            },
        ]
        self.container.ais_repository.get_vessel_history.return_value = self.container.ais_repository.get_vessel_positions.return_value
        self.container.get_vessel_details.execute.return_value = {
            "vessel_id": 1,
            "mmsi": "333444555",
            "name": "TEST CARRIER",
            "ship_type": "Cargo",
        }

        self.app = create_app(settings, self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_scan_ais_tracks_with_buffer_tagging(self):
        resp = self.client.get("/api/scan/test_scan_01/ais_tracks?buffer_meters=500")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("tagged_detections_count", data)
        self.assertIn("vessels_with_tags_count", data)
        self.assertEqual(data["buffer_meters"], 500.0)
        self.assertEqual(data["tagged_detections_count"], 1)

        # Ensure detection has tag info
        det = data["detections"][0]
        self.assertTrue(det["tagged_to_vessel"])
        self.assertEqual(det["route_tagged_vessel"]["mmsi"], "333444555")

    def test_post_scan_tag_route_custom_buffer(self):
        # 10m buffer should exclude detection ~55m away
        resp_tight = self.client.post("/api/scan/test_scan_01/tag_route", json={"buffer_meters": 10.0})
        self.assertEqual(resp_tight.status_code, 200)
        data_tight = resp_tight.get_json()
        self.assertEqual(data_tight["tagged_detections_count"], 0)

        # 200m buffer should include detection ~55m away
        resp_wide = self.client.post("/api/scan/test_scan_01/tag_route", json={"buffer_meters": 200.0})
        self.assertEqual(resp_wide.status_code, 200)
        data_wide = resp_wide.get_json()
        self.assertEqual(data_wide["tagged_detections_count"], 1)
        self.assertTrue(data_wide["detections"][0]["tagged_to_vessel"])

    def test_get_vessel_route_tags_endpoint(self):
        resp = self.client.get("/api/ais/vessels/1/route_tags?scan_folder=test_scan_01&buffer_meters=400")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["vessel_id"], 1)
        self.assertEqual(data["tagged_detections_count"], 1)
        self.assertEqual(len(data["tagged_detections"]), 1)


if __name__ == "__main__":
    unittest.main()
