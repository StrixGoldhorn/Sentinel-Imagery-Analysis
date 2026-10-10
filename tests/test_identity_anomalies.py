import unittest
from datetime import datetime, timedelta, timezone

from sentinel_analysis.application.use_cases.detect_identity_anomalies import DetectIdentityAnomalies
from sentinel_analysis.domain.identity_anomalies import (
    IdentityAnomalySeverity,
    IdentityAnomalyType,
    get_country_from_mmsi,
    haversine_distance_km,
)


class TestIdentityAnomalies(unittest.TestCase):
    def setUp(self):
        self.use_case = DetectIdentityAnomalies()
        self.base_time = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_mid_country_lookup(self):
        country, iso = get_country_from_mmsi("351123456")
        self.assertEqual(country, "Panama")
        self.assertEqual(iso, "PA")

        country_sg, iso_sg = get_country_from_mmsi(563999999)
        self.assertEqual(country_sg, "Singapore")
        self.assertEqual(iso_sg, "SG")

        country_none, iso_none = get_country_from_mmsi("000123")
        self.assertIsNone(country_none)
        self.assertIsNone(iso_none)

    def test_mmsi_reuse_distinct_names_and_dimensions(self):
        records = [
            {
                "mmsi": "351000001",
                "vessel_name": "CARGO STAR",
                "length": 85.0,
                "latitude": 1.25,
                "longitude": 103.75,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "351000001",
                "vessel_name": "NORTHERN LIGHTS",
                "length": 210.0,
                "latitude": 1.26,
                "longitude": 103.76,
                "timestamp": self.base_time + timedelta(minutes=10),
            },
        ]
        res = self.use_case.execute(ais_records=records)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["anomalies_by_type"]["MMSI_REUSE"], 0)
        reasons = [a["description"] for a in res["anomalies"] if a["anomaly_type"] == "MMSI_REUSE"]
        self.assertTrue(any("distinct vessel names" in r for r in reasons))
        self.assertTrue(any("conflicting hull lengths" in r for r in reasons))

    def test_impossible_jump(self):
        # 100 km jump in 20 minutes (~162 knots)
        records = [
            {
                "mmsi": "351000002",
                "vessel_name": "FAST PHANTOM",
                "latitude": 1.20,
                "longitude": 103.70,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "351000002",
                "vessel_name": "FAST PHANTOM",
                "latitude": 2.10,
                "longitude": 103.70,
                "timestamp": self.base_time + timedelta(minutes=20),
            },
        ]
        res = self.use_case.execute(ais_records=records, max_speed_knots=50.0)
        self.assertEqual(res["anomalies_by_type"]["IMPOSSIBLE_JUMP"], 1)
        anom = [a for a in res["anomalies"] if a["anomaly_type"] == "IMPOSSIBLE_JUMP"][0]
        self.assertEqual(anom["severity"], "CRITICAL")
        self.assertGreater(anom["evidence"]["implied_speed_knots"], 100.0)

    def test_flag_and_callsign_change(self):
        # Panama MID (351) reporting German flag
        records = [
            {
                "mmsi": "351000003",
                "vessel_name": "FLAG SHIFTER",
                "flag": "Germany",
                "callsign": "CALL1",
                "latitude": 1.25,
                "longitude": 103.75,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "351000003",
                "vessel_name": "FLAG SHIFTER",
                "flag": "Germany",
                "callsign": "CALL2",
                "latitude": 1.26,
                "longitude": 103.76,
                "timestamp": self.base_time + timedelta(minutes=15),
            },
        ]
        res = self.use_case.execute(ais_records=records)
        self.assertGreater(res["anomalies_by_type"]["FLAG_CALLSIGN_CHANGE"], 0)
        types = [a["anomaly_type"] for a in res["anomalies"]]
        self.assertIn("FLAG_CALLSIGN_CHANGE", types)

    def test_duplicate_identity(self):
        # Same MMSI transmitting at the same minute from positions 50km apart
        records = [
            {
                "mmsi": "351000004",
                "vessel_name": "TWIN CLONE A",
                "latitude": 1.20,
                "longitude": 103.70,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "351000004",
                "vessel_name": "TWIN CLONE B",
                "latitude": 1.65,
                "longitude": 103.70,
                "timestamp": self.base_time + timedelta(minutes=1),
            },
        ]
        res = self.use_case.execute(ais_records=records)
        self.assertEqual(res["anomalies_by_type"]["DUPLICATE_IDENTITY"], 1)
        anom = [a for a in res["anomalies"] if a["anomaly_type"] == "DUPLICATE_IDENTITY"][0]
        self.assertEqual(anom["severity"], "CRITICAL")

    def test_ais_gap(self):
        # Vessel traveling, then 4 hours silence before reappearing
        records = [
            {
                "mmsi": "351000005",
                "vessel_name": "DARK RUNNER",
                "latitude": 1.20,
                "longitude": 103.70,
                "speed": 12.0,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "351000005",
                "vessel_name": "DARK RUNNER",
                "latitude": 1.35,
                "longitude": 103.70,
                "speed": 11.5,
                "timestamp": self.base_time + timedelta(hours=4),
            },
        ]
        res = self.use_case.execute(ais_records=records, gap_threshold_hours=2.0)
        self.assertEqual(res["anomalies_by_type"]["AIS_GAP"], 1)
        anom = [a for a in res["anomalies"] if a["anomaly_type"] == "AIS_GAP"][0]
        self.assertAlmostEqual(anom["evidence"]["gap_hours"], 4.0, places=1)

    def test_loitering_in_open_water_vs_anchorage(self):
        # 3 hours of low speed (< 1 knot)
        records = [
            {
                "mmsi": "351000006",
                "vessel_name": "SLOW DRIFTER",
                "latitude": 1.250 + i * 0.0005,
                "longitude": 103.750 + i * 0.0005,
                "speed": 0.8,
                "timestamp": self.base_time + timedelta(hours=i),
            }
            for i in range(4)
        ]

        # 1. Open water -> flagged as LOITERING
        res_open = self.use_case.execute(ais_records=records, loiter_min_hours=2.0)
        self.assertEqual(res_open["anomalies_by_type"]["LOITERING"], 1)

        # 2. Inside designated anchorage -> exempted
        anchorage = [{"latitude": 1.251, "longitude": 103.751, "radius_km": 5.0}]
        res_anch = self.use_case.execute(
            ais_records=records, loiter_min_hours=2.0, designated_anchorages=anchorage
        )
        self.assertEqual(res_anch["anomalies_by_type"]["LOITERING"], 0)

    def test_geojson_generation(self):
        records = [
            {
                "mmsi": "351000007",
                "vessel_name": "ANOMALOUS TARGET",
                "latitude": 1.20,
                "longitude": 103.70,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "351000007",
                "vessel_name": "ANOMALOUS TARGET",
                "latitude": 2.20,
                "longitude": 103.70,
                "timestamp": self.base_time + timedelta(minutes=15),
            },
        ]
        res = self.use_case.execute(ais_records=records)
        geojson = res.get("geojson")
        self.assertIsNotNone(geojson)
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertGreater(len(geojson["features"]), 0)
        feat = geojson["features"][0]
        self.assertEqual(feat["geometry"]["type"], "Point")
        self.assertEqual(feat["properties"]["mmsi"], "351000007")


if __name__ == "__main__":
    unittest.main()
