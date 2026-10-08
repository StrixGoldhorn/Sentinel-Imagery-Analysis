"""Tests for historical AIS traffic corridors and dark vessel density heatmap generation."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone

from sentinel_analysis.application.use_cases.generate_traffic_heatmap import GenerateHistoricalTrafficHeatmap
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.interfaces.web.application import create_app
from sentinel_analysis.bootstrap.config import Settings


class MockAISRepository:
    def __init__(self, coords=None):
        self._coords = coords or []

    def get_density_coordinates(self, bbox=None, time_range=None, limit=50000):
        results = []
        for lat, lon in self._coords:
            if bbox is not None:
                if not (bbox.min_latitude <= lat <= bbox.max_latitude and bbox.min_longitude <= lon <= bbox.max_longitude):
                    continue
            results.append((lat, lon))
            if len(results) >= limit:
                break
        return results


class MockScanRepository:
    def __init__(self, scans=None, scan_data=None):
        self._scans = scans or []
        self._scan_data = scan_data or {}
        self.output_root = tempfile.mkdtemp()

    def list(self):
        return self._scans

    def prepare(self, folder_name):
        scan_dir = Path(self.output_root) / folder_name
        scan_dir.mkdir(parents=True, exist_ok=True)
        if folder_name in self._scan_data:
            with open(scan_dir / "cv_results.json", "w", encoding="utf-8") as f:
                json.dump(self._scan_data[folder_name], f)
        return str(scan_dir)


class TestTrafficHeatmap(unittest.TestCase):
    def test_empty_heatmap(self):
        use_case = GenerateHistoricalTrafficHeatmap(MockAISRepository([]), MockScanRepository([]))
        result = use_case.execute()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary"]["total_ais_raw_points"], 0)
        self.assertEqual(result["summary"]["total_dark_vessels_detected"], 0)
        self.assertEqual(len(result["leaflet_heat_points"]), 0)
        self.assertEqual(len(result["geojson"]["features"]), 0)

    def test_ais_grid_binning_and_log_normalization(self):
        coords = [
            (1.281, 103.851),
            (1.282, 103.852),
            (1.283, 103.851),
            (1.281, 103.853),  # 4 in bin (1.28, 103.86)
            (1.401, 104.001),  # 1 in bin (1.40, 104.00)
        ]
        repo = MockAISRepository(coords)
        use_case = GenerateHistoricalTrafficHeatmap(ais_repository=repo, scan_repository=MockScanRepository([]))
        result = use_case.execute(cell_size_degrees=0.02)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary"]["total_ais_raw_points"], 5)
        self.assertEqual(result["summary"]["ais_cells"], 2)
        self.assertGreater(len(result["leaflet_heat_points"]), 0)
        self.assertGreater(len(result["ais_density_points"]), 0)

        # Check GeoJSON
        geojson = result["geojson"]
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertEqual(len(geojson["features"]), 2)
        counts = [f["properties"]["count"] for f in geojson["features"]]
        self.assertIn(4, counts)
        self.assertIn(1, counts)

    def test_dark_vessel_clustering(self):
        scan_id = "scan_test_01"
        scan = SimpleNamespace(
            id=scan_id,
            folder_name=scan_id,
            timestamp=datetime.now(timezone.utc),
            status="completed",
        )
        scan_data = {
            scan_id: {
                "bbox": {"min_lon": 103.0, "min_lat": 1.0, "max_lon": 104.0, "max_lat": 2.0},
                "detections": [
                    {"lat": 1.30, "lng": 103.80, "is_dark_vessel": True, "length": 85.0, "confidence": 0.92},
                    {"lat": 1.301, "lng": 103.801, "is_dark_vessel": True, "length": 120.0, "confidence": 0.88},
                    {"lat": 1.302, "lng": 103.802, "is_dark_vessel": True, "length": 110.0, "confidence": 0.95},
                    {"lat": 1.30, "lng": 103.80, "is_dark_vessel": False, "is_correlated": True},  # Not dark
                ],
            }
        }
        scan_repo = MockScanRepository(scans=[scan], scan_data=scan_data)
        use_case = GenerateHistoricalTrafficHeatmap(ais_repository=MockAISRepository([]), scan_repository=scan_repo)
        result = use_case.execute(include_ais=False, include_dark_vessels=True, cell_size_degrees=0.05)

        self.assertEqual(result["summary"]["total_dark_vessels_detected"], 3)
        self.assertEqual(len(result["dark_vessel_clusters"]), 1)
        cluster = result["dark_vessel_clusters"][0]
        self.assertEqual(cluster["count"], 3)
        self.assertGreater(cluster["avg_length_meters"], 100.0)

        # Check geojson feature properties
        dark_features = [f for f in result["geojson"]["features"] if f["properties"]["category"] == "dark_vessel"]
        self.assertEqual(len(dark_features), 1)
        self.assertEqual(dark_features[0]["properties"]["risk_level"], "CRITICAL")

    def test_bbox_filter(self):
        coords = [
            (1.20, 103.50),  # inside bbox
            (5.00, 110.00),  # outside bbox
        ]
        repo = MockAISRepository(coords)
        bbox = BoundingBox(103.0, 1.0, 104.0, 2.0)
        use_case = GenerateHistoricalTrafficHeatmap(ais_repository=repo, scan_repository=MockScanRepository([]))
        result = use_case.execute(bbox=bbox)

        self.assertEqual(result["summary"]["total_ais_raw_points"], 1)
        self.assertEqual(result["summary"]["ais_cells"], 1)

    def test_api_endpoint(self):
        project_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings = Settings(
                copernicus_username="test",
                copernicus_password="pwd",
                n2yo_api_key="test_n2yo",
                project_root=project_root,
                output_root=f"{tmp_dir}/output",
                cache_root=f"{tmp_dir}/cache",
                database_path=f"{tmp_dir}/test.db",
                debug=True,
            )
            from sentinel_analysis.bootstrap.container import ApplicationContainer
            container = ApplicationContainer(settings)
            app = create_app(settings, container, start_background_workers=False)
            client = app.test_client()

            # GET request
            res_get = client.get("/api/ais/heatmap?all_time=true")
            self.assertEqual(res_get.status_code, 200)
            data_get = res_get.get_json()
            self.assertEqual(data_get["status"], "success")
            self.assertIn("summary", data_get)
            self.assertIn("leaflet_heat_points", data_get)

            # POST request with bbox and params
            res_post = client.post(
                "/api/ais/heatmap",
                json={
                    "bbox": [103.5, 1.0, 104.5, 2.0],
                    "cell_size": 0.05,
                    "all_time": True,
                },
            )
            self.assertEqual(res_post.status_code, 200)
            data_post = res_post.get_json()
            self.assertEqual(data_post["status"], "success")


if __name__ == "__main__":
    unittest.main()
