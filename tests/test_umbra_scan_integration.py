"""Integration tests for Umbra Sub-Meter SAR and NASA ASF DAAC scan pipelines."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.imagery.stitching import PillowImageStitcher
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.infrastructure.satellite.asf_client import ASFProduct, ASFSearchClient
from sentinel_analysis.infrastructure.satellite.asf_provider import ASFImageryProvider
from sentinel_analysis.infrastructure.satellite.umbra_client import UmbraOpenDataClient, UmbraSARScene
from sentinel_analysis.infrastructure.satellite.umbra_provider import UmbraImageryProvider
from sentinel_analysis.interfaces.web.application import create_app


class DummyLocationResolver:
    def resolve(self, lat: float, lon: float) -> str:
        return "Test Strait, Maritime Zone"


class TestUmbraAndASFScanIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name)
        self.scan_repo = FilesystemScanRepository(self.output_root)
        self.stitcher = PillowImageStitcher()
        self.locations = DummyLocationResolver()

        self.singapore_bbox = BoundingBox(103.85, 1.25, 103.87, 1.27)

        self.mock_umbra_client = MagicMock(spec=UmbraOpenDataClient)
        self.mock_umbra_client.search_nearby_site.return_value = "singapore_strait"
        self.mock_umbra_client.fetch_site_scenes.return_value = [
            UmbraSARScene(
                scene_id="umbra_singapore_open_01",
                target_name="Singapore Strait",
                timestamp=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
                resolution_meters=0.5,
                polarization="VV",
                bbox=self.singapore_bbox,
                tiff_url="https://umbra.test/image.tif",
                stac_url="https://umbra.test/stac.json",
            )
        ]
        self.umbra_provider = UmbraImageryProvider(self.mock_umbra_client)

        self.mock_asf_client = MagicMock(spec=ASFSearchClient)
        self.mock_asf_client.search.return_value = [
            ASFProduct(
                granule_name="S1A_IW_GRDH_TEST_001",
                platform="Sentinel-1A",
                processing_level="GRD_HD",
                beam_mode="IW",
                polarization="VV+VH",
                flight_direction="DESCENDING",
                start_time=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
                stop_time=datetime(2026, 10, 1, 12, 1, tzinfo=timezone.utc),
                orbit=55000,
                relative_orbit=142,
                download_url="https://datapool.asf.alaska.edu/test.zip",
                size_mb=850.0,
                center_lat=1.25,
                center_lon=103.85,
            )
        ]
        self.asf_provider = ASFImageryProvider(self.mock_asf_client)

        self.create_scan = CreateScan(
            imagery=self.umbra_provider,  # default
            stitcher=self.stitcher,
            scans=self.scan_repo,
            locations=self.locations,
            providers={
                "umbra": self.umbra_provider,
                "asf": self.asf_provider,
            },
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_umbra_provider_acquisitions_and_tiles(self):
        acq = self.umbra_provider.find_latest_acquisition(self.singapore_bbox)
        self.assertIsNotNone(acq)
        self.assertIn("Umbra", acq.satellite)
        self.assertEqual(acq.product_type, "UMBRA_GEC")
        self.assertEqual(acq.polarizations, ("VV",))

        tiles = self.umbra_provider.calculate_tiles(self.singapore_bbox)
        self.assertGreaterEqual(len(tiles), 1)

        tile_out = self.output_root / "test_tile.png"
        self.umbra_provider.download_tile(tiles[0], acq, tile_out)
        self.assertTrue(tile_out.is_file())
        self.assertGreater(tile_out.stat().st_size, 100)

    def test_umbra_download_tile_produces_high_contrast_imagery(self):
        from PIL import Image
        import numpy as np

        acq = self.umbra_provider.find_latest_acquisition(self.singapore_bbox)
        tiles = self.umbra_provider.calculate_tiles(self.singapore_bbox)
        tile_out = self.output_root / "test_tile_enhanced.png"
        self.umbra_provider.download_tile(tiles[0], acq, tile_out)

        with Image.open(tile_out) as img:
            arr = np.array(img, dtype=np.float32)
            self.assertGreater(float(np.std(arr)), 25.0)
            self.assertGreater(float(np.max(arr) - np.min(arr)), 100.0)


    def test_create_scan_with_umbra_provider(self):
        scan = self.create_scan.execute(
            bbox=self.singapore_bbox,
            provider="umbra",
            aoi_name="Singapore_Umbra",
        )
        self.assertIsNotNone(scan)
        self.assertEqual(scan.metadata.get("provider"), "umbra")
        self.assertIn("Umbra", scan.acquisition.satellite)
        self.assertTrue(Path(scan.image_path).is_file())
        self.assertGreater(Path(scan.image_path).stat().st_size, 100)

        # Ensure scan is retrievable from scan repo
        retrieved = self.scan_repo.get(scan.folder_name)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.metadata.get("provider"), "umbra")

    def test_create_scan_with_asf_provider(self):
        scan = self.create_scan.execute(
            bbox=self.singapore_bbox,
            provider="asf",
            aoi_name="Singapore_ASF",
        )
        self.assertIsNotNone(scan)
        self.assertEqual(scan.metadata.get("provider"), "asf")
        self.assertEqual(scan.acquisition.satellite, "Sentinel-1A")
        self.assertEqual(scan.acquisition.product_id, "S1A_IW_GRDH_TEST_001")
        self.assertTrue(Path(scan.image_path).is_file())


class TestUmbraAndASFWebEndpoints(unittest.TestCase):
    def setUp(self):
        self.app = create_app(start_background_workers=False)
        self.client = self.app.test_client()

    def test_umbra_scenes_api_returns_maritime_sites(self):
        resp = self.client.get("/api/umbra/scenes")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("singapore_strait", data["sites"])
        self.assertIn("suez_canal", data["sites"])

    def test_umbra_scenes_api_with_site_filter(self):
        from unittest.mock import patch
        mock_scene = UmbraSARScene(
            scene_id="umbra_test_001",
            target_name="Singapore Strait",
            timestamp=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            resolution_meters=0.5,
            polarization="VV",
            bbox=BoundingBox(103.6, 1.1, 104.1, 1.4),
            tiff_url="https://umbra.test/image.tif",
            stac_url="https://umbra.test/stac.json",
        )
        with patch("sentinel_analysis.infrastructure.satellite.umbra_client.UmbraOpenDataClient.fetch_site_scenes", return_value=[mock_scene]):
            resp = self.client.get("/api/umbra/scenes?site=singapore_strait")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["matched_site"], "singapore_strait")
            self.assertGreaterEqual(len(data["scenes"]), 1)
            first_scene = data["scenes"][0]
            self.assertEqual(first_scene["scene_id"], "umbra_test_001")
            self.assertEqual(first_scene["resolution_meters"], 0.5)

    def test_asf_search_api(self):
        from unittest.mock import patch
        mock_prod = ASFProduct(
            granule_name="S1A_TEST_WEB_001",
            platform="Sentinel-1A",
            processing_level="GRD_HD",
            beam_mode="IW",
            polarization="VV+VH",
            flight_direction="DESCENDING",
            start_time=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            stop_time=datetime(2026, 10, 1, 12, 1, tzinfo=timezone.utc),
            orbit=55000,
            relative_orbit=142,
            download_url="https://datapool.asf.alaska.edu/test.zip",
            size_mb=850.0,
            center_lat=1.25,
            center_lon=103.85,
        )
        with patch("sentinel_analysis.infrastructure.satellite.asf_client.ASFSearchClient.search", return_value=[mock_prod]):
            resp = self.client.get("/api/asf/search?min_lon=103.7&min_lat=1.15&max_lon=104.0&max_lat=1.35")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "success")
            self.assertIn("products", data)
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["products"][0]["granule_name"], "S1A_TEST_WEB_001")


if __name__ == "__main__":
    unittest.main()

