"""Unit tests for DigiTraffic Marine open AIS plugin."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.ais.plugins.digitraffic import DigiTrafficPlugin


class TestDigiTrafficPlugin(unittest.TestCase):
    def setUp(self):
        self.bbox = BoundingBox(
            min_longitude=24.0,
            min_latitude=59.5,
            max_longitude=26.0,
            max_latitude=60.5,
        )

    def test_registry_includes_digitraffic_plugin(self):
        registry = DynamicAISPluginRegistry()
        plugins = registry.get_plugins("DigiTrafficPlugin")
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].name, "DigiTrafficPlugin")
        meta = registry.get_plugin_metadata("DigiTrafficPlugin")
        self.assertEqual(meta["category"], "Open Government API")
        self.assertTrue(meta["default_enabled"])

    def test_ship_type_mapping(self):
        self.assertEqual(DigiTrafficPlugin.get_ship_type(30), "Fishing")
        self.assertEqual(DigiTrafficPlugin.get_ship_type(31), "Tug")
        self.assertEqual(DigiTrafficPlugin.get_ship_type(70), "Cargo")
        self.assertEqual(DigiTrafficPlugin.get_ship_type(80), "Tanker")
        self.assertEqual(DigiTrafficPlugin.get_ship_type(60), "Passenger")
        self.assertEqual(DigiTrafficPlugin.get_ship_type(36), "Sailing")
        self.assertEqual(DigiTrafficPlugin.get_ship_type(99), "Other")
        self.assertIsNone(DigiTrafficPlugin.get_ship_type(None))

    def test_configure(self):
        plugin = DigiTrafficPlugin()
        plugin.configure({
            "proxy_url": "http://127.0.0.1:8080",
            "timeout": 15.0,
            "user_agent": "CustomAgent/1.0",
        })
        self.assertEqual(plugin._timeout, 15.0)
        self.assertEqual(plugin.user_agent, "CustomAgent/1.0")
        self.assertEqual(plugin.proxy_url, "http://127.0.0.1:8080")
        self.assertIsNone(plugin.authenticate())

    def test_parse_geojson_feature_collection(self):
        plugin = DigiTrafficPlugin()
        plugin._vessel_metadata_cache = {
            "230111222": {
                "mmsi": 230111222,
                "name": "NORDIC RUNNER",
                "shipType": 70,
                "callSign": "OG1234",
                "imo": 9123456,
            }
        }

        geojson_payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [24.95, 60.15],
                    },
                    "properties": {
                        "mmsi": 230111222,
                        "sog": 14.5,
                        "cog": 95.0,
                        "heading": 94,
                        "time": 1728345600,
                    },
                },
                {
                    # Outside bbox (lon 10.0, lat 50.0) -> Should be filtered out
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [10.0, 50.0],
                    },
                    "properties": {
                        "mmsi": 999888777,
                        "sog": 5.0,
                        "time": 1728345600,
                    },
                },
            ],
        }

        records = plugin.parse_data(geojson_payload, self.bbox)
        self.assertEqual(len(records), 1)

        rec = records[0]
        self.assertEqual(rec.vessel.mmsi, "230111222")
        self.assertEqual(rec.vessel.name, "NORDIC RUNNER")
        self.assertEqual(rec.vessel.vessel_type, "Cargo")
        self.assertEqual(rec.vessel.callsign, "OG1234")
        self.assertEqual(rec.vessel.imo, "9123456")

        self.assertEqual(rec.position.mmsi, "230111222")
        self.assertAlmostEqual(rec.position.longitude, 24.95)
        self.assertAlmostEqual(rec.position.latitude, 60.15)
        self.assertAlmostEqual(rec.position.speed, 14.5)
        self.assertAlmostEqual(rec.position.heading, 94.0)
        self.assertEqual(rec.position.timestamp, datetime.fromtimestamp(1728345600, tz=timezone.utc))

    def test_parse_temporal_filtering(self):
        plugin = DigiTrafficPlugin()
        t1 = 1728345000  # before window
        t2 = 1728345600  # inside window
        t3 = 1728346200  # after window

        start_dt = datetime.fromtimestamp(1728345500, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(1728345700, tz=timezone.utc)

        features = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [25.0, 60.0]}, "properties": {"mmsi": 1, "time": t1}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [25.0, 60.0]}, "properties": {"mmsi": 2, "time": t2}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [25.0, 60.0]}, "properties": {"mmsi": 3, "time": t3}},
        ]
        payload = {"type": "FeatureCollection", "features": features}

        records = plugin.parse_data(payload, self.bbox, time_range=(start_dt, end_dt))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].vessel.mmsi, "2")

    def test_fetch_with_mock_session(self):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [24.5, 60.0]},
                    "properties": {"mmsi": 230555666, "sog": 10.0, "heading": 180, "time": 1728345600},
                }
            ],
        }
        mock_session.get.return_value = mock_response

        plugin = DigiTrafficPlugin(session=mock_session, load_metadata=False)
        records = plugin.fetch(self.bbox)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].vessel.mmsi, "230555666")
        self.assertAlmostEqual(records[0].position.latitude, 60.0)
        self.assertAlmostEqual(records[0].position.longitude, 24.5)


if __name__ == "__main__":
    unittest.main()
