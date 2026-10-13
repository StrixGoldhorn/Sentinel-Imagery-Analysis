import unittest
from datetime import datetime, timezone

from sentinel_analysis.application.use_cases.decompose_risk_score import DecomposeRiskScore
from sentinel_analysis.domain.risk_scoring import (
    RiskCategory,
    RiskThreatLevel,
    threat_level_from_score,
)


class TestRiskScoreDecomposition(unittest.TestCase):
    def setUp(self):
        self.use_case = DecomposeRiskScore()
        self.base_time = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_threat_level_from_score(self):
        self.assertEqual(threat_level_from_score(15.0), RiskThreatLevel.LOW)
        self.assertEqual(threat_level_from_score(35.0), RiskThreatLevel.GUARD)
        self.assertEqual(threat_level_from_score(60.0), RiskThreatLevel.ELEVATED)
        self.assertEqual(threat_level_from_score(78.0), RiskThreatLevel.HIGH)
        self.assertEqual(threat_level_from_score(92.0), RiskThreatLevel.CRITICAL)

    def test_dark_commercial_vessel_solas_violation(self):
        det = {
            "detection_id": "det_dark_1",
            "latitude": 1.25,
            "longitude": 103.75,
            "length_m": 125.0,
            "width_m": 22.0,
            "wake_speed_knots": 14.0,
        }
        res = self.use_case.execute(detection=det, correlated_ais=None)
        self.assertEqual(res["status"], "success")
        dec = res["decompositions"][0]

        self.assertEqual(dec["target_id"], "det_dark_1")
        dark_comp = dec["component_scores"][RiskCategory.DARK_UNCORRELATED.value]
        self.assertEqual(dark_comp["raw_score"], 80.0)  # 45 (unreported) + 35 (SOLAS >=50m)

        factor_names = [f["name"] for f in dark_comp["factors"]]
        self.assertIn("UNREPORTED_RADAR_CONTACT", factor_names)
        self.assertIn("SOLAS_CARRIAGE_NON_COMPLIANCE", factor_names)

        # Primary driver should be dark uncorrelated
        self.assertEqual(dec["primary_driver"], RiskCategory.DARK_UNCORRELATED.value)
        self.assertIn(dec["threat_level"], (RiskThreatLevel.GUARD.value, RiskThreatLevel.ELEVATED.value, RiskThreatLevel.HIGH.value))

    def test_small_craft_exemption_mitigation(self):
        det = {
            "detection_id": "det_skiff_1",
            "latitude": 1.25,
            "longitude": 103.75,
            "length_m": 12.0,  # < 18m
            "width_m": 3.0,
        }
        res = self.use_case.execute(detection=det, correlated_ais=None)
        dec = res["decompositions"][0]

        # Small craft mitigation should be present
        mitigating_names = [f["name"] for f in dec["mitigating_factors"]]
        self.assertIn("SMALL_CRAFT_EXEMPTION", mitigating_names)
        # Raw dark score = 45 - 25 = 20
        dark_comp = dec["component_scores"][RiskCategory.DARK_UNCORRELATED.value]
        self.assertEqual(dark_comp["raw_score"], 20.0)

    def test_kinematic_spoofing_wake_heading_divergence_and_teleportation(self):
        det = {
            "detection_id": "det_spoofed_1",
            "latitude": 1.25,
            "longitude": 103.75,
            "heading_deg": 220.0,  # Physical wake vector
            "correlated_ais": {
                "mmsi": "351000999",
                "heading": 85.0,  # Diverges by 135 deg
                "speed": 15.0,
            },
        }
        anomalies = [
            {
                "mmsi": "351000999",
                "anomaly_type": "TELEPORTATION",
                "description": "Kinematic teleportation anomaly: displaced 150 km in 5 mins",
            }
        ]
        res = self.use_case.execute(detection=det, identity_anomalies=anomalies)
        dec = res["decompositions"][0]

        kin_comp = dec["component_scores"][RiskCategory.KINEMATIC_SPOOFING.value]
        self.assertGreater(kin_comp["raw_score"], 80.0)
        factor_names = [f["name"] for f in kin_comp["factors"]]
        self.assertIn("WAKE_HEADING_DIVERGENCE", factor_names)
        self.assertIn("TELEPORTATION_VIOLATION", factor_names)

    def test_identity_integrity_and_high_risk_flag(self):
        det = {
            "detection_id": "det_high_risk_flag",
            "latitude": 1.25,
            "longitude": 103.75,
            "correlated_ais": {
                "mmsi": "671000001",
                "flag": "TOGO",  # High risk flag registry
                "heading": 90.0,
            },
        }
        anomalies = [
            {
                "mmsi": "671000001",
                "anomaly_type": "MMSI_REUSE",
                "description": "MMSI reused by distinct vessels",
            },
            {
                "mmsi": "671000001",
                "anomaly_type": "AIS_GAP",
                "description": "Extended blackout gap of 8 hours",
            },
        ]
        res = self.use_case.execute(detection=det, identity_anomalies=anomalies)
        dec = res["decompositions"][0]

        ident_comp = dec["component_scores"][RiskCategory.IDENTITY_INTEGRITY.value]
        self.assertGreaterEqual(ident_comp["raw_score"], 80.0)
        factor_names = [f["name"] for f in ident_comp["factors"]]
        self.assertIn("MMSI_REUSE_DETECTED", factor_names)
        self.assertIn("AIS_BLACKOUT_GAP", factor_names)
        self.assertIn("HIGH_RISK_FLAG_REGISTRY", factor_names)

    def test_behavioral_transshipment_and_loitering(self):
        det = {
            "detection_id": "det_sts_1",
            "latitude": 1.35,
            "longitude": 104.25,
            "correlated_ais": {"mmsi": "351000888"},
        }
        events = [{"detection_idx_1": 0, "detection_idx_2": 1, "type": "TRANSSHIPMENT"}]
        anomalies = [
            {
                "mmsi": "351000888",
                "anomaly_type": "LOITERING",
                "description": "Loitering in open water for 4.5 hours",
            }
        ]
        res = self.use_case.execute(
            detection=det,
            identity_anomalies=anomalies,
            transshipment_events=events,
        )
        dec = res["decompositions"][0]
        behav_comp = dec["component_scores"][RiskCategory.BEHAVIORAL_TACTICAL.value]
        factor_names = [f["name"] for f in behav_comp["factors"]]
        self.assertIn("TRANSSHIPMENT_RENDEZVOUS", factor_names)
        self.assertIn("ANOMALOUS_LOITERING", factor_names)
        self.assertGreaterEqual(behav_comp["raw_score"], 80.0)

    def test_environmental_mpa_and_infrastructure(self):
        det = {
            "detection_id": "det_env_1",
            "latitude": 1.25,
            "longitude": 103.75,
            "correlated_ais": {"mmsi": "351000777"},
        }
        env_ctx = {
            "marine_protected_areas": [
                {"name": "Turtle Coral Sanctuary", "intersects_target": True}
            ],
            "infrastructure_proximity": [
                {"name": "Offshore Oil Platform Beta", "distance_m": 450}
            ],
        }
        res = self.use_case.execute(detection=det, environmental_context=env_ctx)
        dec = res["decompositions"][0]
        env_comp = dec["component_scores"][RiskCategory.ENVIRONMENTAL.value]
        factor_names = [f["name"] for f in env_comp["factors"]]
        self.assertIn("MPA_INCURSION", factor_names)
        self.assertIn("INFRASTRUCTURE_PROXIMITY_ALERT", factor_names)
        self.assertGreaterEqual(env_comp["raw_score"], 70.0)

    def test_multi_target_scan_summary(self):
        dets = [
            {"detection_id": "det_1", "length_m": 120.0, "latitude": 1.1, "longitude": 103.1},
            {"detection_id": "det_2", "length_m": 15.0, "latitude": 1.2, "longitude": 103.2},
        ]
        res = self.use_case.execute(detection=None, scan_id=None)
        self.assertEqual(res["total_targets"], 0)

        # Directly pass multiple detections by executing with detection_index parameter or list
        # Let's verify single detection index query
        res_single = self.use_case.execute(detection=dets[0], detection_index=0)
        self.assertEqual(res_single["status"], "success")
        self.assertIn("decomposition", res_single)
        self.assertEqual(res_single["decomposition"]["target_id"], "det_1")


if __name__ == "__main__":
    unittest.main()
