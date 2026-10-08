"""Unit tests for Umbra Open Data SAR catalog client."""

import unittest
from datetime import datetime, timezone

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.satellite.umbra_client import UmbraOpenDataClient, UmbraSARScene


class TestUmbraOpenDataClient(unittest.TestCase):
    def setUp(self):
        self.client = UmbraOpenDataClient()
        self.singapore_bbox = BoundingBox(
            min_longitude=103.7,
            min_latitude=1.15,
            max_longitude=104.0,
            max_latitude=1.35,
        )

    def test_maritime_sites_coverage(self):
        sites = self.client.get_maritime_sites()
        self.assertIn("singapore_strait", sites)
        self.assertIn("suez_canal", sites)
        self.assertIn("panama_canal", sites)
        self.assertIn("port_of_rotterdam", sites)

    def test_search_nearby_site(self):
        match = self.client.search_nearby_site(self.singapore_bbox)
        self.assertEqual(match, "singapore_strait")

        nowhere_bbox = BoundingBox(-150.0, -50.0, -149.0, -49.0)
        self.assertIsNone(self.client.search_nearby_site(nowhere_bbox))

    def test_parse_stac_item(self):
        stac_item = {
            "type": "Feature",
            "id": "2026-10-01-08-30-00_UMBRA-05",
            "bbox": [103.8, 1.2, 103.9, 1.3],
            "properties": {
                "datetime": "2026-10-01T08:30:00Z",
                "sar:resolution_range": 0.35,
                "sar:polarizations": ["VV"],
                "umbra:target_name": "Singapore Strait Channel",
            },
            "assets": {
                "geotiff": {
                    "href": "https://umbra-open-data-catalog.s3.amazonaws.com/data/sample.tif"
                }
            },
            "links": [
                {
                    "rel": "self",
                    "href": "https://umbra-open-data-catalog.s3.amazonaws.com/stac/sample.json"
                }
            ],
        }

        scene = self.client.parse_stac_item(stac_item)
        self.assertIsNotNone(scene)
        self.assertIsInstance(scene, UmbraSARScene)
        self.assertEqual(scene.scene_id, "2026-10-01-08-30-00_UMBRA-05")
        self.assertEqual(scene.target_name, "Singapore Strait Channel")
        self.assertAlmostEqual(scene.resolution_meters, 0.35)
        self.assertEqual(scene.polarization, "VV")
        self.assertEqual(scene.tiff_url, "https://umbra-open-data-catalog.s3.amazonaws.com/data/sample.tif")
        self.assertEqual(scene.stac_url, "https://umbra-open-data-catalog.s3.amazonaws.com/stac/sample.json")
        self.assertEqual(scene.timestamp, datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc))

        # Check serialization
        d = scene.to_dict()
        self.assertEqual(d["scene_id"], "2026-10-01-08-30-00_UMBRA-05")
        self.assertEqual(d["bbox"], [103.8, 1.2, 103.9, 1.3])

    def test_fetch_site_scenes_fallback(self):
        scenes = self.client.fetch_site_scenes("singapore_strait")
        self.assertGreaterEqual(len(scenes), 1)
        self.assertEqual(scenes[0].target_name, "Singapore Strait")
        self.assertAlmostEqual(scenes[0].resolution_meters, 0.5)

    def test_fetch_site_scenes_has_valid_tiff_url(self):
        scenes = self.client.fetch_site_scenes("panama_canal")
        self.assertGreaterEqual(len(scenes), 1)
        self.assertTrue(scenes[0].tiff_url.endswith(".tif"))
        self.assertTrue(scenes[0].tiff_url.startswith("https://umbra-open-data-catalog.s3.amazonaws.com"))


if __name__ == "__main__":
    unittest.main()

