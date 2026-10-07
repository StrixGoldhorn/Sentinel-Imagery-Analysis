"""Unit tests for Global Fishing Watch (GFW) API client and AIS plugin."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.ais.plugins.global_fishing_watch import GlobalFishingWatchPlugin


class TestGlobalFishingWatchPlugin(unittest.TestCase):
    def setUp(self):
        self.bbox = BoundingBox(
            min_longitude=100.0,
            min_latitude=1.0,
            max_longitude=105.0,
            max_latitude=5.0,
        )

    def test_registry_includes_gfw_plugin(self):
        registry = DynamicAISPluginRegistry()
        plugins = registry.get_plugins("GlobalFishingWatchPlugin")
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].name, "GlobalFishingWatchPlugin")
        meta = registry.get_plugin_metadata("GlobalFishingWatchPlugin")
        self.assertEqual(meta["category"], "Intelligence API")
        self.assertTrue(meta["default_enabled"])

    def test_configure(self):
        plugin = GlobalFishingWatchPlugin()
        plugin.configure({
            "api_token": "test-token-12345",
            "timeout": 45.0,
            "proxy_url": "http://127.0.0.1:8080",
            "user_agent": "GFWClient/1.0",
        })
        self.assertEqual(plugin.api_token, "test-token-12345")
        self.assertEqual(plugin._timeout, 45.0)
        self.assertEqual(plugin.user_agent, "GFWClient/1.0")
        self.assertEqual(plugin.proxy_url, "http://127.0.0.1:8080")

    def test_parse_events_to_ais_records(self):
        plugin = GlobalFishingWatchPlugin(api_token="dummy-token")

        events_payload = {
            "entries": [
                {
                    "id": "evt-001",
                    "vessel": {
                        "ssvid": "412000111",
                        "name": "OCEAN HARVEST",
                        "type": "Trawler",
                        "callsign": "BF123",
                        "imo": "8765432",
                    },
                    "position": {
                        "lat": 3.5,
                        "lon": 102.0,
                    },
                    "start": "2026-10-01T12:00:00Z",
                    "speed": 8.5,
                },
                {
                    # Outside bounding box (lat 50.0, lon 10.0) -> Filtered out
                    "id": "evt-002",
                    "vessel": {
                        "ssvid": "999888777",
                        "name": "FAR AWAY",
                    },
                    "position": {
                        "lat": 50.0,
                        "lon": 10.0,
                    },
                    "start": "2026-10-01T12:00:00Z",
                },
            ]
        }

        records = plugin.parse_events(events_payload, self.bbox)
        self.assertEqual(len(records), 1)

        rec = records[0]
        self.assertEqual(rec.vessel.mmsi, "412000111")
        self.assertEqual(rec.vessel.name, "OCEAN HARVEST")
        self.assertEqual(rec.vessel.vessel_type, "Trawler")
        self.assertEqual(rec.vessel.imo, "8765432")
        self.assertEqual(rec.vessel.callsign, "BF123")

        self.assertAlmostEqual(rec.position.latitude, 3.5)
        self.assertAlmostEqual(rec.position.longitude, 102.0)
        self.assertAlmostEqual(rec.position.speed, 8.5)
        self.assertEqual(rec.position.timestamp, datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_fetch_ais_gaps_identifies_intentional_disabling(self):
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entries": [
                {
                    "id": "gap-991",
                    "vessel": {
                        "ssvid": "413999888",
                        "name": "DARK PROWLER",
                    },
                    "position": {
                        "lat": 2.5,
                        "lon": 103.5,
                    },
                    "start": "2026-10-05T02:00:00Z",
                    "end": "2026-10-05T14:30:00Z",
                    "durationHours": 12.5,
                    "gap_intentional_disabling": True,
                },
                {
                    # Outside bounding box
                    "id": "gap-992",
                    "vessel": {"ssvid": "111"},
                    "position": {"lat": -20.0, "lon": 50.0},
                },
            ]
        }
        mock_session.get.return_value = mock_response

        plugin = GlobalFishingWatchPlugin(api_token="test-token", session=mock_session)
        gaps = plugin.fetch_ais_gaps(
            self.bbox,
            start_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual(gap["event_id"], "gap-991")
        self.assertEqual(gap["mmsi"], "413999888")
        self.assertEqual(gap["vessel_name"], "DARK PROWLER")
        self.assertAlmostEqual(gap["latitude"], 2.5)
        self.assertAlmostEqual(gap["longitude"], 103.5)
        self.assertAlmostEqual(gap["duration_hours"], 12.5)
        self.assertTrue(gap["is_intentional"])

    def test_fetch_without_token_returns_empty_gracefully(self):
        plugin = GlobalFishingWatchPlugin(api_token=None)
        records = plugin.fetch(self.bbox)
        self.assertEqual(records, [])
        gaps = plugin.fetch_ais_gaps(self.bbox)
        self.assertEqual(gaps, [])


if __name__ == "__main__":
    unittest.main()
