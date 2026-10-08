"""Unit and integration tests for Multi-Temporal SAR Coherence and Change Detection."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from sentinel_analysis.application.use_cases.detect_sar_changes import (
    ComputeSARChangeDetection,
)
from sentinel_analysis.domain.entities import (
    Acquisition,
    BoundingBox,
    MultiTemporalChangeReport,
    Scan,
    TemporalChangePoint,
)
from sentinel_analysis.infrastructure.sar_processing.change_detection import (
    NumpySARChangeDetector,
)


class DummyScanRepo:
    def __init__(self, scans: dict[str, Scan]):
        self._scans = scans

    def get(self, folder_name: str) -> Scan | None:
        return self._scans.get(folder_name)

    def get_scan(self, folder_name: str) -> Scan | None:
        return self._scans.get(folder_name)


class TestSARChangeDetection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        # Create synthetic images (100x100 pixels)
        # T1 (reference pass)
        ref_arr = np.full((100, 100), 10, dtype=np.uint8)
        # Departed ship on T1 at (25, 25)
        ref_arr[23:27, 23:27] = 160
        # Persistent oil platform on both T1 and T2 at (50, 50)
        ref_arr[48:53, 48:53] = 180

        self.ref_scan_dir = self.base_path / "scan_t1_ref"
        self.ref_scan_dir.mkdir(parents=True, exist_ok=True)
        self.ref_img_path = self.ref_scan_dir / "sar_image.png"
        Image.fromarray(ref_arr).save(self.ref_img_path)

        # T2 (target pass)
        tgt_arr = np.full((100, 100), 10, dtype=np.uint8)
        # Arrived ship on T2 at (75, 75)
        tgt_arr[73:77, 73:77] = 170
        # Persistent oil platform unchanged on T2 at (50, 50)
        tgt_arr[48:53, 48:53] = 175

        self.tgt_scan_dir = self.base_path / "scan_t2_tgt"
        self.tgt_scan_dir.mkdir(parents=True, exist_ok=True)
        self.tgt_img_path = self.tgt_scan_dir / "sar_image.png"
        Image.fromarray(tgt_arr).save(self.tgt_img_path)

        self.bbox = BoundingBox(
            min_longitude=103.0,
            min_latitude=1.0,
            max_longitude=104.0,
            max_latitude=2.0,
        )

        self.scan_ref = Scan(
            folder_name="scan_t1_ref",
            bbox=self.bbox,
            acquisition=Acquisition(
                acquired_at=datetime(2026, 2, 1, 6, 0, 0, tzinfo=timezone.utc),
                satellite="Sentinel-1A",
                product_type="GRD",
            ),
            image_path=str(self.ref_img_path),
        )

        self.scan_tgt = Scan(
            folder_name="scan_t2_tgt",
            bbox=self.bbox,
            acquisition=Acquisition(
                acquired_at=datetime(2026, 2, 13, 6, 0, 0, tzinfo=timezone.utc),
                satellite="Sentinel-1A",
                product_type="GRD",
            ),
            image_path=str(self.tgt_img_path),
        )

        self.repo = DummyScanRepo({
            "scan_t1_ref": self.scan_ref,
            "scan_t2_tgt": self.scan_tgt,
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_numpy_sar_change_detector_direct(self):
        detector = NumpySARChangeDetector()
        out_map = self.tgt_scan_dir / "change_map_direct.png"

        result = detector.compute_change_map(
            reference_image_path=self.ref_img_path,
            target_image_path=self.tgt_img_path,
            output_path=out_map,
            threshold_db=4.0,
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(out_map.is_file())
        self.assertGreaterEqual(result["arrived_count"], 1)
        self.assertGreaterEqual(result["departed_count"], 1)
        self.assertGreaterEqual(result["persistent_structures_count"], 1)

        # Verify output composite image dimensions
        with Image.open(out_map) as img:
            self.assertEqual(img.size, (100, 100))
            self.assertEqual(img.mode, "RGB")

    def test_compute_sar_change_detection_use_case(self):
        detector = NumpySARChangeDetector()
        use_case = ComputeSARChangeDetection(self.repo, detector)

        result = use_case.execute(
            target_folder="scan_t2_tgt",
            reference_folder="scan_t1_ref",
            threshold_db=4.0,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["target_scan"], "scan_t2_tgt")
        self.assertEqual(result["reference_scan"], "scan_t1_ref")
        self.assertGreaterEqual(result["arrived_count"], 1)
        self.assertGreaterEqual(result["departed_count"], 1)
        self.assertGreaterEqual(result["persistent_structures_count"], 1)

        # Check persisted change report JSON
        report_json = self.tgt_scan_dir / "change_report_scan_t1_ref.json"
        self.assertTrue(report_json.is_file())

        # Check GeoJSON FeatureCollection
        geojson = result["geojson"]
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertTrue(len(geojson["features"]) >= 3)

        change_types = {f["properties"]["change_type"] for f in geojson["features"]}
        self.assertIn("ARRIVED", change_types)
        self.assertIn("DEPARTED", change_types)
        self.assertIn("PERSISTENT_STRUCTURE", change_types)

    def test_missing_scan_raises_value_error(self):
        detector = NumpySARChangeDetector()
        use_case = ComputeSARChangeDetection(self.repo, detector)

        with self.assertRaises(ValueError):
            use_case.execute(target_folder="non_existent", reference_folder="scan_t1_ref")

        with self.assertRaises(ValueError):
            use_case.execute(target_folder="scan_t2_tgt", reference_folder="non_existent")


if __name__ == "__main__":
    unittest.main()
