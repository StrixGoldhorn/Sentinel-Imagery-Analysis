import unittest
from datetime import datetime, timedelta, timezone

from sentinel_analysis.application.use_cases.detect_identity_anomalies import DetectIdentityAnomalies
from sentinel_analysis.domain.identity_anomalies import (
    IdentityAnomalySeverity,
    IdentityAnomalyType,
)


class TestExtendedAnomalies(unittest.TestCase):
    def setUp(self):
        self.use_case = DetectIdentityAnomalies()
        self.base_time = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_impossible_speed_and_teleportation(self):
        records = [
            {
                "mmsi": "412000001",
                "vessel_name": "WARP RUNNER",
                "latitude": 1.25,
                "longitude": 103.75,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "412000001",
                "vessel_name": "WARP RUNNER",
                "latitude": 3.25,
                "longitude": 103.75,
                "timestamp": self.base_time + timedelta(minutes=10),
            },
        ]
        res = self.use_case.execute(ais_records=records)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["anomalies_by_type"]["IMPOSSIBLE_SPEED"], 0)
        self.assertGreater(res["anomalies_by_type"]["TELEPORTATION"], 0)

        teleport = [a for a in res["anomalies"] if a["anomaly_type"] == "TELEPORTATION"][0]
        self.assertEqual(teleport["severity"], "CRITICAL")
        self.assertGreater(teleport["evidence"]["implied_speed_knots"], 150.0)

    def test_course_discontinuity_abrupt_turn(self):
        # Steaming at 16 knots, course suddenly changes from 45 deg to 145 deg (100 deg turn) in 60s
        records = [
            {
                "mmsi": "412000002",
                "vessel_name": "SHARP TURNER",
                "latitude": 1.25,
                "longitude": 103.75,
                "speed": 16.0,
                "cog": 45.0,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "412000002",
                "vessel_name": "SHARP TURNER",
                "latitude": 1.253,
                "longitude": 103.753,
                "speed": 15.5,
                "cog": 145.0,
                "timestamp": self.base_time + timedelta(seconds=60),
            },
        ]
        res = self.use_case.execute(ais_records=records)
        self.assertGreater(res["anomalies_by_type"]["COURSE_DISCONTINUITY"], 0)
        anom = [a for a in res["anomalies"] if a["anomaly_type"] == "COURSE_DISCONTINUITY"][0]
        self.assertEqual(anom["severity"], "HIGH")
        self.assertGreaterEqual(anom["evidence"]["course_delta_deg"], 85.0)

    def test_course_discontinuity_heading_vs_cog_divergence(self):
        # Heading 90 deg, COG 180 deg (90 deg divergence) while moving at 12 knots
        records = [
            {
                "mmsi": "412000003",
                "vessel_name": "CRAB WALKER",
                "latitude": 1.25,
                "longitude": 103.75,
                "speed": 12.0,
                "heading": 90.0,
                "cog": 180.0,
                "timestamp": self.base_time,
            },
            {
                "mmsi": "412000003",
                "vessel_name": "CRAB WALKER",
                "latitude": 1.248,
                "longitude": 103.75,
                "speed": 12.0,
                "heading": 90.0,
                "cog": 180.0,
                "timestamp": self.base_time + timedelta(seconds=120),
            },
        ]
        res = self.use_case.execute(ais_records=records)
        self.assertGreater(res["anomalies_by_type"]["COURSE_DISCONTINUITY"], 0)
        anom = [a for a in res["anomalies"] if a["anomaly_type"] == "COURSE_DISCONTINUITY"][0]
        self.assertGreaterEqual(anom["evidence"]["divergence_deg"], 65.0)

    def test_unusual_route_sinuosity(self):
        # Meandering path: 6 points zigzagging back and forth over 30 km total path,
        # but net displacement between first and last is small (~4 km)
        # Sinuosity = 30 / 4 = 7.5 > 2.3
        points = [
            (1.00, 103.50),
            (1.08, 103.55),
            (1.01, 103.60),
            (1.09, 103.65),
            (1.02, 103.70),
            (1.04, 103.52),
        ]
        records = [
            {
                "mmsi": "412000004",
                "vessel_name": "ZIGZAG SEARCHER",
                "latitude": lat,
                "longitude": lon,
                "speed": 10.0,
                "timestamp": self.base_time + timedelta(minutes=20 * i),
            }
            for i, (lat, lon) in enumerate(points)
        ]
        res = self.use_case.execute(ais_records=records)
        self.assertGreater(res["anomalies_by_type"]["UNUSUAL_ROUTE"], 0)
        anom = [a for a in res["anomalies"] if a["anomaly_type"] == "UNUSUAL_ROUTE"][0]
        self.assertGreater(anom["evidence"]["sinuosity_ratio"], 2.3)

    def test_convoy_formation(self):
        # Two vessels steaming in parallel 1 km apart at ~14 knots with same course
        mmsi_1 = "563000001"
        mmsi_2 = "563000002"
        records = []
        for i in range(3):
            t = self.base_time + timedelta(minutes=5 * i)
            # Vessel 1
            records.append({
                "mmsi": mmsi_1,
                "vessel_name": "ESCORT ALPHA",
                "latitude": 1.20 + i * 0.02,
                "longitude": 103.50 + i * 0.02,
                "speed": 14.0,
                "cog": 45.0,
                "timestamp": t,
            })
            # Vessel 2 ~1 km away
            records.append({
                "mmsi": mmsi_2,
                "vessel_name": "HIGH VALUE ASSET",
                "latitude": 1.208 + i * 0.02,
                "longitude": 103.50 + i * 0.02,
                "speed": 14.2,
                "cog": 46.0,
                "timestamp": t,
            })

        res = self.use_case.execute(ais_records=records)
        self.assertGreater(res["anomalies_by_type"]["CONVOY"], 0)
        anom = [a for a in res["anomalies"] if a["anomaly_type"] == "CONVOY"][0]
        self.assertEqual(anom["evidence"]["partner_mmsi"], mmsi_2)
        self.assertLess(anom["evidence"]["distance_km"], 3.0)

    def test_rendezvous_open_water_vs_anchorage(self):
        mmsi_1 = "351000010"
        mmsi_2 = "351000011"
        t_rdvz = self.base_time + timedelta(minutes=10)
        records = [
            {
                "mmsi": mmsi_1,
                "vessel_name": "BUNKER TANKER",
                "latitude": 1.3500,
                "longitude": 104.2500,
                "speed": 1.2,
                "timestamp": t_rdvz,
            },
            {
                "mmsi": mmsi_2,
                "vessel_name": "CARGO RECEIVER",
                "latitude": 1.3515,  # ~165 meters away
                "longitude": 104.2500,
                "speed": 0.8,
                "timestamp": t_rdvz,
            },
        ]

        # 1. Open water -> rendezvous detected
        res_open = self.use_case.execute(ais_records=records)
        self.assertGreater(res_open["anomalies_by_type"]["RENDEZVOUS"], 0)
        anom = [a for a in res_open["anomalies"] if a["anomaly_type"] == "RENDEZVOUS"][0]
        self.assertLess(anom["evidence"]["separation_meters"], 300.0)

        # 2. Inside designated anchorage -> rendezvous suppressed
        anchorage = [{"latitude": 1.350, "longitude": 104.250, "radius_km": 2.0}]
        res_anch = self.use_case.execute(ais_records=records, designated_anchorages=anchorage)
        self.assertEqual(res_anch["anomalies_by_type"]["RENDEZVOUS"], 0)


if __name__ == "__main__":
    unittest.main()
