"""Unit tests for utils/crs_inspector.py."""

import json
import tempfile
import unittest
from pathlib import Path

from utils.crs_inspector import (
    calculate_utm_zone,
    diagnose_bbox,
    diagnose_coordinates,
    inspect_geojson_file,
    inspect_prj_file,
    recommend_crs,
    web_mercator_to_wgs84,
    wgs84_to_web_mercator,
)


class TestCRSInspector(unittest.TestCase):
    def test_calculate_utm_zone(self):
        # San Francisco: lon -122.4, lat 37.7 -> Zone 10N (EPSG 32610)
        zone, epsg = calculate_utm_zone(-122.4194, 37.7749)
        self.assertEqual(zone, 10)
        self.assertEqual(epsg, 32610)

        # London: lon -0.12, lat 51.5 -> Zone 30N (EPSG 32630)
        zone, epsg = calculate_utm_zone(-0.1276, 51.5072)
        self.assertEqual(zone, 30)
        self.assertEqual(epsg, 32630)

        # Sydney, Australia: lon 151.2, lat -33.8 -> Zone 56S (EPSG 32756)
        zone, epsg = calculate_utm_zone(151.2093, -33.8688)
        self.assertEqual(zone, 56)
        self.assertEqual(epsg, 32756)

    def test_wgs84_coordinates_diagnosis(self):
        # Standard WGS84 coordinates: (lon, lat)
        res = diagnose_coordinates(-122.4194, 37.7749)
        self.assertEqual(res.epsg_code, 4326)
        self.assertEqual(res.coordinate_type, "GEOGRAPHIC")
        self.assertEqual(res.utm_zone_candidate, 10)
        self.assertEqual(res.utm_epsg_candidate, 32610)

    def test_inverted_coordinates_detection(self):
        # Inverted: X=37.7749 (lat), Y=-122.4194 (lon)
        res = diagnose_coordinates(37.7749, -122.4194)
        self.assertEqual(res.epsg_code, 4326)
        self.assertIn("Inverted", res.likely_crs)
        self.assertTrue(any("Axis inversion detected" in w for w in res.warnings))
        self.assertAlmostEqual(res.approx_wgs84_coords[0], -122.4194, places=3)
        self.assertAlmostEqual(res.approx_wgs84_coords[1], 37.7749, places=3)

    def test_web_mercator_diagnosis(self):
        # Web Mercator coordinates for San Francisco
        wm_x, wm_y = wgs84_to_web_mercator(-122.4194, 37.7749)
        res = diagnose_coordinates(wm_x, wm_y)
        self.assertEqual(res.epsg_code, 3857)
        self.assertEqual(res.coordinate_type, "PROJECTED")
        self.assertEqual(res.units, "meters")
        self.assertIsNotNone(res.approx_wgs84_coords)
        self.assertAlmostEqual(res.approx_wgs84_coords[0], -122.4194, places=2)
        self.assertAlmostEqual(res.approx_wgs84_coords[1], 37.7749, places=2)

    def test_utm_coordinates_diagnosis(self):
        # UTM Zone 10N easting/northing
        easting = 551000.0
        northing = 4181000.0
        res = diagnose_coordinates(easting, northing)
        self.assertIn("UTM", res.likely_crs)
        self.assertEqual(res.coordinate_type, "PROJECTED")
        self.assertEqual(res.units, "meters")

    def test_british_national_grid_diagnosis(self):
        # BNG coordinates in London
        bng_x = 530000.0
        bng_y = 180000.0
        res = diagnose_coordinates(bng_x, bng_y)
        self.assertEqual(res.epsg_code, 27700)
        self.assertEqual(res.coordinate_type, "PROJECTED")

    def test_bbox_diagnosis(self):
        res = diagnose_bbox(-123.0, 37.0, -122.0, 38.0)
        self.assertEqual(res.epsg_code, 4326)
        self.assertEqual(res.coordinate_type, "GEOGRAPHIC")

    def test_recommend_crs(self):
        rec_web = recommend_crs("web_display")
        self.assertEqual(rec_web["epsg_code"], 3857)

        rec_storage = recommend_crs("storage")
        self.assertEqual(rec_storage["epsg_code"], 4326)

        rec_area_us = recommend_crs("area", region="us")
        self.assertEqual(rec_area_us["epsg_code"], 5070)

        rec_area_eu = recommend_crs("area", region="europe")
        self.assertEqual(rec_area_eu["epsg_code"], 3035)

        rec_satellite = recommend_crs("satellite", lon=-122.4, lat=37.7)
        self.assertEqual(rec_satellite["epsg_code"], 32610)

    def test_inspect_prj_file(self):
        wkt_content = (
            'PROJCS["WGS 84 / UTM zone 10N",GEOGCS["WGS 84",DATUM["WGS_1984",'
            'SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
            'UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",-123],'
            'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
            'PARAMETER["false_northing",0],UNIT["metre",1],AUTHORITY["EPSG","32610"]]'
        )
        with tempfile.NamedTemporaryFile("w", suffix=".prj", delete=False, encoding="utf-8") as f:
            f.write(wkt_content)
            temp_path = f.name

        try:
            res = inspect_prj_file(temp_path)
            self.assertEqual(res["epsg_code"], 32610)
            self.assertEqual(res["crs_name"], "WGS 84 / UTM zone 10N")
            self.assertTrue(res["is_projected"])
            self.assertEqual(res["datum"], "WGS_1984")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_inspect_geojson_file(self):
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.4194, 37.7749]
                    },
                    "properties": {"name": "Test Point"}
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False, encoding="utf-8") as f:
            json.dump(geojson_data, f)
            temp_path = f.name

        try:
            res = inspect_geojson_file(temp_path)
            self.assertTrue(res["rfc7946_compliant"])
            self.assertEqual(res["sample_coordinate"], [-122.4194, 37.7749])
            self.assertEqual(res["diagnosis"]["epsg_code"], 4326)
        finally:
            Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
