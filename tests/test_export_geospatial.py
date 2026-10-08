"""Unit and integration tests for GeoTIFF and STAC export functionality."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from sentinel_analysis.application.use_cases.export_geospatial import ExportGeospatial
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.infrastructure.imagery.geotiff import PillowGeoTIFFWriter
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.interfaces.web.application import create_app


class TestExportGeospatial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.tmp_dir.name) / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.scan_repo = FilesystemScanRepository(self.output_root)

        # Create dummy scan with an image
        self.scan_folder = "test_geospatial_scan"
        scan_dir = self.output_root / self.scan_folder / "images"
        scan_dir.mkdir(parents=True, exist_ok=True)
        self.img_path = scan_dir / f"{self.scan_folder}_stitched.png"

        # Write 200x100 grayscale image
        img_arr = np.full((100, 200), 128, dtype=np.uint8)
        Image.fromarray(img_arr).save(self.img_path)

        self.bbox = BoundingBox(12.0, 42.0, 14.0, 43.0)
        self.acq = Acquisition(
            datetime(2026, 9, 15, 10, 30, tzinfo=timezone.utc),
            "SENTINEL-1A",
            "sentinel-1-grd",
            product_id="S1A_TEST_GRD",
            polarizations=("VV", "VH"),
            orbit_direction="ASCENDING",
            relative_orbit=42,
        )

        metadata = {
            "aoi_name": "Tyrrhenian Sea",
            "detections": [
                {
                    "pixel_x": 50,
                    "pixel_y": 30,
                    "latitude": 42.7,
                    "longitude": 12.5,
                    "length_m": 85.0,
                    "width_m": 16.0,
                    "confidence": 0.88,
                    "is_dark": True,
                },
                {
                    "pixel_x": 120,
                    "pixel_y": 70,
                    "latitude": 42.3,
                    "longitude": 13.2,
                    "length_m": 220.0,
                    "width_m": 32.0,
                    "confidence": 0.95,
                    "is_dark": False,
                },
            ],
        }

        self.scan = Scan(self.scan_folder, self.bbox, self.acq, str(self.img_path), metadata)
        self.scan_repo.save(self.scan)

        self.exporter = ExportGeospatial(self.scan_repo, PillowGeoTIFFWriter())

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_export_geotiff(self):
        tif_path = self.exporter.export_geotiff(self.scan_folder)
        self.assertTrue(tif_path.is_file())
        self.assertTrue(tif_path.name.endswith(".tif"))

        # Verify GeoTIFF tags
        with Image.open(tif_path) as img:
            self.assertEqual(img.size, (200, 100))
            self.assertIn(33550, img.tag_v2)  # ModelPixelScaleTag
            self.assertIn(33922, img.tag_v2)  # ModelTiepointTag
            self.assertIn(34735, img.tag_v2)  # GeoKeyDirectoryTag

            pixel_scale = img.tag_v2[33550]
            # scale_x = (14.0 - 12.0) / 200 = 0.01
            # scale_y = (43.0 - 42.0) / 100 = 0.01
            self.assertAlmostEqual(pixel_scale[0], 0.01)
            self.assertAlmostEqual(pixel_scale[1], 0.01)

            tie_point = img.tag_v2[33922]
            # (0.0, 0.0, 0.0, min_lon=12.0, max_lat=43.0, 0.0)
            self.assertAlmostEqual(tie_point[3], 12.0)
            self.assertAlmostEqual(tie_point[4], 43.0)

            geo_keys = img.tag_v2[34735]
            # Must reference EPSG:4326
            self.assertIn(4326, geo_keys)

    def test_export_stac_item(self):
        stac = self.exporter.export_stac_item(self.scan_folder, base_url="http://localhost:5000")
        self.assertEqual(stac["type"], "Feature")
        self.assertEqual(stac["stac_version"], "1.0.0")
        self.assertEqual(stac["id"], self.scan_folder)
        self.assertEqual(stac["bbox"], [12.0, 42.0, 14.0, 43.0])
        self.assertEqual(stac["geometry"]["type"], "Polygon")
        self.assertEqual(stac["properties"]["platform"], "sentinel-1a")
        self.assertEqual(stac["properties"]["constellation"], "sentinel-1")
        self.assertIn("VV", stac["properties"]["sar:polarizations"])
        self.assertIn("VH", stac["properties"]["sar:polarizations"])

        # Check assets
        self.assertIn("geotiff", stac["assets"])
        self.assertIn("detections", stac["assets"])
        self.assertIn("thumbnail", stac["assets"])
        self.assertTrue(stac["assets"]["geotiff"]["href"].endswith("/export/geotiff"))
        self.assertTrue(stac["assets"]["detections"]["href"].endswith("/export/geojson"))

    def test_export_geojson(self):
        fc = self.exporter.export_geojson(self.scan_folder)
        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertEqual(len(fc["features"]), 2)

        feat0 = fc["features"][0]
        self.assertEqual(feat0["geometry"]["type"], "Point")
        self.assertAlmostEqual(feat0["geometry"]["coordinates"][0], 12.5)
        self.assertAlmostEqual(feat0["geometry"]["coordinates"][1], 42.7)
        self.assertEqual(feat0["properties"]["length_m"], 85.0)
        self.assertTrue(feat0["properties"]["is_dark"])

        feat1 = fc["features"][1]
        self.assertEqual(feat1["properties"]["length_m"], 220.0)
        self.assertFalse(feat1["properties"]["is_dark"])


class TestExportGeospatialWebAPI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp_dir.name) / "test_api.db"
        output_root = Path(self.tmp_dir.name) / "output"
        output_root.mkdir(parents=True, exist_ok=True)

        scan_repo = FilesystemScanRepository(output_root)
        self.scan_folder = "api_scan_test"
        scan_dir = scan_repo.prepare(self.scan_folder)
        img_path = scan_dir / "images" / f"{self.scan_folder}_stitched.png"
        Image.fromarray(np.full((50, 50), 200, dtype=np.uint8)).save(img_path)

        bbox = BoundingBox(10.0, 50.0, 11.0, 51.0)
        acq = Acquisition(
            datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            "SENTINEL-1A",
            "sentinel-1-grd",
            product_id="S1_TEST",
            polarizations=("VV",),
        )
        metadata = {
            "detections": [{"latitude": 50.5, "longitude": 10.5, "length_m": 40.0}]
        }
        scan_repo.save(Scan(self.scan_folder, bbox, acq, str(img_path), metadata))

        settings = Settings(
            copernicus_username="test_user",
            copernicus_password="test_pass",
            n2yo_api_key="test_key",
            project_root=Path(__file__).resolve().parent.parent,
            database_path=db_path,
            output_root=output_root,
            cache_root=Path(self.tmp_dir.name) / "cache",
        )
        container = ApplicationContainer(settings)
        app = create_app(settings=settings, container=container, start_background_workers=False)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_geotiff_endpoint(self):
        resp = self.client.get(f"/api/scan/{self.scan_folder}/export/geotiff")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("image/tiff", resp.content_type)
        self.assertTrue(len(resp.data) > 0)
        resp.close()

    def test_stac_endpoint(self):
        resp = self.client.get(f"/api/scan/{self.scan_folder}/export/stac")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["stac_version"], "1.0.0")
        self.assertEqual(data["id"], self.scan_folder)

    def test_geojson_endpoint(self):
        resp = self.client.get(f"/api/scan/{self.scan_folder}/export/geojson")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), 1)


if __name__ == "__main__":
    unittest.main()
