"""Tests for SAR radiometry, histogram extraction, contrast, gamma, and despeckling."""

from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

from sentinel_analysis.application.use_cases.enhance_sar_radiometry import (
    EnhanceScanRadiometry,
    GetScanHistogram,
)
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.sar_radiometry import (
    DespeckleFilter,
    HistogramData,
    RadiometryAdjustment,
    RadiometryPreset,
)
from sentinel_analysis.infrastructure.imagery.sar_radiometry import OpenCVSarRadiometryProcessor


class TestSARRadiometry(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name)

        # Create a synthetic 128x128 SAR test image with noise
        self.test_img_path = self.output_root / "test_sar.png"
        np.random.seed(42)
        base = np.linspace(20, 180, 128 * 128, dtype=np.float32).reshape((128, 128))
        noise = np.random.normal(0, 15, (128, 128)).astype(np.float32)
        synthetic = np.clip(base + noise, 0, 255).astype(np.uint8)
        # Add high-intensity simulated ship targets
        synthetic[40:45, 40:45] = 250
        synthetic[80:85, 80:85] = 245
        Image.fromarray(synthetic).save(self.test_img_path)

        self.processor = OpenCVSarRadiometryProcessor()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_domain_adjustment_validation_and_presets(self):
        adj = RadiometryAdjustment(
            contrast_min=300,  # should clamp
            contrast_max=-10,  # should adjust above min
            gamma=0.01,        # should clamp to 0.1
            filter_kernel_size=4,  # should become 5 (odd)
        )
        self.assertLessEqual(adj.contrast_min, 254.0)
        self.assertGreater(adj.contrast_max, adj.contrast_min)
        self.assertEqual(adj.gamma, 0.1)
        self.assertEqual(adj.filter_kernel_size, 5)

        presets = RadiometryPreset.get_presets()
        self.assertIn("DEFAULT", presets)
        self.assertIn("LEE_DESPECKLE", presets)
        self.assertEqual(presets["LEE_DESPECKLE"].despeckle_filter, "lee")

    def test_histogram_computation(self):
        hist_data = self.processor.compute_histogram(self.test_img_path, num_bins=256)
        self.assertEqual(len(hist_data.bins), 256)
        self.assertEqual(len(hist_data.counts), 256)
        self.assertEqual(hist_data.total_pixels, 128 * 128)
        self.assertEqual(len(hist_data.cdf), 256)
        self.assertAlmostEqual(hist_data.cdf[-1], 1.0, places=2)
        self.assertIn("p50", hist_data.percentiles)
        self.assertGreater(hist_data.mean, 0)

        d = hist_data.to_dict()
        self.assertIn("mean", d)
        self.assertIn("percentiles", d)

    def test_despeckle_filters(self):
        for f_type in ("none", "lee", "frost", "median", "boxcar"):
            adj = RadiometryAdjustment(
                contrast_min=10.0,
                contrast_max=240.0,
                gamma=1.1,
                log_stretch=True,
                despeckle_filter=f_type,
                filter_kernel_size=5,
            )
            raw_png = self.processor.enhance_radiometry(self.test_img_path, adj)
            self.assertIsInstance(raw_png, bytes)
            self.assertGreater(len(raw_png), 100)

            # Check that it's a valid readable image
            with Image.open(io.BytesIO(raw_png)) as img:
                self.assertEqual(img.size, (128, 128))

    def test_use_cases(self):
        bbox = BoundingBox(103.7, 1.3, 103.9, 1.4)
        acq = Acquisition(
            acquired_at=datetime(2026, 8, 15, 10, 30, tzinfo=timezone.utc),
            satellite="Sentinel-1",
            product_type="GRD",
            polarizations=("VV", "VH"),
        )
        scan = Scan(
            folder_name="scan_rad_01",
            bbox=bbox,
            acquisition=acq,
            image_path=str(self.test_img_path),
        )

        mock_scan_repo = MagicMock()
        mock_scan_repo.get.return_value = scan

        # Test GetScanHistogram use case
        get_hist = GetScanHistogram(mock_scan_repo, self.processor)
        hist_res = get_hist.execute("scan_rad_01")
        self.assertEqual(hist_res["scan_id"], "scan_rad_01")
        self.assertEqual(hist_res["polarization"], "VV")
        self.assertIn("histogram", hist_res)
        self.assertIn("presets", hist_res)

        # Test EnhanceScanRadiometry use case
        enhance_use_case = EnhanceScanRadiometry(mock_scan_repo, self.processor)
        adj = RadiometryAdjustment(contrast_min=20, contrast_max=220, gamma=0.9, despeckle_filter="lee")
        enhanced_bytes = enhance_use_case.execute("scan_rad_01", adj)
        self.assertGreater(len(enhanced_bytes), 0)

    def test_flask_radiometry_endpoints(self):
        from sentinel_analysis.bootstrap.config import Settings
        from sentinel_analysis.interfaces.web.application import create_app

        bbox = BoundingBox(103.7, 1.3, 103.9, 1.4)
        acq = Acquisition(
            acquired_at=datetime(2026, 8, 15, 10, 30, tzinfo=timezone.utc),
            satellite="Sentinel-1",
            product_type="GRD",
            polarizations=("VV",),
        )
        scan = Scan(
            folder_name="scan_web_rad",
            bbox=bbox,
            acquisition=acq,
            image_path=str(self.test_img_path),
        )

        settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=self.output_root / "test.db",
            output_root=self.output_root,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
        )

        mock_cnt = MagicMock()
        mock_cnt.settings = settings
        mock_cnt.scan_repository.get.return_value = scan
        mock_cnt.get_scan_histogram.execute.return_value = {
            "scan_id": "scan_web_rad",
            "polarization": "VV",
            "histogram": {"bins": [0, 1], "counts": [10, 20], "mean": 100.0, "std_dev": 25.0, "min_val": 0, "max_val": 255},
        }
        mock_cnt.enhance_scan_radiometry.execute.return_value = b"\x89PNG\r\n\x1a\nfake_png"

        app = create_app(settings, mock_cnt, start_background_workers=False)
        client = app.test_client()

        # 1. Test histogram endpoint
        hist_res = client.get("/api/scans/scan_web_rad/histogram")
        self.assertEqual(hist_res.status_code, 200)
        data = hist_res.get_json()
        self.assertEqual(data["scan_id"], "scan_web_rad")

        # 2. Test enhanced image endpoint
        img_res = client.get("/api/scans/scan_web_rad/enhanced-image.png?contrast_min=10&contrast_max=220&gamma=1.2&despeckle=lee")
        self.assertEqual(img_res.status_code, 200)
        self.assertEqual(img_res.mimetype, "image/png")


if __name__ == "__main__":
    unittest.main()
