"""Unit tests for Copernicus Sentinel-2 Optical Cross-Validation and NDWI Analysis."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.application.use_cases.cross_validate_optical import CrossValidateOptical
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.infrastructure.satellite.sentinel2_client import (
    OpticalValidationResult,
    Sentinel2Client,
    Sentinel2Scene,
)


class TestSentinel2Client(unittest.TestCase):
    def setUp(self):
        self.client = Sentinel2Client()

    def test_compute_ndwi(self):
        # Open water: High Green reflectance, very low NIR absorption
        green = np.full((10, 10), 80, dtype=np.uint8)
        nir = np.full((10, 10), 10, dtype=np.uint8)
        ndwi_water = Sentinel2Client.compute_ndwi(green, nir)
        self.assertTrue(np.all(ndwi_water > 0.5))

        # Land / Vegetation: Lower Green, High NIR
        green_land = np.full((10, 10), 40, dtype=np.uint8)
        nir_land = np.full((10, 10), 120, dtype=np.uint8)
        ndwi_land = Sentinel2Client.compute_ndwi(green_land, nir_land)
        self.assertTrue(np.all(ndwi_land < 0.0))

    def test_search_concurrent_optical(self):
        mock_http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "features": [
                {
                    "id": "S2B_MSIL2A_20260410T023539",
                    "bbox": [103.5, 1.1, 104.2, 1.5],
                    "properties": {
                        "datetime": "2026-04-10T03:15:00Z",
                        "eo:cloud_cover": 12.5,
                        "platform": "Sentinel-2B",
                    },
                },
                {
                    "id": "S2A_MSIL2A_20260409T024521",
                    "bbox": [103.5, 1.1, 104.2, 1.5],
                    "properties": {
                        "datetime": "2026-04-09T03:00:00Z",
                        "eo:cloud_cover": 85.0,  # Should be filtered out if max_cloud_cover=50
                        "platform": "Sentinel-2A",
                    },
                },
                {
                    "id": "S2A_MSIL2A_20260411T024521",
                    "bbox": [103.5, 1.1, 104.2, 1.5],
                    "properties": {
                        "datetime": "2026-04-11T03:30:00Z",
                        "eo:cloud_cover": 25.0,
                        "platform": "Sentinel-2A",
                    },
                },
            ]
        }
        mock_http.get.return_value = mock_resp

        client = Sentinel2Client(http_client=mock_http)
        bbox = BoundingBox(min_longitude=103.5, min_latitude=1.1, max_longitude=104.2, max_latitude=1.5)
        sar_time = datetime(2026, 4, 10, 3, 0, 0, tzinfo=timezone.utc)

        scenes = client.search_concurrent_optical(
            bbox=bbox,
            target_datetime=sar_time,
            time_window_hours=48.0,
            max_cloud_cover=50.0,
        )

        self.assertEqual(len(scenes), 2)
        # S2B scene (dt delta ~0.25h) should rank before S2A (dt delta ~24.5h)
        self.assertEqual(scenes[0].scene_id, "S2B_MSIL2A_20260410T023539")
        self.assertAlmostEqual(scenes[0].cloud_cover, 12.5)
        self.assertLess(scenes[0].time_delta_hours, 1.0)

    def test_search_concurrent_optical_http_error(self):
        mock_http = MagicMock()
        mock_http.get.side_effect = Exception("Network timeout")

        client = Sentinel2Client(http_client=mock_http)
        bbox = BoundingBox(min_longitude=103.5, min_latitude=1.1, max_longitude=104.2, max_latitude=1.5)
        scenes = client.search_concurrent_optical(
            bbox=bbox,
            target_datetime=datetime.now(timezone.utc),
        )
        self.assertEqual(scenes, [])

    def test_validate_detection_optical_chips_confirmed(self):
        # Create a water chip (high NDWI) with a vessel target in center (low NDWI)
        # Water: Green=100, NIR=20 -> NDWI=(100-20)/(120) = +0.67
        # Vessel: Green=60, NIR=80 -> NDWI=(60-80)/(140) = -0.14
        green = np.full((32, 32), 100, dtype=np.uint8)
        nir = np.full((32, 32), 20, dtype=np.uint8)
        # Put ship in center (radius 3)
        green[14:18, 14:18] = 60
        nir[14:18, 14:18] = 80

        scene = Sentinel2Scene(
            scene_id="S2A_TEST",
            acquired_at=datetime.now(timezone.utc),
            cloud_cover=10.0,
            platform="Sentinel-2A",
            bbox=(103.5, 1.1, 104.2, 1.5),
            time_delta_hours=2.5,
        )

        res = self.client.validate_detection_optical(
            detection_idx=0,
            det={"x": 100, "y": 100},
            scene=scene,
            green_chip=green,
            nir_chip=nir,
        )

        self.assertEqual(res.status, "CONFIRMED_VESSEL")
        self.assertTrue(res.optical_confirmed)
        self.assertGreater(res.contrast_ndwi, 0.2)
        self.assertGreater(res.optical_confidence, 0.7)

    def test_validate_detection_optical_land_false_alarm(self):
        # Dry land/reef: both target and background have low NDWI (e.g. Green=50, NIR=80 -> NDWI < 0)
        green = np.full((32, 32), 50, dtype=np.uint8)
        nir = np.full((32, 32), 80, dtype=np.uint8)

        scene = Sentinel2Scene(
            scene_id="S2A_TEST_LAND",
            acquired_at=datetime.now(timezone.utc),
            cloud_cover=5.0,
            platform="Sentinel-2A",
            bbox=(103.5, 1.1, 104.2, 1.5),
            time_delta_hours=1.0,
        )

        res = self.client.validate_detection_optical(
            detection_idx=1,
            det={"x": 50, "y": 50},
            scene=scene,
            green_chip=green,
            nir_chip=nir,
        )

        self.assertEqual(res.status, "LAND_FALSE_ALARM")
        self.assertFalse(res.optical_confirmed)

    def test_validate_detection_optical_cloud_obscuration(self):
        # Heavy cloud: bright white RGB and saturated NIR
        green = np.full((32, 32), 240, dtype=np.uint8)
        nir = np.full((32, 32), 230, dtype=np.uint8)
        rgb = np.full((32, 32, 3), 240, dtype=np.uint8)

        scene = Sentinel2Scene(
            scene_id="S2A_TEST_CLOUD",
            acquired_at=datetime.now(timezone.utc),
            cloud_cover=40.0,
            platform="Sentinel-2A",
            bbox=(103.5, 1.1, 104.2, 1.5),
            time_delta_hours=1.0,
        )

        res = self.client.validate_detection_optical(
            detection_idx=2,
            det={"x": 60, "y": 60},
            scene=scene,
            green_chip=green,
            nir_chip=nir,
            rgb_chip=rgb,
        )

        self.assertEqual(res.status, "CLOUD_OBSCURED")
        self.assertFalse(res.optical_confirmed)

    def test_validate_detection_metadata_fallback(self):
        scene_clear = Sentinel2Scene(
            scene_id="S2A_CLEAR",
            acquired_at=datetime.now(timezone.utc),
            cloud_cover=12.0,
            platform="Sentinel-2A",
            bbox=(103.5, 1.1, 104.2, 1.5),
            time_delta_hours=3.0,
        )
        res_clear = self.client.validate_detection_optical(0, {}, scene=scene_clear)
        self.assertEqual(res_clear.status, "CONFIRMED_VESSEL")
        self.assertTrue(res_clear.optical_confirmed)

        scene_cloudy = Sentinel2Scene(
            scene_id="S2A_CLOUDY",
            acquired_at=datetime.now(timezone.utc),
            cloud_cover=85.0,
            platform="Sentinel-2A",
            bbox=(103.5, 1.1, 104.2, 1.5),
            time_delta_hours=3.0,
        )
        res_cloudy = self.client.validate_detection_optical(0, {}, scene=scene_cloudy)
        self.assertEqual(res_cloudy.status, "CLOUD_OBSCURED")

        res_none = self.client.validate_detection_optical(0, {}, scene=None)
        self.assertEqual(res_none.status, "NO_CONCURRENT_OPTICAL")


class TestCrossValidateOpticalUseCase(unittest.TestCase):
    def test_execute_use_case_updates_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scan_dir = Path(tmp_dir) / "Scan_Test_2026"
            scan_dir.mkdir(parents=True, exist_ok=True)
            img_path = scan_dir / "Scan_Test_2026.png"
            img_path.write_bytes(b"dummy_png")

            # Create mock detection_results.json
            det_results = {
                "scan_id": "Scan_Test_2026",
                "detections": [
                    {"x": 100, "y": 150, "lat": 1.25, "lon": 103.85, "length_meters": 120.0},
                    {"x": 300, "y": 450, "lat": 1.30, "lon": 103.90, "length_meters": 75.0},
                ],
            }
            (scan_dir / "detection_results.json").write_text(json.dumps(det_results), encoding="utf-8")

            # Create mock detections.geojson
            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [103.85, 1.25]},
                        "properties": {"detection_index": 0},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [103.90, 1.30]},
                        "properties": {"detection_index": 1},
                    },
                ],
            }
            (scan_dir / "detections.geojson").write_text(json.dumps(geojson_data), encoding="utf-8")

            scan = Scan(
                folder_name="Scan_Test_2026",
                image_path=str(img_path),
                acquisition=Acquisition(
                    acquired_at=datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc),
                    satellite="Sentinel-1A",
                    product_type="GRD",
                ),
                bbox=BoundingBox(min_longitude=103.5, min_latitude=1.1, max_longitude=104.2, max_latitude=1.5),
            )

            mock_repo = MagicMock(spec=ScanRepository)
            mock_repo.get.return_value = scan

            mock_client = MagicMock(spec=Sentinel2Client)
            mock_scene = Sentinel2Scene(
                scene_id="S2B_20260410_OPTICAL",
                acquired_at=datetime(2026, 4, 10, 11, 30, 0, tzinfo=timezone.utc),
                cloud_cover=14.0,
                platform="Sentinel-2B",
                bbox=(103.5, 1.1, 104.2, 1.5),
                time_delta_hours=1.5,
            )
            mock_client.search_concurrent_optical.return_value = [mock_scene]
            mock_client.validate_detection_optical.side_effect = [
                OpticalValidationResult(
                    detection_index=0,
                    status="CONFIRMED_VESSEL",
                    optical_confirmed=True,
                    time_delta_hours=1.5,
                    cloud_cover=14.0,
                    optical_confidence=0.85,
                    scene_id="S2B_20260410_OPTICAL",
                    details="Corroborated by optical NDWI",
                ),
                OpticalValidationResult(
                    detection_index=1,
                    status="LAND_FALSE_ALARM",
                    optical_confirmed=False,
                    time_delta_hours=1.5,
                    cloud_cover=14.0,
                    optical_confidence=0.90,
                    scene_id="S2B_20260410_OPTICAL",
                    details="Nearshore reef false alarm",
                ),
            ]

            use_case = CrossValidateOptical(mock_repo, optical_validator=mock_client)
            result = use_case.execute("Scan_Test_2026")

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["confirmed_count"], 1)
            self.assertEqual(result["land_false_alarm_count"], 1)
            self.assertEqual(len(result["results"]), 2)

            # Check that files were written
            self.assertTrue((scan_dir / "optical_validation.json").exists())
            opt_json = json.loads((scan_dir / "optical_validation.json").read_text(encoding="utf-8"))
            self.assertEqual(opt_json["confirmed_count"], 1)

            updated_det_json = json.loads((scan_dir / "detection_results.json").read_text(encoding="utf-8"))
            self.assertEqual(updated_det_json["detections"][0]["optical_status"], "CONFIRMED_VESSEL")
            self.assertEqual(updated_det_json["detections"][1]["optical_status"], "LAND_FALSE_ALARM")

            updated_geojson = json.loads((scan_dir / "detections.geojson").read_text(encoding="utf-8"))
            self.assertTrue(updated_geojson["features"][0]["properties"]["optical_confirmed"])
            self.assertFalse(updated_geojson["features"][1]["properties"]["optical_confirmed"])


if __name__ == "__main__":
    unittest.main()
