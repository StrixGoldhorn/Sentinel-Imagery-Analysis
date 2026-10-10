import unittest
from sentinel_analysis.application.use_cases.compare_detection_algorithms import CompareDetectionAlgorithms
from sentinel_analysis.domain.algorithm_comparison import (
    calculate_box_iou,
    DetectionComparisonResult,
)
from sentinel_analysis.domain.entities import ShipDetection


class TestDetectionComparison(unittest.TestCase):
    def test_box_iou_calculation(self):
        # 1. Identical boxes -> 1.0
        box1 = (10, 10, 100, 100)
        box2 = (10, 10, 100, 100)
        self.assertAlmostEqual(calculate_box_iou(box1, box2), 1.0)

        # 2. Non-overlapping -> 0.0
        box3 = (200, 200, 50, 50)
        self.assertAlmostEqual(calculate_box_iou(box1, box3), 0.0)

        # 3. Known overlap:
        # Box A: [0, 0, 100, 100], area = 10000
        # Box B: [50, 0, 100, 100], area = 10000
        # Overlap: [50, 0, 50, 100], area = 5000
        # Union = 10000 + 10000 - 5000 = 15000
        # IoU = 5000 / 15000 = 1/3 ~ 0.3333
        box_a = {"x": 0, "y": 0, "width": 100, "height": 100}
        box_b = {"x": 50, "y": 0, "width": 100, "height": 100}
        self.assertAlmostEqual(calculate_box_iou(box_a, box_b), 1.0 / 3.0, places=4)

    def test_consensus_clustering_and_agreement_metrics(self):
        use_case = CompareDetectionAlgorithms()

        # Target 1: Detected by all 3 (High Consensus)
        det_t1_cfar = {"x": 100, "y": 100, "width": 40, "height": 80, "confidence": 0.90, "length": 85.0, "beam": 18.0}
        det_t1_cv = {"x": 102, "y": 98, "width": 38, "height": 82, "confidence": 0.85, "length": 82.0, "beam": 17.0}
        det_t1_onnx = {"x": 99, "y": 101, "width": 41, "height": 79, "confidence": 0.95, "length": 88.0, "beam": 19.0}

        # Target 2: Detected by CFAR and Classical CV only (Moderate Consensus)
        det_t2_cfar = {"x": 300, "y": 300, "width": 50, "height": 120, "confidence": 0.82, "length": 125.0, "beam": 25.0}
        det_t2_cv = {"x": 305, "y": 298, "width": 48, "height": 122, "confidence": 0.80, "length": 120.0, "beam": 24.0}

        # Target 3: Detected only by ONNX (Single-algorithm unique)
        det_t3_onnx = {"x": 600, "y": 600, "width": 30, "height": 50, "confidence": 0.75, "length": 52.0, "beam": 12.0}

        # Target 4: Detected only by CFAR (Low RCS target / single-algorithm unique)
        det_t4_cfar = {"x": 800, "y": 150, "width": 20, "height": 30, "confidence": 0.65, "length": 32.0, "beam": 9.0}

        detections_by_algo = {
            "cfar": [det_t1_cfar, det_t2_cfar, det_t4_cfar],
            "classical_cv": [det_t1_cv, det_t2_cv],
            "onnx": [det_t1_onnx, det_t3_onnx],
        }

        result = use_case.execute(detections_by_algo=detections_by_algo, iou_threshold=0.3)

        self.assertIsInstance(result, DetectionComparisonResult)
        self.assertEqual(result.total_unique_targets, 4)
        self.assertEqual(result.three_way_consensus_count, 1)
        self.assertEqual(result.two_way_consensus_count, 1)
        self.assertEqual(result.unique_counts["cfar"], 1)
        self.assertEqual(result.unique_counts["classical_cv"], 0)
        self.assertEqual(result.unique_counts["onnx"], 1)

        # Algorithm counts
        self.assertEqual(result.algorithm_metrics["cfar"].detection_count, 3)
        self.assertEqual(result.algorithm_metrics["classical_cv"].detection_count, 2)
        self.assertEqual(result.algorithm_metrics["onnx"].detection_count, 2)

        # Pairwise metrics:
        # CFAR and Classical CV share 2 targets (T1 and T2).
        # Union = 3 + 2 - 2 = 3. Jaccard = 2/3 ~ 0.6667
        cfar_cv = result.pairwise_metrics["cfar_vs_classical_cv"]
        self.assertEqual(cfar_cv.matched_count, 2)
        self.assertAlmostEqual(cfar_cv.jaccard_similarity, 2.0 / 3.0, places=3)
        self.assertGreater(cfar_cv.mean_iou, 0.7)

        # Dictionary serialization
        res_dict = result.to_dict()
        self.assertIn("summary", res_dict)
        self.assertIn("consensus_contacts", res_dict)
        self.assertEqual(len(res_dict["consensus_contacts"]), 4)

        # Check high-consensus target
        high_targets = [c for c in result.consensus_contacts if c.agreement_level == "HIGH"]
        self.assertEqual(len(high_targets), 1)
        self.assertCountEqual(high_targets[0].detected_by, ["cfar", "classical_cv", "onnx"])
        self.assertAlmostEqual(high_targets[0].mean_confidence, (0.90 + 0.85 + 0.95) / 3.0, places=3)

    def test_with_ship_detection_domain_objects(self):
        use_case = CompareDetectionAlgorithms()

        det1 = ShipDetection(x=50, y=50, width=30, height=60, confidence=0.92, length=65.0, beam=14.0)
        det2 = ShipDetection(x=52, y=49, width=28, height=61, confidence=0.88, length=64.0, beam=13.0)

        result = use_case.execute(
            detections_by_algo={
                "cfar": [det1],
                "classical_cv": [det2],
                "onnx": [],
            }
        )

        self.assertEqual(result.total_unique_targets, 1)
        self.assertEqual(result.two_way_consensus_count, 1)
        self.assertEqual(result.three_way_consensus_count, 0)
        self.assertEqual(result.consensus_contacts[0].agreement_level, "MODERATE")
