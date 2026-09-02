import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_TMP_DIR = Path(__file__).resolve().parent / "runtime" / "tmp"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP_DIR)

from sentinel_analysis.application.exceptions import VesselNotFoundError
from sentinel_analysis.application.use_cases.manage_vessels import (
    GetVesselDetails,
    UpdateVesselDetails,
)
from sentinel_analysis.domain.entities import AISRecord, Vessel, VesselPosition
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository


class InMemoryVesselRepository:
    def __init__(self, vessels=None):
        self._vessels = vessels or {}

    def get_vessel_by_id(self, vessel_id: int):
        return self._vessels.get(vessel_id)

    def update_vessel(
        self,
        vessel_id: int,
        name: str | None = None,
        vessel_type: str | None = None,
        callsign: str | None = None,
        imo: str | None = None,
    ):
        if vessel_id not in self._vessels:
            return None
        v = dict(self._vessels[vessel_id])
        if name is not None:
            v["name"] = name
            v["vessel_name"] = name
        if vessel_type is not None:
            v["type"] = vessel_type
            v["vessel_type"] = vessel_type
        if callsign is not None:
            v["callsign"] = callsign
        if imo is not None:
            v["imo"] = imo
        self._vessels[vessel_id] = v
        return v


class ManageVesselsUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.mock_vessel = {
            "id": 1,
            "vessel_id": 1,
            "imo": "IMO9123456",
            "mmsi": "123456789",
            "name": "OCEAN EXPLORER",
            "vessel_name": "OCEAN EXPLORER",
            "type": "Cargo",
            "vessel_type": "Cargo",
            "callsign": "ABCD",
            "latitude": 1.25,
            "longitude": 103.85,
        }
        self.repo = InMemoryVesselRepository({1: self.mock_vessel})
        self.get_use_case = GetVesselDetails(self.repo)
        self.update_use_case = UpdateVesselDetails(self.repo)

    def test_get_vessel_details_success(self):
        vessel = self.get_use_case.execute(1)
        self.assertEqual(vessel["name"], "OCEAN EXPLORER")
        self.assertEqual(vessel["mmsi"], "123456789")

    def test_get_vessel_details_not_found(self):
        with self.assertRaises(VesselNotFoundError):
            self.get_use_case.execute(999)

    def test_get_vessel_details_invalid_id(self):
        with self.assertRaises(ValueError):
            self.get_use_case.execute(-1)
        with self.assertRaises(ValueError):
            self.get_use_case.execute("1")  # type: ignore

    def test_update_vessel_details_success(self):
        updated = self.update_use_case.execute(
            vessel_id=1,
            name="PACIFIC VOYAGER",
            vessel_type="Tanker",
            callsign="EFGH",
            imo="IMO9876543",
        )
        self.assertEqual(updated["name"], "PACIFIC VOYAGER")
        self.assertEqual(updated["type"], "Tanker")
        self.assertEqual(updated["callsign"], "EFGH")
        self.assertEqual(updated["imo"], "IMO9876543")

    def test_update_vessel_details_partial(self):
        updated = self.update_use_case.execute(
            vessel_id=1,
            name="NEW NAME",
        )
        self.assertEqual(updated["name"], "NEW NAME")
        self.assertEqual(updated["type"], "Cargo")  # Unchanged

    def test_update_vessel_not_found(self):
        with self.assertRaises(VesselNotFoundError):
            self.update_use_case.execute(vessel_id=999, name="Ghost Ship")

    def test_update_vessel_empty_imo(self):
        with self.assertRaises(ValueError):
            self.update_use_case.execute(vessel_id=1, imo="   ")


class SQLiteVesselRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=str(_TMP_DIR))
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.repo = SQLiteAISRepository(self.db_path)

        # Seed test record
        vessel = Vessel(imo="IMO1112233", mmsi="567890123", name="TEST BOAT", vessel_type="Fishing", callsign="CALL1")
        pos = VesselPosition(mmsi="567890123", latitude=12.34, longitude=56.78, timestamp=datetime.now(timezone.utc), speed=8.5, heading=180.0)
        self.repo.save_records([AISRecord(vessel=vessel, position=pos)], source_plugin="MockPlugin")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_and_update_vessel_sqlite(self):
        # Query existing vessel from DB
        vessels = self.repo.get_vessel_positions(latest_only=True)
        self.assertEqual(len(vessels), 1)
        vessel_id = vessels[0]["vessel_id"]

        detail = self.repo.get_vessel_by_id(vessel_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["name"], "TEST BOAT")
        self.assertEqual(detail["mmsi"], "567890123")
        self.assertAlmostEqual(detail["latitude"], 12.34)

        # Update vessel
        updated = self.repo.update_vessel(
            vessel_id=vessel_id,
            name="UPDATED BOAT",
            vessel_type="Passenger",
            callsign="NEWCALL",
            imo="IMO9999999",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["name"], "UPDATED BOAT")
        self.assertEqual(updated["type"], "Passenger")
        self.assertEqual(updated["callsign"], "NEWCALL")
        self.assertEqual(updated["imo"], "IMO9999999")

        # Verify persisted query
        reloaded = self.repo.get_vessel_by_id(vessel_id)
        self.assertEqual(reloaded["name"], "UPDATED BOAT")
        self.assertEqual(reloaded["type"], "Passenger")


if __name__ == "__main__":
    unittest.main()
