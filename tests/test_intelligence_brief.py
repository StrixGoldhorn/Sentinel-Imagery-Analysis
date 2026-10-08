"""Unit and integration tests for Maritime Intelligence PDF Briefing."""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from sentinel_analysis.application.use_cases.generate_briefing import GenerateIntelligenceBrief
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.infrastructure.reporting.pdf_brief import MatplotlibIntelligenceBriefGenerator
from sentinel_analysis.interfaces.web.application import create_app


class TestIntelligenceBriefGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.tmp_dir.name) / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.scan_repo = FilesystemScanRepository(self.output_root)

        self.scan_folder = "test_brief_scan"
        scan_dir = self.scan_repo.prepare(self.scan_folder)
        self.img_path = scan_dir / "images" / f"{self.scan_folder}_stitched.png"

        # Write 256x256 test image
        img_arr = np.full((256, 256), 100, dtype=np.uint8)
        # Add high-intensity radar targets
        img_arr[60:70, 60:70] = 240
        img_arr[150:160, 180:190] = 250
        Image.fromarray(img_arr).save(self.img_path)

        self.bbox = BoundingBox(103.5, 1.1, 104.2, 1.6)
        self.acq = Acquisition(
            datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc),
            "SENTINEL-1A",
            "sentinel-1-grd",
            product_id="S1A_IW_GRDH_TEST",
            polarizations=("VV", "VH"),
            orbit_direction="DESCENDING",
            relative_orbit=12,
        )

        metadata = {
            "aoi_name": "Singapore Strait Outer",
            "detections": [
                {
                    "pixel_x": 64,
                    "pixel_y": 64,
                    "latitude": 1.45,
                    "longitude": 103.85,
                    "length_m": 72.0,  # >= 45m and dark -> SOLAS violation
                    "width_m": 14.0,
                    "confidence": 0.94,
                    "is_dark": True,
                },
                {
                    "pixel_x": 185,
                    "pixel_y": 155,
                    "latitude": 1.25,
                    "longitude": 104.05,
                    "length_m": 180.0,
                    "width_m": 28.0,
                    "confidence": 0.98,
                    "is_dark": False,  # Correlated with AIS
                },
                {
                    "pixel_x": 30,
                    "pixel_y": 200,
                    "latitude": 1.18,
                    "longitude": 103.60,
                    "length_m": 25.0,  # Dark but small skiff (< 45m)
                    "width_m": 6.0,
                    "confidence": 0.72,
                    "is_dark": True,
                },
            ],
        }

        self.scan = Scan(self.scan_folder, self.bbox, self.acq, str(self.img_path), metadata)
        self.scan_repo.save(self.scan)

        self.brief_gen = MatplotlibIntelligenceBriefGenerator()
        self.use_case = GenerateIntelligenceBrief(self.scan_repo, self.brief_gen)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_summary_calculation(self):
        summary = self.use_case.generate_summary(self.scan_folder)
        self.assertEqual(summary["total_vessels"], 3)
        self.assertEqual(summary["dark_vessels"], 2)
        self.assertEqual(summary["ais_correlated"], 1)
        self.assertAlmostEqual(summary["dark_percentage"], 66.7, places=1)
        self.assertEqual(summary["solas_suspect_count"], 1)  # Only the 72m dark vessel
        self.assertEqual(summary["aoi_name"], "Singapore Strait Outer")

    def test_pdf_generation_file_validity(self):
        pdf_path = self.use_case.execute(self.scan_folder)
        self.assertTrue(pdf_path.is_file())
        self.assertTrue(pdf_path.name.endswith(".pdf"))
        self.assertGreater(pdf_path.stat().st_size, 1000)

        with open(pdf_path, "rb") as f:
            header = f.read(5)
            self.assertEqual(header, b"%PDF-")


class TestIntelligenceBriefWebAPI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp_dir.name) / "test_brief.db"
        output_root = Path(self.tmp_dir.name) / "output"
        output_root.mkdir(parents=True, exist_ok=True)

        scan_repo = FilesystemScanRepository(output_root)
        self.scan_folder = "api_brief_test"
        scan_dir = scan_repo.prepare(self.scan_folder)
        img_path = scan_dir / "images" / f"{self.scan_folder}_stitched.png"
        Image.fromarray(np.full((64, 64), 180, dtype=np.uint8)).save(img_path)

        bbox = BoundingBox(12.0, 42.0, 13.0, 43.0)
        acq = Acquisition(
            datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
            "SENTINEL-1A",
            "sentinel-1-grd",
            product_id="S1_API_TEST",
            polarizations=("VV",),
        )
        metadata = {
            "aoi_name": "Ligurian Basin",
            "detections": [
                {"latitude": 42.5, "longitude": 12.5, "length_m": 80.0, "is_dark": True, "confidence": 0.9}
            ],
        }
        scan_repo.save(Scan(self.scan_folder, bbox, acq, str(img_path), metadata))

        settings = Settings(
            copernicus_username="test_user",
            copernicus_password="test_pass",
            n2yo_api_key="test_key",
            project_root=Path(__file__).resolve().parent.parent,
            database_path=db_path,
            output_root=output_root,
            cache_root=Path(self.tmp_dir.name) / "cache",
        )
        container = ApplicationContainer(settings)
        app = create_app(settings=settings, container=container, start_background_workers=False)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_briefing_summary_json_endpoint(self):
        resp = self.client.get(f"/api/scan/{self.scan_folder}/briefing")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["scan_folder"], self.scan_folder)
        self.assertEqual(data["total_vessels"], 1)
        self.assertEqual(data["dark_vessels"], 1)
        self.assertEqual(data["solas_suspect_count"], 1)

    def test_briefing_pdf_endpoint(self):
        resp = self.client.get(f"/api/scan/{self.scan_folder}/briefing/pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/pdf")
        self.assertTrue(resp.data.startswith(b"%PDF-"))
        resp.close()


if __name__ == "__main__":
    unittest.main()
