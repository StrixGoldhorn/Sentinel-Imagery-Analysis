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

    def test_export_geojson_converts_pixels_via_affine_when_lat_lon_missing(self):
        # Create scan where detections only have pixel coordinates (no lat/lon)
        scan_folder_affine = "test_scan_affine_coords"
        scan_dir = self.output_root / scan_folder_affine / "images"
        scan_dir.mkdir(parents=True, exist_ok=True)
        img_path = scan_dir / f"{scan_folder_affine}_stitched.png"
        img_arr = np.full((100, 200), 128, dtype=np.uint8)
        Image.fromarray(img_arr).save(img_path)

        metadata = {
            "detections": [
                {
                    "pixel_x": 50,
                    "pixel_y": 30,
                    "confidence": 0.9,
                },
                {
                    "pixel_x": 120,
                    "pixel_y": 70,
                    "confidence": 0.85,
                },
            ],
        }
        scan = Scan(scan_folder_affine, self.bbox, self.acq, str(img_path), metadata)
        self.scan_repo.save(scan)

        fc = self.exporter.export_geojson(scan_folder_affine)
        self.assertEqual(len(fc["features"]), 2)

        feat0 = fc["features"][0]
        # (50, 30) on 200x100 raster with bbox [12.0, 42.0, 14.0, 43.0]
        # lon = 12.0 + 50 * (2.0 / 200) = 12.5
        # lat = 43.0 - 30 * (1.0 / 100) = 42.7
        # Must NOT be AOI center (13.0, 42.5)
        self.assertAlmostEqual(feat0["geometry"]["coordinates"][0], 12.5)
        self.assertAlmostEqual(feat0["geometry"]["coordinates"][1], 42.7)
        self.assertAlmostEqual(feat0["properties"]["longitude"], 12.5)
        self.assertAlmostEqual(feat0["properties"]["latitude"], 42.7)

        feat1 = fc["features"][1]
        # lon = 12.0 + 120 * 0.01 = 13.2
        # lat = 43.0 - 70 * 0.01 = 42.3
        self.assertAlmostEqual(feat1["geometry"]["coordinates"][0], 13.2)
        self.assertAlmostEqual(feat1["geometry"]["coordinates"][1], 42.3)

    def test_export_geojson_transforms_polygon_points_via_affine(self):
        scan_folder_poly = "test_scan_affine_polygon"
        scan_dir = self.output_root / scan_folder_poly / "images"
        scan_dir.mkdir(parents=True, exist_ok=True)
        img_path = scan_dir / f"{scan_folder_poly}_stitched.png"
        img_arr = np.full((100, 200), 128, dtype=np.uint8)
        Image.fromarray(img_arr).save(img_path)

        metadata = {
            "detections": [
                {
                    "pixel_x": 50,
                    "pixel_y": 30,
                    "polygon_points": [(40, 20), (60, 20), (60, 40), (40, 40)],
                    "confidence": 0.92,
                },
            ],
        }
        scan = Scan(scan_folder_poly, self.bbox, self.acq, str(img_path), metadata)
        self.scan_repo.save(scan)

        fc = self.exporter.export_geojson(scan_folder_poly)
        feat = fc["features"][0]
        self.assertEqual(feat["geometry"]["type"], "Polygon")
        ring = feat["geometry"]["coordinates"][0]
        self.assertEqual(len(ring), 5)  # 4 vertices + 1 closed
        self.assertEqual(ring[0], ring[-1])
        # (40, 20) -> lon = 12.0 + 40*0.01 = 12.4, lat = 43.0 - 20*0.01 = 42.8
        self.assertAlmostEqual(ring[0][0], 12.4)
        self.assertAlmostEqual(ring[0][1], 42.8)

    def test_export_kmz(self):
        import zipfile
        kmz_path = self.exporter.export_kmz(self.scan_folder)
        self.assertTrue(kmz_path.is_file())
        self.assertTrue(kmz_path.name.endswith(".kmz"))

        with zipfile.ZipFile(kmz_path, "r") as kmz:
            self.assertIn("doc.kml", kmz.namelist())
            kml_text = kmz.read("doc.kml").decode("utf-8")
            self.assertIn("<kml xmlns=", kml_text)
            self.assertIn(self.scan_folder, kml_text)
            self.assertIn("darkVesselStyle", kml_text)
            self.assertIn("compliantVesselStyle", kml_text)
            self.assertIn("12.5,42.7,0", kml_text)

    def test_export_cursor_on_target(self):
        cot_xml = self.exporter.export_cursor_on_target(self.scan_folder)
        self.assertIn('<?xml version="1.0" encoding="UTF-8"?>', cot_xml)
        self.assertIn('<events version="2.0">', cot_xml)
        self.assertIn('<event version="2.0"', cot_xml)
        self.assertIn('lat="42.700000" lon="12.500000"', cot_xml)
        self.assertIn('type="a-u-S"', cot_xml)  # dark vessel


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

    def test_kmz_endpoint(self):
        resp = self.client.get(f"/api/scan/{self.scan_folder}/export/kmz")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/vnd.google-earth.kmz", resp.content_type)
        self.assertTrue(len(resp.data) > 0)
        resp.close()

    def test_cot_endpoint(self):
        resp = self.client.get(f"/api/scan/{self.scan_folder}/export/cot")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/xml", resp.content_type)
        self.assertIn(b"<events version=\"2.0\">", resp.data)
        self.assertIn(b"<event version=\"2.0\"", resp.data)
        resp.close()


if __name__ == "__main__":
    unittest.main()
