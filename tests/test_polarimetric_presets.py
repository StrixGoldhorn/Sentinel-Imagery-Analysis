"""Unit tests for SAR polarimetric presets and change-detection composites."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from sentinel_analysis.application.use_cases.get_polarimetric_presets import (
    GetPolarimetricPresetsUseCase,
    RenderPolarimetricPresetUseCase,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.polarimetry import (
    PolarimetricPresetCatalog,
    PolarimetricPresetType,
)
from sentinel_analysis.infrastructure.imagery.polarimetry import OpenCVPolarimetricProcessor
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.interfaces.web.application import create_app


class FakeScanRepository:
    def __init__(self, scans: list[Scan]) -> None:
        self._scans = {s.folder_name: s for s in scans}

    def get(self, folder_name: str) -> Scan | None:
        return self._scans.get(folder_name)

    def list_recent(self, limit: int = 50) -> list[Scan]:
        return list(self._scans.values())[:limit]


class TestPolarimetricPresets(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        # Create dummy test image (100x100 grayscale)
        self.img_path = self.dir_path / "test_sar.png"
        raw_arr = np.random.randint(10, 80, (100, 100), dtype=np.uint8)
        # Add high backscatter ship target in center
        raw_arr[45:55, 45:55] = 230
        Image.fromarray(raw_arr).save(self.img_path)

        # Create baseline prior image for change detection
        self.prior_img_path = self.dir_path / "test_prior_sar.png"
        prior_arr = np.random.randint(10, 80, (100, 100), dtype=np.uint8)
        # Add a departed ship target elsewhere
        prior_arr[15:25, 15:25] = 230
        Image.fromarray(prior_arr).save(self.prior_img_path)

        # Scan entities
        self.scan1 = Scan(
            folder_name="scan_current",
            bbox=BoundingBox(103.0, 1.0, 104.0, 2.0),
            image_path=str(self.img_path),
            acquisition=Acquisition(
                acquired_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
                satellite="Sentinel-1",
                product_type="GRD",
                polarizations=("VV", "VH"),
            ),
        )

        self.scan_prior = Scan(
            folder_name="scan_prior",
            bbox=BoundingBox(103.1, 1.1, 103.9, 1.9),
            image_path=str(self.prior_img_path),
            acquisition=Acquisition(
                acquired_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
                satellite="Sentinel-1",
                product_type="GRD",
                polarizations=("VV", "VH"),
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_domain_catalog(self) -> None:
        presets = PolarimetricPresetCatalog.get_all()
        self.assertEqual(len(presets), 5)

        preset_ids = {p.preset_id for p in presets}
        expected = {"vv", "vh", "vv_vh_ratio", "false_color_composite", "change_detection"}
        self.assertEqual(preset_ids, expected)

        vv = PolarimetricPresetCatalog.get_preset("vv")
        self.assertEqual(vv.name, "VV")
        self.assertFalse(vv.supports_multitemporal)

        change = PolarimetricPresetCatalog.get_preset("change_detection")
        self.assertTrue(change.supports_multitemporal)
        self.assertIn("green", change.color_mapping)

        with self.assertRaises(ValueError):
            PolarimetricPresetCatalog.get_preset("invalid_preset_xyz")

    def test_opencv_processor_presets(self) -> None:
        processor = OpenCVPolarimetricProcessor()

        for preset_type in ("vv", "vh", "vv_vh_ratio", "false_color_composite"):
            png_bytes = processor.render_preset(
                image_path=self.img_path,
                preset_type=preset_type,
                gamma=1.1,
            )
            self.assertTrue(isinstance(png_bytes, bytes))
            self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        # Test change detection with prior image
        change_bytes = processor.render_preset(
            image_path=self.img_path,
            preset_type="change_detection",
            prior_image_path=self.prior_img_path,
        )
        self.assertTrue(change_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        # Test invalid preset
        with self.assertRaises(ValueError):
            processor.render_preset(self.img_path, "unknown_mode")

    def test_use_cases(self) -> None:
        repo = FakeScanRepository([self.scan1, self.scan_prior])
        processor = OpenCVPolarimetricProcessor()

        get_presets_uc = GetPolarimetricPresetsUseCase(repo)
        catalog_result = get_presets_uc.execute("scan_current")
        self.assertEqual(catalog_result["scan_id"], "scan_current")
        self.assertEqual(len(catalog_result["presets"]), 5)
        self.assertGreaterEqual(len(catalog_result["baseline_candidates"]), 1)
        self.assertEqual(catalog_result["baseline_candidates"][0]["scan_id"], "scan_prior")

        render_uc = RenderPolarimetricPresetUseCase(repo, processor)
        # Test rendering false color composite
        rgb_bytes = render_uc.execute("scan_current", "false_color_composite")
        self.assertTrue(rgb_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        # Test change detection auto-resolving prior scan
        cd_bytes = render_uc.execute("scan_current", "change_detection")
        self.assertTrue(cd_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        # Test non-existent scan
        with self.assertRaises(FileNotFoundError):
            get_presets_uc.execute("non_existent_scan")

    def test_flask_routes(self) -> None:
        # Create full filesystem repository and test via Flask test client
        scans_dir = self.dir_path / "scans"
        repo = FilesystemScanRepository(scans_dir)
        workspace1 = repo.prepare("scan_web_1")
        workspace_prior = repo.prepare("scan_web_prior")

        img1 = workspace1 / "images" / "sar_image.png"
        img_prior = workspace_prior / "images" / "sar_image.png"

        Image.fromarray(np.full((80, 80), 50, dtype=np.uint8)).save(img1)
        Image.fromarray(np.full((80, 80), 60, dtype=np.uint8)).save(img_prior)

        repo.save(Scan(
            folder_name="scan_web_1",
            bbox=BoundingBox(10.0, 50.0, 11.0, 51.0),
            image_path=str(img1),
            acquisition=Acquisition(
                acquired_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
                satellite="Sentinel-1",
                product_type="GRD",
            ),
        ))
        repo.save(Scan(
            folder_name="scan_web_prior",
            bbox=BoundingBox(10.0, 50.0, 11.0, 51.0),
            image_path=str(img_prior),
            acquisition=Acquisition(
                acquired_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                satellite="Sentinel-1",
                product_type="GRD",
            ),
        ))

        settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=self.dir_path / "test.db",
            output_root=scans_dir,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
        )
        container = ApplicationContainer(settings)
        app = create_app(settings, container, start_background_workers=False)
        client = app.test_client()

        # 1. Test GET /api/scans/<id>/polarimetry/presets
        resp = client.get("/api/scans/scan_web_1/polarimetry/presets")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["scan_id"], "scan_web_1")
        self.assertEqual(len(data["presets"]), 5)
        self.assertGreaterEqual(len(data["baseline_candidates"]), 1)

        # 2. Test GET /api/scans/<id>/polarimetry/<preset>.png
        for p in ("vv", "vh", "vv_vh_ratio", "false_color_composite", "change_detection"):
            img_resp = client.get(f"/api/scans/scan_web_1/polarimetry/{p}.png?gamma=1.0")
            self.assertEqual(img_resp.status_code, 200)
            self.assertEqual(img_resp.mimetype, "image/png")
            self.assertTrue(img_resp.data.startswith(b"\x89PNG\r\n\x1a\n"))

        # 3. Test invalid preset
        bad_resp = client.get("/api/scans/scan_web_1/polarimetry/invalid_preset.png")
        self.assertEqual(bad_resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
