"""Unit tests for tactical explainability and classification rationales."""

import unittest
from sentinel_analysis.application.use_cases.generate_tactical_explainability import (
    GenerateTacticalExplainability,
)
from sentinel_analysis.domain.explainability import (
    EvidenceItem,
    ExplainabilityReport,
    TargetClassification,
    TargetRationale,
    ThreatLevel,
)


class TestTacticalExplainability(unittest.TestCase):
    def setUp(self):
        self.use_case = GenerateTacticalExplainability()

    def test_dark_vessel_classification(self):
        """Unassociated contact with strong radar signature and no AIS is flagged as dark vessel."""
        det = {
            "lat": 1.15,
            "lng": 103.85,
            "confidence": 0.88,
            "length": 28.0,
            "beam": 7.0,
            "is_correlated": False,
            "correlation_status": "no_ais",
            "is_dark": True,
        }

        report = self.use_case.execute(detections=[det])

        self.assertIsInstance(report, ExplainabilityReport)
        self.assertEqual(report.total_targets, 1)
        self.assertEqual(report.dark_vessel_count, 1)
        self.assertEqual(report.rationales[0].primary_classification, TargetClassification.DARK_VESSEL)
        self.assertIn(report.rationales[0].threat_level, (ThreatLevel.HIGH, ThreatLevel.MEDIUM, ThreatLevel.ELEVATED))
        self.assertTrue(any("DARK" in code or "RADAR" in code for code in report.rationales[0].reason_codes))
        self.assertTrue(len(report.rationales[0].evidence) >= 1)

    def test_solas_suspect_classification(self):
        """Large vessel (> 45-50m) unbroadcast is flagged as SOLAS suspect under Chapter V."""
        det = {
            "lat": 1.25,
            "lng": 103.95,
            "confidence": 0.92,
            "length": 75.0,
            "beam": 15.0,
            "is_correlated": False,
            "correlation_status": "no_ais",
        }

        report = self.use_case.execute(detections=[det])

        self.assertEqual(report.solas_suspect_count, 1)
        rat = report.rationales[0]
        self.assertEqual(rat.primary_classification, TargetClassification.SOLAS_SUSPECT)
        self.assertEqual(rat.threat_level, ThreatLevel.HIGH)
        self.assertTrue(any("SOLAS" in ev.factor for ev in rat.evidence))
        self.assertIn("SOLAS", rat.summary_rationale)

    def test_spoofed_ais_classification(self):
        """Kinematic wake discrepancy with AIS broadcast signals spoofing."""
        det = {
            "lat": 1.30,
            "lng": 104.05,
            "confidence": 0.90,
            "length": 75.0,
            "beam": 14.0,
            "is_correlated": True,
            "is_course_spoofed": True,
            "spoofing_warning": "Kinematic wake heading 240 deg diverges from AIS heading 045 deg",
            "wake_heading_deg": 240.0,
            "wake_speed_knots": 16.5,
            "correlated_ais": {
                "mmsi": 567890123,
                "name": "OCEAN_PHANTOM",
                "heading": 45.0,
                "speed": 3.0,
            },
        }

        report = self.use_case.execute(detections=[det])

        self.assertEqual(report.spoofed_count, 1)
        rat = report.rationales[0]
        self.assertEqual(rat.primary_classification, TargetClassification.SPOOFED_AIS)
        self.assertEqual(rat.threat_level, ThreatLevel.CRITICAL)
        self.assertTrue(any("SPOOF" in ev.factor or "DIVERGENCE" in ev.factor for ev in rat.evidence))

    def test_transshipment_suspect_classification(self):
        """Close-quarter encounter (<500m) in open waters triggers STS transshipment alert."""
        vessel1 = {
            "lat": 1.1800,
            "lng": 103.6200,
            "confidence": 0.85,
            "length": 160.0,
            "beam": 28.0,
            "is_correlated": True,
            "transshipment_suspect": True,
            "correlated_ais": {"mmsi": 412345678, "speed": 1.2},
        }
        vessel2 = {
            "lat": 1.1815,
            "lng": 103.6210,
            "confidence": 0.88,
            "length": 90.0,
            "beam": 16.0,
            "is_correlated": False,
            "transshipment_suspect": True,
        }

        report = self.use_case.execute(detections=[vessel1, vessel2])

        self.assertGreaterEqual(report.transshipment_count, 1)
        sts_targets = [r for r in report.rationales if r.primary_classification == TargetClassification.TRANSSHIPMENT_SUSPECT]
        self.assertTrue(len(sts_targets) >= 1)
        self.assertEqual(sts_targets[0].threat_level, ThreatLevel.HIGH)

    def test_offshore_infrastructure_suppression(self):
        """Co-location with known offshore platforms classifies contact as infrastructure rather than vessel."""
        det = {
            "lat": 56.50,
            "lng": 3.20,
            "confidence": 0.95,
            "length": 65.0,
            "beam": 60.0,
            "is_correlated": False,
            "is_infrastructure": True,
            "offshore_infrastructure": {
                "name": "Ekofisk Complex Alpha",
                "type": "OIL_PLATFORM",
            },
        }

        report = self.use_case.execute(detections=[det])

        self.assertEqual(report.infrastructure_count, 1)
        rat = report.rationales[0]
        self.assertEqual(rat.primary_classification, TargetClassification.OFFSHORE_INFRASTRUCTURE)
        self.assertTrue(any("INFRASTRUCTURE" in code for code in rat.reason_codes))

    def test_cooperative_vessel_classification(self):
        """Nominal vessel with matched AIS track is verified cooperative."""
        det = {
            "lat": 1.20,
            "lng": 103.70,
            "confidence": 0.94,
            "length": 210.0,
            "beam": 32.0,
            "is_correlated": True,
            "correlation_status": "inside_box",
            "association_probability": 0.96,
            "correlated_ais": {
                "mmsi": 211234567,
                "name": "HAMBURG_EXPRESS",
                "speed": 14.5,
                "heading": 85.0,
            },
        }

        report = self.use_case.execute(detections=[det])

        self.assertEqual(report.cooperative_count, 1)
        rat = report.rationales[0]
        self.assertEqual(rat.primary_classification, TargetClassification.COOPERATIVE_VESSEL)
        self.assertEqual(rat.threat_level, ThreatLevel.LOW)

    def test_report_to_dict_serialization(self):
        """Explainability report serializes completely to dictionary for JSON APIs."""
        det = {
            "lat": 1.15,
            "lng": 103.85,
            "confidence": 0.88,
            "length": 28.0,
            "beam": 7.0,
            "is_correlated": False,
        }

        report = self.use_case.execute(scan_id="scan_test_exp_01", detections=[det])
        data = report.to_dict()

        self.assertEqual(data["scan_id"], "scan_test_exp_01")
        self.assertEqual(data["total_targets"], 1)
        self.assertIn("rationales", data)
        self.assertEqual(len(data["rationales"]), 1)
        self.assertIn("primary_classification", data["rationales"][0])
        self.assertIn("evidence", data["rationales"][0])


if __name__ == "__main__":
    unittest.main()
