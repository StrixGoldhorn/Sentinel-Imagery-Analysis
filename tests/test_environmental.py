import unittest
from datetime import datetime, timezone

from sentinel_analysis.application.use_cases.enrich_environmental_context import EnrichMarineEnvironmentalContext
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.environmental import (
    BathymetryPoint,
    InfrastructureType,
    MaritimeZoneType,
    NavigationalZone,
    OceanCurrentVector,
    OffshoreInfrastructure,
    WaveField,
    WindVector,
)
from sentinel_analysis.infrastructure.environmental.marine_context_provider import MarineContextProvider


class TestEnvironmentalLayers(unittest.TestCase):
    def setUp(self):
        self.provider = MarineContextProvider()
        self.use_case = EnrichMarineEnvironmentalContext(self.provider)
        self.singapore_bbox = BoundingBox(
            min_latitude=1.15,
            min_longitude=103.65,
            max_latitude=1.30,
            max_longitude=103.85,
        )

    def test_domain_models_properties(self):
        wind = WindVector(speed_mps=10.0, direction_deg=90.0, gust_mps=14.0)
        self.assertAlmostEqual(wind.speed_knots, 19.4, delta=0.2)
        self.assertEqual(wind.to_dict()["direction_deg"], 90.0)

        # 3.5m wave height is ROUGH and Beaufort 6
        wave = WaveField(significant_wave_height_m=3.5, peak_period_s=7.0, mean_direction_deg=260.0)
        self.assertEqual(wave.sea_state_beaufort, 6)
        self.assertEqual(wave.sea_state_code, "ROUGH")

        current = OceanCurrentVector(speed_mps=1.0, direction_deg=180.0)
        self.assertAlmostEqual(current.speed_knots, 1.94, delta=0.1)

        bathy = BathymetryPoint(depth_meters=45.0, is_navigable_channel=True, shoal_hazard=False)
        self.assertEqual(bathy.depth_meters, 45.0)
        self.assertTrue(bathy.is_navigable_channel)

    def test_marine_context_provider_bbox_query(self):
        ctx = self.provider.get_context_for_bbox(self.singapore_bbox)
        self.assertIsNotNone(ctx.wind)
        self.assertIsNotNone(ctx.waves)
        self.assertIsNotNone(ctx.currents)
        self.assertIn("mean_depth_meters", ctx.bathymetry_summary)

        zone_names = [z.name for z in ctx.active_zones]
        # Should detect Singapore Strait TSS and Singapore anchorages
        self.assertTrue(any("Singapore Strait TSS" in name for name in zone_names))
        self.assertTrue(any("Port of Singapore" in name for name in zone_names))

    def test_tag_detections_with_environmental_context(self):
        detections = [
            # 1. Target in Singapore Strait TSS corridor (Lat 1.20, Lon 103.70)
            {
                "latitude": 1.20,
                "longitude": 103.70,
                "confidence": 0.95,
                "is_dark_vessel": True,
            },
            # 2. Target co-located with Singapore Single Buoy Mooring (Lat 1.215, Lon 103.785)
            {
                "latitude": 1.215,
                "longitude": 103.785,
                "confidence": 0.98,
                "is_dark_vessel": True,
            },
            # 3. Target in Singapore Western Anchorage (Lat 1.22, Lon 103.70)
            {
                "latitude": 1.22,
                "longitude": 103.70,
                "confidence": 0.88,
                "is_dark_vessel": False,
            },
        ]

        tagged = self.provider.tag_detections_with_environmental_context(detections, self.singapore_bbox)
        self.assertEqual(len(tagged), 3)

        # Contact 1: in shipping lane
        det1 = tagged[0]
        self.assertTrue(det1["is_in_shipping_lane"])
        self.assertEqual(det1["shipping_lane_name"], "Singapore Strait TSS")
        self.assertFalse(det1["is_offshore_infrastructure"])

        # Contact 2: matched with offshore infrastructure
        det2 = tagged[1]
        self.assertTrue(det2["is_offshore_infrastructure"])
        self.assertIsNotNone(det2["matched_infrastructure"])
        self.assertEqual(det2["matched_infrastructure"]["feature_id"], "INFRA_SINGAPORE_SBM")
        self.assertEqual(det2["detection_category"], "OFFSHORE_INFRASTRUCTURE")
        self.assertFalse(det2["is_dark_vessel"])  # Suppressed false alarm

        # Contact 3: in anchorage
        det3 = tagged[2]
        self.assertTrue(det3["is_in_anchorage"])
        self.assertEqual(det3["anchorage_name"], "Singapore Western Anchorage (AWB)")

    def test_enrich_environmental_use_case(self):
        detections = [
            {"latitude": 1.20, "longitude": 103.70, "confidence": 0.90},
        ]
        res = self.use_case.execute(aoi_bbox=self.singapore_bbox, detections=detections)
        self.assertEqual(res["status"], "success")
        self.assertIn("context", res)
        self.assertIn("wind", res["context"])
        self.assertIn("waves", res["context"])
        self.assertIn("currents", res["context"])
        self.assertIn("bathymetry", res["context"])

        geojson = res["geojson_layers"]
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertGreater(len(geojson["features"]), 0)

        layers = [f["properties"]["layer"] for f in geojson["features"]]
        self.assertIn("NAVIGATIONAL_ZONE", layers)


if __name__ == "__main__":
    unittest.main()
