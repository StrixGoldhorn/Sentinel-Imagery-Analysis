"""Unit and contract tests for SeaSentry AIS scrapers."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from sentinel_analysis.application.ports.ais import AISPlugin
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.domain.entities import AISRecord, BoundingBox
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.ais.plugins import (
    AISFriendsPlugin,
    AprsFiPlugin,
    MockAISPlugin,
    MockPublicAISPlugin,
    UDPListenerPlugin,
    VesselFinderPlugin,
)
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository


BBOX = BoundingBox(103.80, 1.20, 103.90, 1.30)


class ScrapersTestSuite(unittest.TestCase):
    def test_all_plugins_conform_to_ais_plugin_protocol(self) -> None:
        plugins = [
            AISFriendsPlugin(),
            VesselFinderPlugin(),
            AprsFiPlugin(),
            UDPListenerPlugin(),
            MockAISPlugin(),
            MockPublicAISPlugin(),
        ]
        for plugin in plugins:
            with self.subTest(plugin=plugin.name):
                self.assertIsInstance(plugin, AISPlugin)
                self.assertIsNone(plugin.authenticate())

    def test_dynamic_registry_registers_all_six_plugins_by_default(self) -> None:
        registry = DynamicAISPluginRegistry()
        plugins = registry.get_plugins()
        names = [p.name for p in plugins]

        expected = [
            "MockAISPlugin",
            "MockPublicAISPlugin",
            "AISFriendsPlugin",
            "VesselFinderPlugin",
            "AprsFiPlugin",
            "UDPListenerPlugin",
        ]
        self.assertEqual(names, expected)

        for name in expected:
            selected = registry.get_plugins(name)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0].name, name)

    def test_ais_friends_ship_type_mapping(self) -> None:
        self.assertEqual(AISFriendsPlugin.get_ship_type(30), "Fishing")
        self.assertEqual(AISFriendsPlugin.get_ship_type(31), "Tug")
        self.assertEqual(AISFriendsPlugin.get_ship_type(35), "Military")
        self.assertEqual(AISFriendsPlugin.get_ship_type(51), "SAR")
        self.assertEqual(AISFriendsPlugin.get_ship_type(70), "Cargo")
        self.assertEqual(AISFriendsPlugin.get_ship_type(85), "Tanker")
        self.assertIsNone(AISFriendsPlugin.get_ship_type(999))
        self.assertIsNone(AISFriendsPlugin.get_ship_type(None))

    def test_ais_friends_plugin_fetch_and_parse(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "mmsi": 566123456,
                "imo": 9123456,
                "name_ais": "EVER GIVEN",
                "ship_type_id": 70,
                "latitude": 1.25,
                "longitude": 103.85,
                "timestamp_of_position": 1700000000,
                "speed_over_ground": 14.2,
                "true_heading": 180,
                "callsign": "9V1234",
            },
            {
                "mmsi": "566789012",
                "imo": None,
                "name": "OCEAN TUG 1",
                "ship_type_id": 31,
                "latitude": 1.28,
                "longitude": 103.82,
                "timestamp_of_position": None,
                "speed_over_ground": 5.0,
                "true_heading": 90,
            },
        ]
        mock_response.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        plugin = AISFriendsPlugin(session=mock_session)
        records = plugin.fetch(BBOX)

        self.assertEqual(len(records), 2)
        r1, r2 = records

        self.assertEqual(r1.vessel.mmsi, "566123456")
        self.assertEqual(r1.vessel.imo, "9123456")
        self.assertEqual(r1.vessel.name, "EVER GIVEN")
        self.assertEqual(r1.vessel.vessel_type, "Cargo")
        self.assertEqual(r1.vessel.callsign, "9V1234")
        self.assertAlmostEqual(r1.position.latitude, 1.25)
        self.assertAlmostEqual(r1.position.longitude, 103.85)
        self.assertAlmostEqual(r1.position.speed, 14.2)
        self.assertAlmostEqual(r1.position.heading, 180.0)

        self.assertEqual(r2.vessel.mmsi, "566789012")
        self.assertEqual(r2.vessel.imo, "UNKNOWN-566789012")
        self.assertEqual(r2.vessel.name, "OCEAN TUG 1")
        self.assertEqual(r2.vessel.vessel_type, "Tug")

    def test_vessel_finder_binary_decoding(self) -> None:
        plugin = VesselFinderPlugin()

        # Build a valid binary packet matching VesselFinder mp2 schema:
        # Header (12 bytes):
        header = b"012345678901"

        # Record 1:
        # flags (2 bytes): ship_type 4 (Cargo) -> (4 << 4) = 0x0040 = 64
        flags = (4 << 4).to_bytes(2, "big")
        mmsi = (563001234).to_bytes(4, "big")
        lat = round(1.245 * 600000).to_bytes(4, "big", signed=True)
        lon = round(103.835 * 600000).to_bytes(4, "big", signed=True)
        time_delta = (5).to_bytes(1, "big")  # 5 minutes ago
        ship_name = "SINGAPORE STAR".encode("utf-8")
        name_len = len(ship_name).to_bytes(1, "big")
        zoom_padding = b"\x00" * 10  # zoom 15 has 10 bytes padding

        blob = header + flags + mmsi + lat + lon + time_delta + name_len + ship_name + zoom_padding

        records = plugin.parse_data(blob)
        self.assertEqual(len(records), 1)

        rec = records[0]
        self.assertEqual(rec.vessel.mmsi, "563001234")
        self.assertEqual(rec.vessel.name, "SINGAPORE STAR")
        self.assertEqual(rec.vessel.vessel_type, "Cargo")
        self.assertAlmostEqual(rec.position.latitude, 1.245, places=4)
        self.assertAlmostEqual(rec.position.longitude, 103.835, places=4)

    def test_vessel_finder_fetch_with_mocked_session(self) -> None:
        header = b"012345678901"
        flags = (6 << 4).to_bytes(2, "big")  # Tanker
        mmsi = (211009876).to_bytes(4, "big")
        lat = round(1.26 * 600000).to_bytes(4, "big", signed=True)
        lon = round(103.86 * 600000).to_bytes(4, "big", signed=True)
        time_delta = (0).to_bytes(1, "big")
        ship_name = "NORDIC TANKER".encode("utf-8")
        name_len = len(ship_name).to_bytes(1, "big")
        zoom_padding = b"\x00" * 10

        mock_blob = header + flags + mmsi + lat + lon + time_delta + name_len + ship_name + zoom_padding

        mock_session = MagicMock()
        mock_session.fetch_mp2.return_value = mock_blob

        plugin = VesselFinderPlugin(session_factory=lambda: mock_session)
        records = plugin.fetch(BBOX)

        self.assertGreaterEqual(len(records), 1)
        self.assertEqual(records[0].vessel.mmsi, "211009876")
        self.assertEqual(records[0].vessel.name, "NORDIC TANKER")
        self.assertEqual(records[0].vessel.vessel_type, "Tanker")
        mock_session.cleanup.assert_called_once()

    def test_aprs_fi_xml2_parsing(self) -> None:
        plugin = AprsFiPlugin()
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
        <response>
            <item>it({"name": "563999888", "showname": "HARBOR PILOT", "lat": 1.275, "lng": 103.845, "time": 1700000000, "speed": 10.5, "course": 270});</item>
            <item>it({"name": "564111222", "showname": "CONTAINER 9", "lat": 1.22, "lng": 103.88, "time": 1700000100, "speed": 18.0, "course": 45});</item>
        </response>
        """

        records = plugin.parse_data(xml_content)
        self.assertEqual(len(records), 2)

        r1, r2 = records
        self.assertEqual(r1.vessel.mmsi, "563999888")
        self.assertEqual(r1.vessel.name, "HARBOR PILOT")
        self.assertAlmostEqual(r1.position.latitude, 1.275)
        self.assertAlmostEqual(r1.position.longitude, 103.845)
        self.assertAlmostEqual(r1.position.speed, 10.5)
        self.assertAlmostEqual(r1.position.heading, 270.0)

        self.assertEqual(r2.vessel.mmsi, "564111222")
        self.assertEqual(r2.vessel.name, "CONTAINER 9")

    def test_aprs_fi_fetch_with_mocked_session(self) -> None:
        xml_payload = """<response><item>it({"name": "999888777", "showname": "FERRY ONE", "lat": 1.25, "lng": 103.85, "time": 1700000000});</item></response>"""
        mock_session = MagicMock()
        mock_session.fetch_xml2.return_value = xml_payload

        plugin = AprsFiPlugin(session_factory=lambda: mock_session)
        records = plugin.fetch(BBOX)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].vessel.mmsi, "999888777")
        self.assertEqual(records[0].vessel.name, "FERRY ONE")
        mock_session.cleanup.assert_called_once()

    def test_udp_listener_sentence_push_and_bounding_box_filter(self) -> None:
        plugin = UDPListenerPlugin(auto_start=False)

        # Standard NMEA AIVDM Position Report Sentence (MMSI 227006760, lat ~48.8, lon ~-3.0)
        nmea_outside = "!AIVDM,1,1,,B,133s:V00000n9chFn6<h00000000,0*38"

        # Static Voyage report updating MMSI 563111222 name to "SENTINEL PATROL"
        # Type 24 or direct push
        plugin.push_message(nmea_outside)

        # Inside BBOX mock decode injection
        rec_inside = AISRecord(
            vessel=MagicMock(imo="9999999", mmsi="563111222", name="SENTINEL PATROL", vessel_type="Patrol", callsign=None),
            position=MagicMock(mmsi="563111222", latitude=1.25, longitude=103.85, timestamp=datetime.now(timezone.utc), speed=12.0, heading=90.0),
        )

        # Ensure fetch filters coordinates properly
        with unittest.mock.patch.object(plugin, "parse_data", return_value=[rec_inside]):
            results = plugin.fetch(BBOX)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].vessel.mmsi, "563111222")

    def test_end_to_end_ingestion_with_sqlite_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test_ais.db"
            repo = SQLiteAISRepository(db_path)

            mock_response = MagicMock()
            mock_response.json.return_value = [
                {
                    "mmsi": 566111222,
                    "imo": 9876543,
                    "name_ais": "MAERSK TEST",
                    "ship_type_id": 70,
                    "latitude": 1.255,
                    "longitude": 103.845,
                    "timestamp_of_position": 1700000000,
                    "speed_over_ground": 15.5,
                    "true_heading": 210,
                }
            ]
            mock_response.raise_for_status = MagicMock()
            mock_session = MagicMock()
            mock_session.get.return_value = mock_response

            plugin = AISFriendsPlugin(session=mock_session)
            registry = DynamicAISPluginRegistry([plugin])

            ingest_use_case = IngestAIS(registry, repo)
            result = ingest_use_case.execute(BBOX, (None, None), plugin_name="AISFriendsPlugin")

            self.assertEqual(result["total_inserted"], 1)
            self.assertEqual(result["logs"][0]["status"], "SUCCESS")

            with sqlite3.connect(db_path) as conn:
                vessels = conn.execute("SELECT imo, mmsi, vessel_name, vessel_type FROM vessels").fetchall()
                self.assertEqual(len(vessels), 1)
                self.assertEqual(vessels[0], ("9876543", "566111222", "MAERSK TEST", "Cargo"))

                locations = conn.execute("SELECT latitude, longitude, speed, source_plugin FROM vessel_locations").fetchall()
                self.assertEqual(len(locations), 1)
                self.assertAlmostEqual(locations[0][0], 1.255)
                self.assertEqual(locations[0][3], "AISFriendsPlugin")

    def test_ais_friends_time_window_filtering(self) -> None:
        pass_time = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
        from sentinel_analysis.application.use_cases.scrape_aoi_ais import calculate_pass_window
        time_range = calculate_pass_window(pass_time, window_minutes=5)
        # Window is [11:55:00, 12:05:00]

        items = [
            # 11:50:00 (10 min before pass -> outside)
            {"mmsi": 111, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 11, 50, 0, tzinfo=timezone.utc).timestamp())},
            # 11:57:00 (3 min before pass -> inside)
            {"mmsi": 222, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 11, 57, 0, tzinfo=timezone.utc).timestamp())},
            # 12:02:00 (2 min after pass -> inside)
            {"mmsi": 333, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 12, 2, 0, tzinfo=timezone.utc).timestamp())},
            # 12:10:00 (10 min after pass -> outside)
            {"mmsi": 444, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 12, 10, 0, tzinfo=timezone.utc).timestamp())},
        ]

        plugin = AISFriendsPlugin()
        records = plugin.parse_data(items, time_range)
        mmsis = [r.vessel.mmsi for r in records]
        self.assertEqual(mmsis, ["222", "333"])

    def test_aprs_fi_time_window_filtering(self) -> None:
        pass_time = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
        from sentinel_analysis.application.use_cases.scrape_aoi_ais import calculate_pass_window
        time_range = calculate_pass_window(pass_time, window_minutes=5)

        t_inside = int(datetime(2026, 8, 30, 11, 58, 0, tzinfo=timezone.utc).timestamp())
        t_outside = int(datetime(2026, 8, 30, 12, 15, 0, tzinfo=timezone.utc).timestamp())

        xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
        <response>
            <item>it({{"name": "563999888", "showname": "INSIDE", "lat": 1.25, "lng": 103.85, "time": {t_inside}}});</item>
            <item>it({{"name": "564111222", "showname": "OUTSIDE", "lat": 1.25, "lng": 103.85, "time": {t_outside}}});</item>
        </response>
        """
        plugin = AprsFiPlugin()
        records = plugin.parse_data(xml_content, time_range)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].vessel.mmsi, "563999888")

    def test_scrape_aoi_ais_use_case(self) -> None:
        from sentinel_analysis.application.use_cases.scrape_aoi_ais import ScrapeAreaOfInterestAIS
        from sentinel_analysis.domain.entities import AreaOfInterest

        pass_time = datetime(2026, 8, 30, 14, 0, 0, tzinfo=timezone.utc)
        aoi = AreaOfInterest("Singapore Strait", BBOX, id=1, next_scan=pass_time)

        mock_aoi_repo = MagicMock()
        mock_aoi_repo.get.return_value = aoi

        mock_ingest = MagicMock()
        mock_ingest.execute.return_value = {"total_inserted": 5, "logs": []}

        use_case = ScrapeAreaOfInterestAIS(mock_aoi_repo, mock_ingest)
        result = use_case.execute(aoi_id=1)

        self.assertEqual(result["total_inserted"], 5)
        mock_ingest.execute.assert_called_once()
        args = mock_ingest.execute.call_args[0]
        self.assertEqual(args[0], BBOX)
        # Expected window: 13:55:00 to 14:05:00
        start_w, end_w = args[1]
        self.assertEqual(start_w, datetime(2026, 8, 30, 13, 55, 0, tzinfo=timezone.utc))
        self.assertEqual(end_w, datetime(2026, 8, 30, 14, 5, 0, tzinfo=timezone.utc))

    def test_scrape_aoi_ais_force_scan_ignores_pass_time(self) -> None:
        from sentinel_analysis.application.use_cases.scrape_aoi_ais import ScrapeAreaOfInterestAIS
        from sentinel_analysis.domain.entities import AreaOfInterest

        # Next scan is far in the future
        pass_time = datetime(2026, 9, 15, 14, 0, 0, tzinfo=timezone.utc)
        aoi = AreaOfInterest("Singapore Strait", BBOX, id=1, next_scan=pass_time)

        mock_aoi_repo = MagicMock()
        mock_aoi_repo.get.return_value = aoi

        mock_ingest = MagicMock()
        mock_ingest.execute.return_value = {"total_inserted": 12, "logs": []}

        use_case = ScrapeAreaOfInterestAIS(mock_aoi_repo, mock_ingest)
        result = use_case.execute(aoi_id=1, force_now=True)

        self.assertEqual(result["total_inserted"], 12)
        mock_ingest.execute.assert_called_once()
        args = mock_ingest.execute.call_args[0]
        self.assertEqual(args[0], BBOX)
        # Should NOT use the future 2026-09-15 date; should use live current time window
        start_w, end_w = args[1]
        self.assertNotEqual(start_w.year, 2026 if start_w.month == 9 and start_w.day == 15 else -1)
        self.assertTrue((end_w - start_w).total_seconds() > 0)

    def test_sqlite_ais_get_vessel_positions_filtering(self) -> None:
        from sentinel_analysis.application.use_cases.get_vessels import GetVesselPositions
        from sentinel_analysis.domain.entities import Vessel, VesselPosition

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test_ais.db"
            repo = SQLiteAISRepository(db_path)

            # Insert 2 vessels
            vessel1 = Vessel(imo="9123456", mmsi="563000111", name="PACIFIC TRADER", vessel_type="Cargo", callsign="9V123")
            pos1_old = VesselPosition(mmsi="563000111", latitude=1.25, longitude=103.85, timestamp=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc), speed=10.5, heading=90.0)
            pos1_new = VesselPosition(mmsi="563000111", latitude=1.26, longitude=103.86, timestamp=datetime(2026, 8, 30, 10, 5, tzinfo=timezone.utc), speed=12.0, heading=95.0)

            vessel2 = Vessel(imo="9654321", mmsi="563000222", name="OCEAN TANKER", vessel_type="Tanker", callsign="9V456")
            pos2 = VesselPosition(mmsi="563000222", latitude=2.50, longitude=104.50, timestamp=datetime(2026, 8, 30, 10, 3, tzinfo=timezone.utc), speed=14.0, heading=180.0)

            repo.save_records([AISRecord(vessel=vessel1, position=pos1_old)], "TestPlugin")
            repo.save_records([AISRecord(vessel=vessel1, position=pos1_new)], "TestPlugin")
            repo.save_records([AISRecord(vessel=vessel2, position=pos2)], "TestPlugin")

            # Query all latest
            all_latest = repo.get_vessel_positions(latest_only=True)
            self.assertEqual(len(all_latest), 2)
            # Check PACIFIC TRADER returns the newer position
            trader = next(v for v in all_latest if v["mmsi"] == "563000111")
            self.assertAlmostEqual(trader["latitude"], 1.26)
            self.assertAlmostEqual(trader["speed"], 12.0)
            self.assertEqual(trader["name"], "PACIFIC TRADER")
            self.assertEqual(trader["type"], "Cargo")

            # Query with bbox filtering (matching only vessel1)
            bbox_sg = BoundingBox(103.80, 1.20, 103.90, 1.30)
            sg_vessels = repo.get_vessel_positions(bbox=bbox_sg, latest_only=True)
            self.assertEqual(len(sg_vessels), 1)
            self.assertEqual(sg_vessels[0]["mmsi"], "563000111")

            # Use Case execution
            use_case = GetVesselPositions(repo)
            uc_results = use_case.execute(bbox=bbox_sg)
            self.assertEqual(len(uc_results), 1)
            self.assertEqual(uc_results[0]["name"], "PACIFIC TRADER")

            # Timeline bounds
            bounds = repo.get_timeline_bounds()
            self.assertEqual(bounds["count"], 3)
            self.assertIsNotNone(bounds["min_timestamp"])
            self.assertIsNotNone(bounds["max_timestamp"])

            # Time range query
            t_early = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
            t_mid = datetime(2026, 8, 30, 10, 10, tzinfo=timezone.utc)
            early_vessels = repo.get_vessel_positions(time_range=(t_early, t_mid), latest_only=True)
            self.assertEqual(len(early_vessels), 2)
            trader_early = next(v for v in early_vessels if v["mmsi"] == "563000111")
            # Should have the 10:00 position, speed 10.5
            self.assertAlmostEqual(trader_early["speed"], 10.5)


    def test_bounding_box_split_into_zones(self) -> None:
        from sentinel_analysis.infrastructure.ais.zone_splitter import split_into_zones

        # Small bbox (5 NM x 5 NM) -> 1 zone
        small_bbox = BoundingBox(103.80, 1.20, 103.85, 1.25)
        small_zones = split_into_zones(small_bbox, zone_size_nm=10.0)
        self.assertEqual(len(small_zones), 1)
        self.assertEqual(small_zones[0], small_bbox)

        # Large bbox (spanning ~0.6 degrees lat x ~0.6 degrees lon, ~36 NM x ~36 NM) -> 4x4 = 16 zones
        large_bbox = BoundingBox(103.50, 1.00, 104.10, 1.60)
        large_zones = split_into_zones(large_bbox, zone_size_nm=10.0)
        self.assertGreater(len(large_zones), 1)
        # Ensure all zones combined span the whole original bbox
        min_lon = min(z.min_longitude for z in large_zones)
        min_lat = min(z.min_latitude for z in large_zones)
        max_lon = max(z.max_longitude for z in large_zones)
        max_lat = max(z.max_latitude for z in large_zones)
        self.assertAlmostEqual(min_lon, large_bbox.min_longitude)
        self.assertAlmostEqual(min_lat, large_bbox.min_latitude)
        self.assertAlmostEqual(max_lon, large_bbox.max_longitude)
        self.assertAlmostEqual(max_lat, large_bbox.max_latitude)

    def test_deduplicate_ais_records(self) -> None:
        from sentinel_analysis.domain.entities import Vessel, VesselPosition
        from sentinel_analysis.infrastructure.ais.zone_splitter import deduplicate_ais_records

        vessel = Vessel(imo="9123456", mmsi="563000111", name="VESSEL A", vessel_type="Cargo", callsign=None)
        rec_old = AISRecord(vessel=vessel, position=VesselPosition(mmsi="563000111", latitude=1.2, longitude=103.8, timestamp=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc), speed=10, heading=90))
        rec_new = AISRecord(vessel=vessel, position=VesselPosition(mmsi="563000111", latitude=1.21, longitude=103.81, timestamp=datetime(2026, 8, 30, 10, 5, tzinfo=timezone.utc), speed=11, heading=95))

        deduped = deduplicate_ais_records([rec_old, rec_new])
        self.assertEqual(len(deduped), 1)
        self.assertAlmostEqual(deduped[0].position.latitude, 1.21)

    def test_ais_friends_multi_zone_scraping(self) -> None:
        # Large bounding box spanning multiple 10 NM zones
        large_bbox = BoundingBox(103.50, 1.00, 104.10, 1.60)
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "mmsi": 566123456,
                "name_ais": "ZONE VESSEL",
                "ship_type_id": 70,
                "latitude": 1.25,
                "longitude": 103.85,
                "timestamp_of_position": 1700000000,
            }
        ]
        mock_response.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_response

        plugin = AISFriendsPlugin(session=mock_session)
        records = plugin.fetch(large_bbox)

        # Multiple sub-zones queried
        self.assertGreater(mock_session.get.call_count, 1)
        # Records properly aggregated and deduplicated
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].vessel.name, "ZONE VESSEL")


if __name__ == "__main__":
    unittest.main()


