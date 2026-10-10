import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.review import (
    ReviewAction,
    ReviewBox,
    ReviewDisposition,
    ReviewRecord,
    calculate_iou,
)
from sentinel_analysis.infrastructure.persistence.sqlite_review import SQLiteReviewRepository
from sentinel_analysis.interfaces.web.application import create_app


class TestReviewDomain(unittest.TestCase):
    """Test domain logic, geometry conversions, and IoU calculations."""

    def test_review_box_and_iou(self):
        box1 = ReviewBox(min_x=10, min_y=10, max_x=50, max_y=50)
        box2 = ReviewBox(min_x=10, min_y=10, max_x=50, max_y=50)
        # Identical boxes should have IoU = 1.0
        self.assertAlmostEqual(calculate_iou(box1, box2), 1.0)

        # Disjoint boxes should have IoU = 0.0
        box3 = ReviewBox(min_x=100, min_y=100, max_x=150, max_y=150)
        self.assertAlmostEqual(calculate_iou(box1, box3), 0.0)

        # Partial overlap
        box4 = ReviewBox(min_x=30, min_y=10, max_x=70, max_y=50)
        iou = calculate_iou(box1, box4)
        # box1 area = 40*40=1600. box4 area = 40*40=1600.
        # intersection = [30,10] to [50,50] -> 20*40=800.
        # union = 1600 + 1600 - 800 = 2400. IoU = 800/2400 = 1/3 ~ 0.3333
        self.assertAlmostEqual(iou, 1.0 / 3.0, places=3)

    def test_review_box_yolo_conversion(self):
        box = ReviewBox(min_x=10, min_y=20, max_x=30, max_y=60)
        yolo = box.to_yolo(img_width=100, img_height=200)
        self.assertAlmostEqual(yolo["x_center"], 20.0 / 100.0)
        self.assertAlmostEqual(yolo["y_center"], 40.0 / 200.0)
        self.assertAlmostEqual(yolo["width"], 20.0 / 100.0)
        self.assertAlmostEqual(yolo["height"], 40.0 / 200.0)

    def test_review_record_properties(self):
        rec = ReviewRecord(
            review_id="rev-1",
            scan_id="scan-1",
            detection_idx=0,
            disposition=ReviewDisposition.PENDING,
            original_bbox=ReviewBox(0, 0, 10, 10),
            corrected_bbox=ReviewBox(1, 1, 12, 12),
        )
        self.assertFalse(rec.is_reviewed)
        self.assertEqual(rec.effective_bbox.min_x, 1)

        rec.disposition = ReviewDisposition.ACCEPTED
        self.assertTrue(rec.is_reviewed)


class TestSQLiteReviewRepository(unittest.TestCase):
    """Test repository operations and immutable history tracking."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_review.db"
        self.repo = SQLiteReviewRepository(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_update_with_immutable_history(self):
        record = ReviewRecord(
            review_id="test-rev-1",
            scan_id="scan-alpha",
            detection_idx=0,
            disposition=ReviewDisposition.PENDING,
            reviewer_id="system",
            original_bbox=ReviewBox(10, 10, 50, 50),
            confidence=0.85,
            vessel_class="FISHING_VESSEL",
        )
        saved = self.repo.save(record)
        self.assertEqual(saved.review_id, "test-rev-1")

        # Initial history entry exists
        history = self.repo.get_history("test-rev-1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].action, ReviewAction.CREATED.value)

        # Update disposition to accepted with corrected box
        record.disposition = ReviewDisposition.ACCEPTED
        record.reviewer_id = "analyst-john"
        record.corrected_bbox = ReviewBox(12, 12, 52, 52)
        record.comments = "Wake confirms fishing vessel"
        self.repo.save(record, action=ReviewAction.ACCEPTED)

        # Check updated record
        fetched = self.repo.get_by_id("test-rev-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.disposition, ReviewDisposition.ACCEPTED)
        self.assertEqual(fetched.reviewer_id, "analyst-john")
        self.assertEqual(fetched.comments, "Wake confirms fishing vessel")

        # Immutable history now has 2 entries
        history = self.repo.get_history("test-rev-1")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1].action, ReviewAction.ACCEPTED.value)
        self.assertEqual(history[1].reviewer_id, "analyst-john")
        self.assertEqual(history[1].comments, "Wake confirms fishing vessel")

    def test_query_and_stats(self):
        # Insert 3 records: 1 pending, 1 accepted, 1 rejected
        self.repo.save(ReviewRecord(
            review_id="r1", scan_id="s1", detection_idx=0, disposition=ReviewDisposition.PENDING
        ))
        self.repo.save(ReviewRecord(
            review_id="r2", scan_id="s1", detection_idx=1, disposition=ReviewDisposition.ACCEPTED
        ))
        self.repo.save(ReviewRecord(
            review_id="r3", scan_id="s2", detection_idx=0, disposition=ReviewDisposition.REJECTED
        ))

        stats = self.repo.count_by_disposition()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["uncertain"], 0)

        # Filter by disposition
        accepted_items, total_acc = self.repo.query(disposition="accepted")
        self.assertEqual(len(accepted_items), 1)
        self.assertEqual(total_acc, 1)
        self.assertEqual(accepted_items[0].review_id, "r2")


class TestReviewUseCasesAndWeb(unittest.TestCase):
    """Test use cases and end-to-end web endpoints."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "sentinel_test.db"
        output_root = Path(self.temp_dir.name) / "outputs"
        output_root.mkdir(parents=True, exist_ok=True)

        settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=db_path,
            output_root=output_root,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
            debug=True,
        )
        self.container = ApplicationContainer(settings)

        # Prepare scan directory on disk
        scan_dir = self.container.scan_repository.prepare("SCAN_TEST_001")
        img_path = scan_dir / "images" / "test_sar.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

        # Write mock detection_results.json
        det_data = {
            "detections": [
                {
                    "x": 10,
                    "y": 10,
                    "width": 40,
                    "height": 40,
                    "confidence": 0.88,
                    "vessel_class": "Cargo",
                    "reason_codes": ["CONFIRMED_WAKE"],
                },
                {
                    "x": 60,
                    "y": 60,
                    "width": 30,
                    "height": 30,
                    "confidence": 0.62,
                    "vessel_class": "Fishing",
                    "reason_codes": ["UNVERIFIED"],
                },
            ]
        }
        (scan_dir / "detection_results.json").write_text(json.dumps(det_data), encoding="utf-8")

        # Save Scan entity
        scan = Scan(
            folder_name="SCAN_TEST_001",
            bbox=BoundingBox(min_longitude=103.0, min_latitude=1.0, max_longitude=104.0, max_latitude=2.0),
            acquisition=Acquisition(
                acquired_at=datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc),
                satellite="SENTINEL-1A",
                product_type="GRD",
            ),
            image_path=str(img_path),
        )
        self.container.scan_repository.save(scan)

        self.app = create_app(settings=settings, container=self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self):
        self.container.shutdown()
        self.temp_dir.cleanup()

    def test_review_page_render(self):
        resp = self.client.get("/review")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Analyst Review Queue", resp.data)

    def test_queue_listing_and_submission(self):
        # 1. Fetch queue - should automatically discover the 2 detections from SCAN_TEST_001
        resp = self.client.get("/api/review/queue")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["stats"]["total"], 2)
        self.assertEqual(data["stats"]["pending"], 2)

        # 2. Submit review decision: Accept detection 0
        submit_payload = {
            "scan_id": "SCAN_TEST_001",
            "detection_idx": 0,
            "disposition": "accepted",
            "reviewer_id": "analyst-42",
            "comments": "Confirmed cargo vessel with visible wake",
            "corrected_bbox": {"min_x": 12, "min_y": 12, "max_x": 52, "max_y": 52},
            "reason_codes": ["CONFIRMED_WAKE", "RADAR_REFLECTANCE_VALID"],
        }
        submit_resp = self.client.post("/api/review/submit", json=submit_payload)
        self.assertEqual(submit_resp.status_code, 200)
        res_data = submit_resp.get_json()
        self.assertTrue(res_data["success"])
        self.assertEqual(res_data["review"]["disposition"], "accepted")
        self.assertEqual(res_data["review"]["reviewer_id"], "analyst-42")

        # 3. Submit review decision: Reject detection 1
        reject_payload = {
            "scan_id": "SCAN_TEST_001",
            "detection_idx": 1,
            "disposition": "rejected",
            "reviewer_id": "analyst-42",
            "comments": "Wave crest false positive",
            "reason_codes": ["FALSE_POSITIVE_WAVE"],
        }
        reject_resp = self.client.post("/api/review/submit", json=reject_payload)
        self.assertEqual(reject_resp.status_code, 200)

        # 4. Check queue stats again
        queue_resp = self.client.get("/api/review/queue")
        data = queue_resp.get_json()
        self.assertEqual(data["stats"]["accepted"], 1)
        self.assertEqual(data["stats"]["rejected"], 1)
        self.assertEqual(data["stats"]["pending"], 0)

        # 5. Fetch review detail and immutable history
        rev_id = res_data["review"]["review_id"]
        detail_resp = self.client.get(f"/api/review/{rev_id}")
        self.assertEqual(detail_resp.status_code, 200)
        detail_data = detail_resp.get_json()
        self.assertTrue(detail_data["success"])
        self.assertEqual(len(detail_data["review"]["history"]), 2)  # CREATED + ACCEPTED

    def test_retraining_and_benchmark_dataset_generation(self):
        # Disposition 1 as accepted and 1 as rejected
        self.client.post("/api/review/submit", json={
            "scan_id": "SCAN_TEST_001",
            "detection_idx": 0,
            "disposition": "accepted",
            "reviewer_id": "evaluator",
            "corrected_bbox": {"min_x": 10, "min_y": 10, "max_x": 50, "max_y": 50},
        })
        self.client.post("/api/review/submit", json={
            "scan_id": "SCAN_TEST_001",
            "detection_idx": 1,
            "disposition": "rejected",
            "reviewer_id": "evaluator",
        })

        # Test Benchmark metrics
        bench_resp = self.client.get("/api/review/datasets/benchmark?iou_threshold=0.5")
        self.assertEqual(bench_resp.status_code, 200)
        bench_data = bench_resp.get_json()
        self.assertTrue(bench_data["success"])
        metrics = bench_data["benchmark"]["metrics"]
        self.assertEqual(metrics["true_positives"], 1)
        self.assertEqual(metrics["false_positives"], 1)
        self.assertAlmostEqual(metrics["precision"], 0.5)

        # Test Retraining dataset
        retrain_resp = self.client.get("/api/review/datasets/retraining?format=yolo")
        self.assertEqual(retrain_resp.status_code, 200)
        retrain_data = retrain_resp.get_json()
        self.assertTrue(retrain_data["success"])
        summary = retrain_data["dataset"]["summary"]
        self.assertEqual(summary["positive_samples"], 1)
        self.assertEqual(summary["hard_negatives"], 1)

        # Test Build endpoint
        build_resp = self.client.post("/api/review/datasets/build")
        self.assertEqual(build_resp.status_code, 200)

        # Test Export JSON bundle download
        export_resp = self.client.get("/api/review/datasets/export")
        self.assertEqual(export_resp.status_code, 200)
        self.assertEqual(export_resp.content_type, "application/json")


if __name__ == "__main__":
    unittest.main()
