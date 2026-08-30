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


class InMemoryAISRepository:
    def __init__(self):
        self.vessels = {}
        self.locations = []
        self.logs = []

    def save_records(self, records, source_plugin: str) -> int:
        records_list = list(records)
        for rec in records_list:
            self.vessels[rec.vessel.mmsi] = {
                "imo": rec.vessel.imo,
                "mmsi": rec.vessel.mmsi,
                "vessel_name": rec.vessel.name,
                "vessel_type": rec.vessel.vessel_type,
                "callsign": rec.vessel.callsign,
            }
            self.locations.append({
                "mmsi": rec.position.mmsi,
                "latitude": rec.position.latitude,
                "longitude": rec.position.longitude,
                "speed": rec.position.speed,
                "heading": rec.position.heading,
                "timestamp": rec.position.timestamp,
                "source_plugin": source_plugin,
            })
        return len(records_list)

    def log_execution(self, plugin_name: str, status: str, records_inserted: int, error_message: str | None = None) -> None:
        self.logs.append((plugin_name, status, records_inserted, error_message))

    def get_vessel_positions(
        self,
        bbox: BoundingBox | None = None,
        time_range: tuple[datetime, datetime] | None = None,
        latest_only: bool = True,
        limit: int = 500,
    ) -> list[dict]:
        locs = list(self.locations)
        if bbox is not None:
            locs = [
                l for l in locs
                if bbox.min_latitude <= l["latitude"] <= bbox.max_latitude
                and bbox.min_longitude <= l["longitude"] <= bbox.max_longitude
            ]
        if time_range is not None:
            t_start, t_end = time_range
            locs = [l for l in locs if (t_start is None or l["timestamp"] >= t_start) and (t_end is None or l["timestamp"] <= t_end)]

        if latest_only:
            by_mmsi = {}
            for l in locs:
                mmsi = l["mmsi"]
                if mmsi not in by_mmsi or l["timestamp"] > by_mmsi[mmsi]["timestamp"]:
                    by_mmsi[mmsi] = l
            locs = list(by_mmsi.values())

        locs.sort(key=lambda l: l["timestamp"], reverse=True)
        locs = locs[:limit]

        result = []
        for idx, l in enumerate(locs, start=1):
            v = self.vessels.get(l["mmsi"], {})
            ts_str = l["timestamp"].isoformat() if isinstance(l["timestamp"], datetime) else str(l["timestamp"])
            result.append({
                "location_id": idx,
                "vessel_id": idx,
                "mmsi": l["mmsi"],
                "imo": v.get("imo"),
                "name": v.get("vessel_name"),
                "type": v.get("vessel_type"),
                "callsign": v.get("callsign"),
                "latitude": l["latitude"],
                "longitude": l["longitude"],
                "speed": l["speed"],
                "heading": l["heading"],
                "timestamp": ts_str,
                "source_plugin": l["source_plugin"],
            })
        return result

    def get_timeline_bounds(self) -> dict:
        if not self.locations:
            return {"min_timestamp": None, "max_timestamp": None, "total_records": 0, "count": 0}
        timestamps = [l["timestamp"] for l in self.locations if l["timestamp"]]
        min_ts = min(timestamps).isoformat() if timestamps else None
        max_ts = max(timestamps).isoformat() if timestamps else None
        total = len(self.locations)
        return {
            "min_timestamp": min_ts,
            "max_timestamp": max_ts,
            "total_records": total,
            "count": total,
        }


def test_all_plugins_conform_to_ais_plugin_protocol() -> None:
    plugins = [
        AISFriendsPlugin(),
        VesselFinderPlugin(),
        AprsFiPlugin(),
        UDPListenerPlugin(),
        MockAISPlugin(),
        MockPublicAISPlugin(),
    ]
    for plugin in plugins:
        assert isinstance(plugin, AISPlugin)
        assert plugin.authenticate() is None


def test_dynamic_registry_registers_all_six_plugins_by_default() -> None:
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
    assert names == expected

    for name in expected:
        selected = registry.get_plugins(name)
        assert len(selected) == 1
        assert selected[0].name == name


def test_ais_friends_ship_type_mapping() -> None:
    assert AISFriendsPlugin.get_ship_type(30) == "Fishing"
    assert AISFriendsPlugin.get_ship_type(31) == "Tug"
    assert AISFriendsPlugin.get_ship_type(35) == "Military"
    assert AISFriendsPlugin.get_ship_type(51) == "SAR"
    assert AISFriendsPlugin.get_ship_type(70) == "Cargo"
    assert AISFriendsPlugin.get_ship_type(85) == "Tanker"
    assert AISFriendsPlugin.get_ship_type(999) is None
    assert AISFriendsPlugin.get_ship_type(None) is None


def test_ais_friends_plugin_fetch_and_parse() -> None:
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

    assert len(records) == 2
    r1, r2 = records

    assert r1.vessel.mmsi == "566123456"
    assert r1.vessel.imo == "9123456"
    assert r1.vessel.name == "EVER GIVEN"
    assert r1.vessel.vessel_type == "Cargo"
    assert r1.vessel.callsign == "9V1234"
    assert abs(r1.position.latitude - 1.25) < 1e-4
    assert abs(r1.position.longitude - 103.85) < 1e-4
    assert abs(r1.position.speed - 14.2) < 1e-4
    assert abs(r1.position.heading - 180.0) < 1e-4

    assert r2.vessel.mmsi == "566789012"
    assert r2.vessel.imo == "UNKNOWN-566789012"
    assert r2.vessel.name == "OCEAN TUG 1"
    assert r2.vessel.vessel_type == "Tug"


def test_vessel_finder_binary_decoding() -> None:
    plugin = VesselFinderPlugin()

    # Build a valid binary packet matching VesselFinder mp2 schema:
    header = b"012345678901"
    flags = (4 << 4).to_bytes(2, "big")
    mmsi = (563001234).to_bytes(4, "big")
    lat = round(1.245 * 600000).to_bytes(4, "big", signed=True)
    lon = round(103.835 * 600000).to_bytes(4, "big", signed=True)
    time_delta = (5).to_bytes(1, "big")
    ship_name = "SINGAPORE STAR".encode("utf-8")
    name_len = len(ship_name).to_bytes(1, "big")
    zoom_padding = b"\x00" * 10

    blob = header + flags + mmsi + lat + lon + time_delta + name_len + ship_name + zoom_padding

    records = plugin.parse_data(blob)
    assert len(records) == 1

    rec = records[0]
    assert rec.vessel.mmsi == "563001234"
    assert rec.vessel.name == "SINGAPORE STAR"
    assert rec.vessel.vessel_type == "Cargo"
    assert abs(rec.position.latitude - 1.245) < 1e-3
    assert abs(rec.position.longitude - 103.835) < 1e-3


def test_vessel_finder_fetch_with_mocked_session() -> None:
    header = b"012345678901"
    flags = (6 << 4).to_bytes(2, "big")
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

    assert len(records) >= 1
    assert records[0].vessel.mmsi == "211009876"
    assert records[0].vessel.name == "NORDIC TANKER"
    assert records[0].vessel.vessel_type == "Tanker"
    mock_session.cleanup.assert_called_once()


def test_aprs_fi_xml2_parsing() -> None:
    plugin = AprsFiPlugin()
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
    <response>
        <item>it({"name": "563999888", "showname": "HARBOR PILOT", "lat": 1.275, "lng": 103.845, "time": 1700000000, "speed": 10.5, "course": 270});</item>
        <item>it({"name": "564111222", "showname": "CONTAINER 9", "lat": 1.22, "lng": 103.88, "time": 1700000100, "speed": 18.0, "course": 45});</item>
    </response>
    """

    records = plugin.parse_data(xml_content)
    assert len(records) == 2

    r1, r2 = records
    assert r1.vessel.mmsi == "563999888"
    assert r1.vessel.name == "HARBOR PILOT"
    assert abs(r1.position.latitude - 1.275) < 1e-4
    assert abs(r1.position.longitude - 103.845) < 1e-4
    assert abs(r1.position.speed - 10.5) < 1e-4
    assert abs(r1.position.heading - 270.0) < 1e-4

    assert r2.vessel.mmsi == "564111222"
    assert r2.vessel.name == "CONTAINER 9"


def test_aprs_fi_fetch_with_mocked_session() -> None:
    xml_payload = """<response><item>it({"name": "999888777", "showname": "FERRY ONE", "lat": 1.25, "lng": 103.85, "time": 1700000000});</item></response>"""
    mock_session = MagicMock()
    mock_session.fetch_xml2.return_value = xml_payload

    plugin = AprsFiPlugin(session_factory=lambda: mock_session)
    records = plugin.fetch(BBOX)

    assert len(records) == 1
    assert records[0].vessel.mmsi == "999888777"
    assert records[0].vessel.name == "FERRY ONE"
    mock_session.cleanup.assert_called_once()


def test_udp_listener_sentence_push_and_bounding_box_filter() -> None:
    plugin = UDPListenerPlugin(auto_start=False)

    nmea_outside = "!AIVDM,1,1,,B,133s:V00000n9chFn6<h00000000,0*38"
    plugin.push_message(nmea_outside)

    rec_inside = AISRecord(
        vessel=MagicMock(imo="9999999", mmsi="563111222", name="SENTINEL PATROL", vessel_type="Patrol", callsign=None),
        position=MagicMock(mmsi="563111222", latitude=1.25, longitude=103.85, timestamp=datetime.now(timezone.utc), speed=12.0, heading=90.0),
    )

    with unittest.mock.patch.object(plugin, "parse_data", return_value=[rec_inside]):
        results = plugin.fetch(BBOX)
        assert len(results) == 1
        assert results[0].vessel.mmsi == "563111222"


def test_end_to_end_ingestion_with_in_memory_repository() -> None:
    repo = InMemoryAISRepository()

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

    assert result["total_inserted"] == 1
    assert result["logs"][0]["status"] == "SUCCESS"

    vessels = repo.get_vessel_positions()
    assert len(vessels) == 1
    assert vessels[0]["imo"] == "9876543"
    assert vessels[0]["mmsi"] == "566111222"
    assert vessels[0]["name"] == "MAERSK TEST"
    assert vessels[0]["type"] == "Cargo"
    assert abs(vessels[0]["latitude"] - 1.255) < 1e-4
    assert vessels[0]["source_plugin"] == "AISFriendsPlugin"


def test_ais_friends_time_window_filtering() -> None:
    pass_time = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
    from sentinel_analysis.application.use_cases.scrape_aoi_ais import calculate_pass_window
    time_range = calculate_pass_window(pass_time, window_minutes=5)

    items = [
        {"mmsi": 111, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 11, 50, 0, tzinfo=timezone.utc).timestamp())},
        {"mmsi": 222, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 11, 57, 0, tzinfo=timezone.utc).timestamp())},
        {"mmsi": 333, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 12, 2, 0, tzinfo=timezone.utc).timestamp())},
        {"mmsi": 444, "latitude": 1.25, "longitude": 103.85, "timestamp_of_position": int(datetime(2026, 8, 30, 12, 10, 0, tzinfo=timezone.utc).timestamp())},
    ]

    plugin = AISFriendsPlugin()
    records = plugin.parse_data(items, time_range)
    mmsis = [r.vessel.mmsi for r in records]
    assert mmsis == ["222", "333"]


def test_aprs_fi_time_window_filtering() -> None:
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
    assert len(records) == 1
    assert records[0].vessel.mmsi == "563999888"


def test_scrape_aoi_ais_use_case() -> None:
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

    assert result["total_inserted"] == 5
    mock_ingest.execute.assert_called_once()
    args = mock_ingest.execute.call_args[0]
    assert args[0] == BBOX
    start_w, end_w = args[1]
    assert start_w == datetime(2026, 8, 30, 13, 55, 0, tzinfo=timezone.utc)
    assert end_w == datetime(2026, 8, 30, 14, 5, 0, tzinfo=timezone.utc)


def test_scrape_aoi_ais_force_scan_ignores_pass_time() -> None:
    from sentinel_analysis.application.use_cases.scrape_aoi_ais import ScrapeAreaOfInterestAIS
    from sentinel_analysis.domain.entities import AreaOfInterest

    pass_time = datetime(2026, 9, 15, 14, 0, 0, tzinfo=timezone.utc)
    aoi = AreaOfInterest("Singapore Strait", BBOX, id=1, next_scan=pass_time)

    mock_aoi_repo = MagicMock()
    mock_aoi_repo.get.return_value = aoi

    mock_ingest = MagicMock()
    mock_ingest.execute.return_value = {"total_inserted": 12, "logs": []}

    use_case = ScrapeAreaOfInterestAIS(mock_aoi_repo, mock_ingest)
    result = use_case.execute(aoi_id=1, force_now=True)

    assert result["total_inserted"] == 12
    mock_ingest.execute.assert_called_once()
    args = mock_ingest.execute.call_args[0]
    assert args[0] == BBOX
    start_w, end_w = args[1]
    assert start_w.year != (2026 if start_w.month == 9 and start_w.day == 15 else -1)
    assert (end_w - start_w).total_seconds() > 0


def test_in_memory_ais_get_vessel_positions_filtering() -> None:
    from sentinel_analysis.application.use_cases.get_vessels import GetVesselPositions
    from sentinel_analysis.domain.entities import Vessel, VesselPosition

    repo = InMemoryAISRepository()

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
    assert len(all_latest) == 2
    trader = next(v for v in all_latest if v["mmsi"] == "563000111")
    assert abs(trader["latitude"] - 1.26) < 1e-4
    assert abs(trader["speed"] - 12.0) < 1e-4
    assert trader["name"] == "PACIFIC TRADER"
    assert trader["type"] == "Cargo"

    # Query with bbox filtering (matching only vessel1)
    bbox_sg = BoundingBox(103.80, 1.20, 103.90, 1.30)
    sg_vessels = repo.get_vessel_positions(bbox=bbox_sg, latest_only=True)
    assert len(sg_vessels) == 1
    assert sg_vessels[0]["mmsi"] == "563000111"

    # Use Case execution
    use_case = GetVesselPositions(repo)
    uc_results = use_case.execute(bbox=bbox_sg)
    assert len(uc_results) == 1
    assert uc_results[0]["name"] == "PACIFIC TRADER"

    # Timeline bounds
    bounds = repo.get_timeline_bounds()
    assert bounds["count"] == 3
    assert bounds["min_timestamp"] is not None
    assert bounds["max_timestamp"] is not None

    # Time range query (window 09:00 to 10:02 captures pos1_old at 10:00, excludes pos1_new at 10:05 & pos2 at 10:03)
    t_early = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
    t_mid = datetime(2026, 8, 30, 10, 2, tzinfo=timezone.utc)
    early_vessels = repo.get_vessel_positions(time_range=(t_early, t_mid), latest_only=True)
    assert len(early_vessels) == 1
    trader_early = early_vessels[0]
    assert trader_early["mmsi"] == "563000111"
    assert abs(trader_early["speed"] - 10.5) < 1e-4

    # Window up to 10:10 captures latest for both vessels (10:05 for trader, 10:03 for tanker)
    t_late = datetime(2026, 8, 30, 10, 10, tzinfo=timezone.utc)
    all_in_window = repo.get_vessel_positions(time_range=(t_early, t_late), latest_only=True)
    assert len(all_in_window) == 2
    trader_late = next(v for v in all_in_window if v["mmsi"] == "563000111")
    assert abs(trader_late["speed"] - 12.0) < 1e-4


def test_bounding_box_split_into_zones() -> None:
    from sentinel_analysis.infrastructure.ais.zone_splitter import split_into_zones

    small_bbox = BoundingBox(103.80, 1.20, 103.85, 1.25)
    small_zones = split_into_zones(small_bbox, zone_size_nm=10.0)
    assert len(small_zones) == 1
    assert small_zones[0] == small_bbox

    large_bbox = BoundingBox(103.50, 1.00, 104.10, 1.60)
    large_zones = split_into_zones(large_bbox, zone_size_nm=10.0)
    assert len(large_zones) > 1
    min_lon = min(z.min_longitude for z in large_zones)
    min_lat = min(z.min_latitude for z in large_zones)
    max_lon = max(z.max_longitude for z in large_zones)
    max_lat = max(z.max_latitude for z in large_zones)
    assert abs(min_lon - large_bbox.min_longitude) < 1e-4
    assert abs(min_lat - large_bbox.min_latitude) < 1e-4
    assert abs(max_lon - large_bbox.max_longitude) < 1e-4
    assert abs(max_lat - large_bbox.max_latitude) < 1e-4


def test_deduplicate_ais_records() -> None:
    from sentinel_analysis.domain.entities import Vessel, VesselPosition
    from sentinel_analysis.infrastructure.ais.zone_splitter import deduplicate_ais_records

    vessel = Vessel(imo="9123456", mmsi="563000111", name="VESSEL A", vessel_type="Cargo", callsign=None)
    rec_old = AISRecord(vessel=vessel, position=VesselPosition(mmsi="563000111", latitude=1.2, longitude=103.8, timestamp=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc), speed=10, heading=90))
    rec_new = AISRecord(vessel=vessel, position=VesselPosition(mmsi="563000111", latitude=1.21, longitude=103.81, timestamp=datetime(2026, 8, 30, 10, 5, tzinfo=timezone.utc), speed=11, heading=95))

    deduped = deduplicate_ais_records([rec_old, rec_new])
    assert len(deduped) == 1
    assert abs(deduped[0].position.latitude - 1.21) < 1e-4


def test_ais_friends_multi_zone_scraping() -> None:
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

    assert mock_session.get.call_count > 1
    assert len(records) == 1
    assert records[0].vessel.name == "ZONE VESSEL"


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()



