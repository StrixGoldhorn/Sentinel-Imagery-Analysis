"""Tests for SAR preprocessing provenance metadata lineage according to geospatial intelligence standards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection
from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.domain.provenance import (
    PreprocessingProvenance,
    build_preprocessing_provenance,
    compute_source_checksum,
)
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.detection.detection_saver import save_detection_results


class PreprocessingProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    def test_checksum_computation(self) -> None:
        test_file = self.temp_dir / "test_raster.bin"
        test_file.write_bytes(b"SENTINEL_SAR_PAYLOAD_DATA_12345")

        checksum = compute_source_checksum(test_file)
        self.assertEqual(len(checksum), 64)
        self.assertEqual(compute_source_checksum(None), "")
        self.assertEqual(compute_source_checksum(self.temp_dir / "non_existent.bin"), "")

    def test_dataclass_defaults_and_to_dict(self) -> None:
        prov = PreprocessingProvenance()
        d = prov.to_dict()

        expected_keys = {
            "orbit",
            "product_id",
            "polarization",
            "processing_baseline",
            "calibration_method",
            "terrain_correction",
            "dem",
            "speckle_filtering",
            "pixel_spacing",
            "model_version",
            "thresholds",
            "source_checksum",
            "source_checksums",
        }
        self.assertEqual(set(d.keys()), expected_keys)
        self.assertEqual(d["orbit"], "DESCENDING")
        self.assertEqual(d["polarization"], "VV")
        self.assertEqual(d["calibration_method"], "radiometric_sigma0")
        self.assertIsInstance(d["pixel_spacing"], float)

    def test_build_provenance_from_scan_entity(self) -> None:
        acq = Acquisition(
            acquired_at=datetime(2026, 8, 15, 10, 30, tzinfo=timezone.utc),
            satellite="Sentinel-1A",
            product_type="sentinel-1-grd",
            orbit_direction="ASCENDING",
            relative_orbit=142,
            polarizations=["VV", "VH"],
            product_id="S1A_IW_GRDH_1SDV_20260815T103000_TEST",
        )
        scan = Scan(
            folder_name="scan_20260815_test",
            bbox=BoundingBox(103.5, 1.1, 104.0, 1.5),
            acquisition=acq,
            image_path=str(self.temp_dir / "dummy_image.png"),
            metadata={
                "processing_baseline": "003.52",
                "calibration_method": "radiometric_sigma0",
                "speckle_filtering": "Lee (window=7, var=0.25)",
            },
        )

        prov = build_preprocessing_provenance(scan=scan)
        self.assertIn("ASCENDING", prov["orbit"])
        self.assertIn("142", prov["orbit"])
        self.assertEqual(prov["product_id"], "S1A_IW_GRDH_1SDV_20260815T103000_TEST")
        self.assertIn("VV", prov["polarization"])
        self.assertIn("VH", prov["polarization"])
        self.assertEqual(prov["processing_baseline"], "003.52")
        self.assertEqual(prov["calibration_method"], "radiometric_sigma0")
        self.assertIn("Lee", prov["speckle_filtering"])
        self.assertEqual(len(prov["source_checksum"]), 64)

    def test_ship_detection_provenance_validation(self) -> None:
        # Valid dictionary provenance
        det = ShipDetection(
            x=10,
            y=20,
            width=30,
            height=40,
            provenance={"orbit": "DESCENDING", "product_id": "TEST_001"},
        )
        self.assertEqual(det.provenance["orbit"], "DESCENDING")

        # Invalid non-dictionary provenance
        with self.assertRaises(DomainValidationError):
            ShipDetection(
                x=10,
                y=20,
                width=30,
                height=40,
                provenance="invalid_string_provenance",  # type: ignore
            )

    def test_classical_detector_attaches_provenance(self) -> None:
        img_file = self.temp_dir / "sar_synthetic.png"
        img = np.zeros((100, 100), dtype=np.uint8)
        # Create a bright ship target
        img[45:55, 45:55] = 250
        Image.fromarray(img).save(img_file)

        detector = ClassicalShipDetector(minimum_area=10, maximum_area=500)
        res = detector.detect(img_file, threshold=40)

        self.assertGreaterEqual(len(res.detections), 1)
        det = res.detections[0]
        self.assertIsNotNone(det.provenance)
        self.assertIn("model_version", det.provenance)
        self.assertIn("thresholds", det.provenance)
        self.assertEqual(det.provenance["pixel_spacing"], 10.0)
        self.assertEqual(len(det.provenance["source_checksum"]), 64)

    def test_detection_saver_exports_all_twelve_metadata_dimensions(self) -> None:
        img_file = self.temp_dir / "target_scan.png"
        Image.new("L", (100, 100), 50).save(img_file)

        det = ShipDetection(
            x=45,
            y=45,
            width=10,
            height=10,
            confidence=0.92,
            length=100.0,
            beam=20.0,
            provenance={
                "orbit": "ASCENDING (rel 55)",
                "product_id": "S1A_IW_GRDH_TEST_PROVENANCE",
                "polarization": "VV / VH (Dual-Pol)",
                "processing_baseline": "003.52",
                "calibration_method": "radiometric_sigma0",
                "terrain_correction": "Range-Doppler RTC",
                "dem": "Copernicus 30m GLO-30 DEM",
                "speckle_filtering": "Lee (window=7, var=0.25)",
                "pixel_spacing": 10.0,
                "model_version": "Classical-CFAR-OBB-v2.1",
                "thresholds": {"threshold": 40, "coastal_buffer_pixels": 81},
                "source_checksum": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            },
        )

        saved = save_detection_results(
            image_path=img_file,
            detections=[det],
            image_width=100,
            image_height=100,
            bbox=BoundingBox(103.0, 1.0, 104.0, 2.0),
        )

        # 1. Check returned structure
        self.assertEqual(saved["ship_count"], 1)

        # 2. Check JSON output
        json_path = self.temp_dir / "detection_results.json"
        self.assertTrue(json_path.is_file())
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
        first_det = json_data["detections"][0]

        all_12_keys = [
            "orbit",
            "product_id",
            "polarization",
            "processing_baseline",
            "calibration_method",
            "terrain_correction",
            "dem",
            "speckle_filtering",
            "pixel_spacing",
            "model_version",
            "thresholds",
            "source_checksum",
        ]
        for key in all_12_keys:
            self.assertIn(key, first_det, f"Missing key {key} in serialized detection")
            self.assertEqual(first_det[key], det.provenance[key])

        # 3. Check GeoJSON output
        geojson_path = self.temp_dir / "detections.geojson"
        self.assertTrue(geojson_path.is_file())
        geojson_data = json.loads(geojson_path.read_text(encoding="utf-8"))
        feat_props = geojson_data["features"][0]["properties"]

        for key in all_12_keys:
            self.assertIn(key, feat_props, f"Missing key {key} in GeoJSON properties")
            self.assertEqual(feat_props[key], det.provenance[key])


if __name__ == "__main__":
    unittest.main()
