"""Unit and integration tests for SAR geospatial coordinate projection and GeoJSON generation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np

from sentinel_analysis.domain.coordinates import (
    build_geojson_geometry,
    coerce_bounding_box,
    geo_to_pixel,
    pixel_to_geo,
    project_detection_coordinates,
    resolve_image_transform,
)
from sentinel_analysis.domain.entities import BoundingBox, Scan, ShipDetection
from sentinel_analysis.infrastructure.detection.detection_saver import save_detection_results


class TestCoordinateProjection(unittest.TestCase):
    def setUp(self) -> None:
        self.bbox = BoundingBox(
            min_longitude=10.0,
            min_latitude=50.0,
            max_longitude=12.0,
            max_latitude=52.0,
        )
        self.img_w = 1000
        self.img_h = 1000

    def test_coerce_bounding_box(self) -> None:
        # None
        self.assertIsNone(coerce_bounding_box(None))

        # Existing BoundingBox
        self.assertEqual(coerce_bounding_box(self.bbox), self.bbox)

        # Object with attributes
        class BBoxObj:
            min_longitude = 1.0
            min_latitude = 2.0
            max_longitude = 3.0
            max_latitude = 4.0

        coerced = coerce_bounding_box(BBoxObj())
        self.assertIsNotNone(coerced)
        self.assertEqual(coerced.min_longitude, 1.0)
        self.assertEqual(coerced.min_latitude, 2.0)
        self.assertEqual(coerced.max_longitude, 3.0)
        self.assertEqual(coerced.max_latitude, 4.0)

        # Dict with min_lat / min_lon
        dict_box = {"min_lat": 20.0, "max_lat": 22.0, "min_lon": -60.0, "max_lon": -58.0}
        coerced_dict = coerce_bounding_box(dict_box)
        self.assertIsNotNone(coerced_dict)
        self.assertEqual(coerced_dict.min_latitude, 20.0)
        self.assertEqual(coerced_dict.min_longitude, -60.0)

        # Sequence of 4 floats (min_lon, min_lat, max_lon, max_lat)
        seq = [10.0, 50.0, 12.0, 52.0]
        coerced_seq = coerce_bounding_box(seq)
        self.assertIsNotNone(coerced_seq)
        self.assertEqual(coerced_seq.min_longitude, 10.0)
        self.assertEqual(coerced_seq.max_latitude, 52.0)

        # Invalid cases
        self.assertIsNone(coerce_bounding_box("invalid"))
        self.assertIsNone(coerce_bounding_box([1, 2]))

    def test_pixel_to_geo_and_roundtrip(self) -> None:
        # Top-left pixel (0, 0) -> (max_lat, min_lon)
        lat, lon = pixel_to_geo(0, 0, self.bbox, self.img_w, self.img_h)
        self.assertAlmostEqual(lat, 52.0)
        self.assertAlmostEqual(lon, 10.0)

        # Bottom-right pixel (1000, 1000) -> (min_lat, max_lon)
        lat, lon = pixel_to_geo(1000, 1000, self.bbox, self.img_w, self.img_h)
        self.assertAlmostEqual(lat, 50.0)
        self.assertAlmostEqual(lon, 12.0)

        # Center pixel (500, 500) -> (51.0, 11.0)
        lat, lon = pixel_to_geo(500, 500, self.bbox, self.img_w, self.img_h)
        self.assertAlmostEqual(lat, 51.0)
        self.assertAlmostEqual(lon, 11.0)

        # Roundtrip via geo_to_pixel
        px, py = geo_to_pixel(lat, lon, self.bbox, self.img_w, self.img_h)
        self.assertAlmostEqual(px, 500.0)
        self.assertAlmostEqual(py, 500.0)

    def test_pixel_to_geo_clamping_and_errors(self) -> None:
        # Without bbox and transform_fn -> ValueError
        with self.assertRaises(ValueError):
            pixel_to_geo(10, 10)

        # Custom transform_fn
        def dummy_transform(px: float, py: float) -> tuple[float, float]:
            return 15.5, 45.2  # lon, lat

        lat, lon = pixel_to_geo(0, 0, transform_fn=dummy_transform)
        self.assertAlmostEqual(lat, 45.2)
        self.assertAlmostEqual(lon, 15.5)

    def test_project_detection_coordinates_ship_detection(self) -> None:
        det = ShipDetection(
            x=200,
            y=300,
            width=50,
            height=40,
            confidence=0.92,
            angle=30.0,
            polygon_points=[[200, 300], [250, 300], [250, 340], [200, 340]],
        )

        projected = project_detection_coordinates(
            det=det,
            bbox=self.bbox,
            image_width=self.img_w,
            image_height=self.img_h,
        )

        # Center x=225, y=320
        # Expected lon = 10.0 + (225 / 1000) * 2.0 = 10.45
        # Expected lat = 52.0 - (320 / 1000) * 2.0 = 51.36
        self.assertAlmostEqual(projected["lat"], 51.36, places=4)
        self.assertAlmostEqual(projected["lng"], 10.45, places=4)
        self.assertAlmostEqual(projected["latitude"], 51.36, places=5)
        self.assertAlmostEqual(projected["longitude"], 10.45, places=5)

        # Check geo_bbox
        self.assertIn("min_lat", projected["geo_bbox"])
        self.assertIn("max_lat", projected["geo_bbox"])
        self.assertIn("min_lon", projected["geo_bbox"])
        self.assertIn("max_lon", projected["geo_bbox"])
        self.assertTrue(projected["geo_bbox"]["min_lat"] < projected["geo_bbox"]["max_lat"])
        self.assertTrue(projected["geo_bbox"]["min_lon"] < projected["geo_bbox"]["max_lon"])

        # Check geo_polygon
        self.assertIsNotNone(projected["geo_polygon"])
        self.assertEqual(len(projected["geo_polygon"]), 4)

    def test_project_detection_coordinates_dict_preservation(self) -> None:
        det_dict = {
            "x": 100,
            "y": 100,
            "width": 20,
            "height": 20,
            "latitude": -33.8688,
            "longitude": 151.2093,
            "geo_bbox": {
                "min_lat": -33.87,
                "max_lat": -33.86,
                "min_lon": 151.20,
                "max_lon": 151.21,
            },
        }

        # With force=False, existing coordinates must be preserved
        projected = project_detection_coordinates(det_dict, bbox=self.bbox, force=False)
        self.assertAlmostEqual(projected["latitude"], -33.8688, places=4)
        self.assertAlmostEqual(projected["longitude"], 151.2093, places=4)

        # With force=True, reprojected from bbox
        projected_forced = project_detection_coordinates(det_dict, bbox=self.bbox, image_width=1000, image_height=1000, force=True)
        self.assertTrue(projected_forced["latitude"] > 50.0)

    def test_build_geojson_geometry(self) -> None:
        # Case 1: geo_polygon provided (list of (lat, lon))
        det_poly = {
            "latitude": 51.0,
            "longitude": 11.0,
            "geo_polygon": [(51.0, 11.0), (51.1, 11.0), (51.1, 11.2), (51.0, 11.2)],
        }
        geom = build_geojson_geometry(det_poly)
        self.assertIsNotNone(geom)
        self.assertEqual(geom["type"], "Polygon")
        coords = geom["coordinates"][0]
        # GeoJSON is [lon, lat] and must be closed
        self.assertEqual(coords[0], [11.0, 51.0])
        self.assertEqual(coords[-1], [11.0, 51.0])
        self.assertEqual(len(coords), 5)

        # Case 2: geo_bbox provided
        det_box = {
            "latitude": 51.0,
            "longitude": 11.0,
            "geo_bbox": {"min_lat": 50.5, "max_lat": 51.5, "min_lon": 10.5, "max_lon": 11.5},
        }
        geom_box = build_geojson_geometry(det_box)
        self.assertIsNotNone(geom_box)
        self.assertEqual(geom_box["type"], "Polygon")
        self.assertEqual(len(geom_box["coordinates"][0]), 5)

        # Case 3: Only lat / lng provided
        det_pt = {"latitude": -12.34, "longitude": 45.67}
        geom_pt = build_geojson_geometry(det_pt)
        self.assertIsNotNone(geom_pt)
        self.assertEqual(geom_pt["type"], "Point")
        self.assertEqual(geom_pt["coordinates"], [45.67, -12.34])


class TestDetectionSaverGeospatial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.image_path = self.dir_path / "sar_scene.png"
        sample_img = np.zeros((200, 200), dtype=np.uint8)
        sample_img[50:80, 60:90] = 220
        cv2.imwrite(str(self.image_path), sample_img)
        self.bbox = BoundingBox(
            min_longitude=-5.0,
            min_latitude=48.0,
            max_longitude=-3.0,
            max_latitude=50.0,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_detection_results_embeds_and_projects_geocoordinates(self) -> None:
        detections = [
            ShipDetection(
                x=60,
                y=50,
                width=30,
                height=30,
                confidence=0.91,
                angle=20.0,
                length=35.0,
                beam=15.0,
                center_x=75.0,
                center_y=65.0,
                polygon_points=[[60, 50], [90, 50], [90, 80], [60, 80]],
            )
        ]

        result = save_detection_results(
            image_path=self.image_path,
            detections=detections,
            image_width=200,
            image_height=200,
            metadata={"source": "test_sar"},
            bbox=self.bbox,
        )

        self.assertEqual(result["ship_count"], 1)

        # Check standard detection_results.json
        res_json = self.dir_path / "detection_results.json"
        self.assertTrue(res_json.is_file())
        data = json.loads(res_json.read_text(encoding="utf-8"))
        det = data["detections"][0]

        # Verify lat/lng and latitude/longitude exist and are non-null
        self.assertIn("lat", det)
        self.assertIn("lng", det)
        self.assertIn("latitude", det)
        self.assertIn("longitude", det)
        self.assertIsNotNone(det["lat"])
        self.assertIsNotNone(det["lng"])
        self.assertTrue(48.0 <= det["lat"] <= 50.0)
        self.assertTrue(-5.0 <= det["lng"] <= -3.0)

        # Verify geo_bbox and geo_polygon
        self.assertIn("geo_bbox", det)
        self.assertIn("geo_polygon", det)
        self.assertIsNotNone(det["geo_bbox"])
        self.assertIsNotNone(det["geo_polygon"])

        # Check GeoJSON outputs
        geojson_files = [
            self.dir_path / "detections.geojson",
            self.dir_path / "sar_scene_detections.geojson",
        ]
        for gj_file in geojson_files:
            self.assertTrue(gj_file.is_file(), f"{gj_file.name} must exist")
            gj_data = json.loads(gj_file.read_text(encoding="utf-8"))
            self.assertEqual(gj_data["type"], "FeatureCollection")
            self.assertEqual(len(gj_data["features"]), 1)
            feat = gj_data["features"][0]
            geom = feat["geometry"]
            self.assertIsNotNone(geom, "Geometry in GeoJSON feature must not be null")
            self.assertEqual(geom["type"], "Polygon")
            # In GeoJSON RFC 7946, coordinates are [longitude, latitude]
            ring = geom["coordinates"][0]
            self.assertEqual(ring[0], ring[-1], "Polygon linear ring must be closed")
            for coord in ring:
                lon, lat = coord[0], coord[1]
                self.assertTrue(-5.0 <= lon <= -3.0)
                self.assertTrue(48.0 <= lat <= 50.0)


if __name__ == "__main__":
    unittest.main()
