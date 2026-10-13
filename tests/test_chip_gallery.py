import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone

import cv2
import numpy as np

from sentinel_analysis.application.use_cases.generate_chip_gallery import GenerateChipGallery
from sentinel_analysis.domain.chip_gallery import (
    CandidateChipGallery,
    ChipArtifact,
    ChipModality,
)
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan


class MockScanRepo:
    def __init__(self, scans=None):
        self._scans = {s.folder_name: s for s in (scans or [])}

    def get(self, folder_name: str):
        return self._scans.get(folder_name)

    def list(self):
        return list(self._scans.values())


class TestChipGallery(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

        # Create synthetic scan directory
        self.scan_folder = "S1A_IW_GRDH_1SDV_20260401_TEST"
        self.scan_path = self.work_dir / self.scan_folder
        self.scan_path.mkdir(parents=True, exist_ok=True)

        # Create synthetic SAR image (500x500)
        self.img_file = self.scan_path / "measurement.png"
        img = np.full((500, 500), 25, dtype=np.uint8)  # Sea clutter
        # Draw bright vessel return at (200, 200)
        cv2.rectangle(img, (180, 180), (220, 220), 240, -1)
        cv2.imwrite(str(self.img_file), img)

        # Create detection_results.json
        self.detections = [
            {
                "detection_id": "det_0",
                "x": 180,
                "y": 180,
                "width": 40,
                "height": 40,
                "confidence": 0.94,
                "length_m": 120.0,
                "width_m": 24.0,
                "heading": 45.0,
                "is_dark": True,
                "latitude": 1.250,
                "longitude": 103.750,
            },
            {
                "detection_id": "det_1",
                "x": 350,
                "y": 350,
                "width": 30,
                "height": 30,
                "confidence": 0.88,
                "length_m": 65.0,
                "width_m": 14.0,
                "heading": 180.0,
                "is_dark": False,
                "mmsi": "563001234",
                "latitude": 1.280,
                "longitude": 103.780,
            },
        ]
        det_file = self.scan_path / "detection_results.json"
        det_file.write_text(json.dumps({"detections": self.detections}), encoding="utf-8")

        self.scan = Scan(
            folder_name=self.scan_folder,
            bbox=BoundingBox(103.7, 1.2, 103.8, 1.3),
            acquisition=Acquisition(
                acquired_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc),
                satellite="SENTINEL-1A",
                product_type="GRD",
            ),
            image_path=str(self.img_file),
        )
        self.repo = MockScanRepo([self.scan])
        self.use_case = GenerateChipGallery(self.repo)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_domain_model_serialization(self):
        chip = ChipArtifact(
            modality=ChipModality.SAR,
            available=True,
            status="SUCCESS",
            image_data_uri="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            width=256,
            height=256,
            metadata={"snr_db": 18.5},
        )
        cdict = chip.to_dict()
        self.assertEqual(cdict["modality"], "SAR")
        self.assertTrue(cdict["available"])
        self.assertEqual(cdict["metadata"]["snr_db"], 18.5)

        gallery = CandidateChipGallery(
            detection_index=0,
            scan_id="test_scan",
            target_coordinates={"latitude": 1.25, "longitude": 103.75},
            chips={"SAR": chip},
            summary={"sar_snr_db": 18.5},
        )
        gdict = gallery.to_dict()
        self.assertEqual(gdict["detection_index"], 0)
        self.assertIn("SAR", gdict["chips"])

    def test_generate_chip_gallery_all_modalities(self):
        result = self.use_case.execute(scan_id=self.scan_folder, detection_idx=0, chip_size=128)
        self.assertEqual(result["scan_id"], self.scan_folder)
        self.assertEqual(result["detection_index"], 0)

        # Check target coordinates
        coords = result["target_coordinates"]
        self.assertAlmostEqual(coords["latitude"], 1.250, places=3)
        self.assertAlmostEqual(coords["longitude"], 103.750, places=3)
        self.assertTrue(coords["is_dark"])

        # Check all 4 modalities are populated
        chips = result["chips"]
        self.assertIn(ChipModality.SAR.value, chips)
        self.assertIn(ChipModality.OPTICAL.value, chips)
        self.assertIn(ChipModality.PREVIOUS_PASS.value, chips)
        self.assertIn(ChipModality.AIS_CONTEXT.value, chips)

        # Check SAR Chip
        sar_chip = chips[ChipModality.SAR.value]
        self.assertEqual(sar_chip["modality"], "SAR")
        self.assertTrue(sar_chip["available"])
        self.assertEqual(sar_chip["status"], "SUCCESS")
        self.assertTrue(sar_chip["image_data_uri"].startswith("data:image/png;base64,"))
        self.assertEqual(sar_chip["width"], 128)
        self.assertEqual(sar_chip["height"], 128)
        self.assertIn("snr_db", sar_chip["metadata"])
        self.assertIn("rcs_dbsm", sar_chip["metadata"])

        # Check AIS Context Tactical Map Chip
        ais_chip = chips[ChipModality.AIS_CONTEXT.value]
        self.assertEqual(ais_chip["modality"], "AIS_CONTEXT")
        self.assertTrue(ais_chip["available"])
        self.assertEqual(ais_chip["status"], "SUCCESS")
        self.assertTrue(ais_chip["image_data_uri"].startswith("data:image/png;base64,"))
        self.assertTrue(ais_chip["metadata"]["is_dark"])

        # Check Optical Card (fallback card when no concurrent optical scene)
        opt_chip = chips[ChipModality.OPTICAL.value]
        self.assertTrue(opt_chip["image_data_uri"].startswith("data:image/png;base64,"))

        # Check Previous Pass Card
        prev_chip = chips[ChipModality.PREVIOUS_PASS.value]
        self.assertTrue(prev_chip["image_data_uri"].startswith("data:image/png;base64,"))

        # Multi-sensor summary and tactical assessment
        summary = result["summary"]
        self.assertIsNotNone(summary["tactical_assessment"])
        self.assertIn("Non-broadcasting dark target", summary["tactical_assessment"])

    def test_generate_chip_gallery_correlated_target(self):
        # Detection index 1 is correlated with MMSI 563001234
        result = self.use_case.execute(scan_id=self.scan_folder, detection_idx=1, chip_size=128)
        coords = result["target_coordinates"]
        self.assertFalse(coords["is_dark"])
        self.assertEqual(coords["mmsi"], "563001234")

        summary = result["summary"]
        self.assertIn("563001234", summary["tactical_assessment"])

    def test_execute_all_detections(self):
        galleries = self.use_case.execute_all(scan_id=self.scan_folder, chip_size=128)
        self.assertEqual(len(galleries), 2)
        self.assertEqual(galleries[0]["detection_index"], 0)
        self.assertEqual(galleries[1]["detection_index"], 1)

    def test_scan_not_found_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.use_case.execute("NON_EXISTENT_SCAN", 0)


if __name__ == "__main__":
    unittest.main()
