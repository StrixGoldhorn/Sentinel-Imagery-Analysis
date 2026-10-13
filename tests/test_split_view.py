"""Unit and integration tests for synchronized multi-modality split views."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from PIL import Image

from sentinel_analysis.application.use_cases.get_synchronized_views import GetSynchronizedViews
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.synchronized_views import (
    LayerModality,
    LayerPane,
    SplitViewMode,
    SynchronizedSplitView,
    ViewportInfo,
)


class TestSynchronizedViews(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_domain_viewport_and_panes(self):
        vp = ViewportInfo(
            center_lat=1.35,
            center_lon=103.82,
            min_lat=1.2,
            min_lon=103.6,
            max_lat=1.5,
            max_lon=104.0,
            initial_zoom=12,
        )
        d = vp.to_dict()
        self.assertEqual(d["center"], [1.35, 103.82])
        self.assertEqual(d["initial_zoom"], 12)
        self.assertEqual(d["bounds"], [[1.2, 103.6], [1.5, 104.0]])

        pane = LayerPane(
            modality=LayerModality.SAR.value,
            title="SAR Amplitude",
            available=True,
            image_url="/media/scans/test/stitched.png",
            metadata={"pol": "VV"},
        )
        pd = pane.to_dict()
        self.assertEqual(pd["modality"], "sar")
        self.assertTrue(pd["available"])

        view = SynchronizedSplitView(
            scan_id="test_scan",
            viewport=vp,
            panes={"sar": pane},
            generated_at="2026-10-13T12:00:00Z",
        )
        vd = view.to_dict()
        self.assertEqual(vd["scan_id"], "test_scan")
        self.assertIn("sar", vd["panes"])
        self.assertEqual(vd["modes_supported"], ["dual", "quad"])

    def test_get_synchronized_views_use_case(self):
        scan_folder = self.output_root / "scan_sync_01"
        scan_folder.mkdir(parents=True, exist_ok=True)
        sar_img = scan_folder / "stitched.png"
        Image.new("RGB", (200, 200), (40, 40, 40)).save(sar_img)

        # Create optical image
        optical_img = scan_folder / "optical_image.png"
        Image.new("RGB", (200, 200), (30, 80, 160)).save(optical_img)

        # Create detections
        det_data = [
            {
                "x": 50,
                "y": 60,
                "width": 20,
                "height": 10,
                "latitude": 1.34,
                "longitude": 103.81,
                "confidence": 0.94,
                "is_dark": False,
                "mmsi": "567890123",
                "length": 85.0,
            },
            {
                "x": 120,
                "y": 140,
                "width": 15,
                "height": 8,
                "latitude": 1.36,
                "longitude": 103.85,
                "confidence": 0.88,
                "is_dark": True,
                "length": 45.0,
            },
        ]
        (scan_folder / "detection_results.json").write_text(json.dumps(det_data), encoding="utf-8")

        bbox = BoundingBox(103.7, 1.3, 103.9, 1.4)
        acq = Acquisition(
            acquired_at=datetime(2026, 8, 15, 10, 30, tzinfo=timezone.utc),
            satellite="Sentinel-1",
            product_type="GRD",
            polarizations=("VV", "VH"),
        )
        scan = Scan(
            folder_name="scan_sync_01",
            bbox=bbox,
            acquisition=acq,
            image_path=str(sar_img),
        )

        mock_scan_repo = MagicMock()
        mock_scan_repo.get.return_value = scan

        mock_ais_repo = MagicMock()
        mock_ais_repo.get_vessel_positions.return_value = [
            {
                "mmsi": "567890123",
                "vessel_name": "STAR CARRIER",
                "latitude": 1.3405,
                "longitude": 103.8105,
                "speed": 11.2,
                "heading": 85.0,
                "vessel_type": "Cargo",
            }
        ]

        use_case = GetSynchronizedViews(
            scan_repository=mock_scan_repo,
            ais_repository=mock_ais_repo,
        )

        result = use_case.execute("scan_sync_01")
        self.assertEqual(result["scan_id"], "scan_sync_01")
        self.assertIn("viewport", result)
        self.assertAlmostEqual(result["viewport"]["center_lat"], 1.35)
        self.assertAlmostEqual(result["viewport"]["center_lon"], 103.8)

        panes = result["panes"]
        self.assertIn("sar", panes)
        self.assertIn("optical", panes)
        self.assertIn("mask", panes)
        self.assertIn("ais", panes)

        # SAR checks
        self.assertTrue(panes["sar"]["available"])
        self.assertEqual(panes["sar"]["metadata"]["polarizations"], ["VV", "VH"])

        # Optical checks
        self.assertTrue(panes["optical"]["available"])
        self.assertEqual(panes["optical"]["metadata"]["has_optical_file"], True)

        # Detection Mask checks
        self.assertTrue(panes["mask"]["available"])
        self.assertEqual(panes["mask"]["metadata"]["total_detections"], 2)
        self.assertEqual(panes["mask"]["metadata"]["dark_targets"], 1)
        self.assertEqual(panes["mask"]["metadata"]["correlated_targets"], 1)
        features = panes["mask"]["vector_geojson"]["features"]
        self.assertEqual(len(features), 2)

        # AIS checks
        self.assertTrue(panes["ais"]["available"])
        ais_features = panes["ais"]["vector_geojson"]["features"]
        self.assertEqual(len(ais_features), 1)
        self.assertEqual(ais_features[0]["properties"]["mmsi"], "567890123")
        self.assertTrue(ais_features[0]["properties"]["is_correlated"])

    def test_get_synchronized_views_missing_scan_raises_error(self):
        mock_scan_repo = MagicMock()
        mock_scan_repo.get.return_value = None

        use_case = GetSynchronizedViews(scan_repository=mock_scan_repo)
        with self.assertRaises(FileNotFoundError):
            use_case.execute("non_existent_scan")

    def test_flask_split_view_endpoints(self):
        from sentinel_analysis.bootstrap.config import Settings
        from sentinel_analysis.interfaces.web.application import create_app

        scan_folder = self.output_root / "scan_web_01"
        scan_folder.mkdir(parents=True, exist_ok=True)
        sar_img = scan_folder / "stitched.png"
        Image.new("RGB", (64, 64), (100, 100, 100)).save(sar_img)

        bbox = BoundingBox(103.7, 1.3, 103.9, 1.4)
        acq = Acquisition(
            acquired_at=datetime(2026, 8, 15, 10, 30, tzinfo=timezone.utc),
            satellite="Sentinel-1",
            product_type="GRD",
            polarizations=("VV",),
        )
        scan = Scan(
            folder_name="scan_web_01",
            bbox=bbox,
            acquisition=acq,
            image_path=str(sar_img),
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
        mock_cnt.get_synchronized_views.execute.return_value = {
            "scan_id": "scan_web_01",
            "viewport": {"center": [1.35, 103.8]},
            "panes": {},
        }

        app = create_app(settings, mock_cnt, start_background_workers=False)
        client = app.test_client()

        # 1. Test JSON synchronized views metadata
        res = client.get("/api/scans/scan_web_01/synchronized-views")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["scan_id"], "scan_web_01")

        # 2. Test Detection Mask PNG endpoint
        mask_res = client.get("/api/scans/scan_web_01/detection-mask.png")
        self.assertEqual(mask_res.status_code, 200)
        self.assertEqual(mask_res.mimetype, "image/png")
        self.assertGreater(len(mask_res.data), 0)

        # 3. Test Optical layer PNG fallback (1x1 transparent png)
        opt_res = client.get("/api/scans/scan_web_01/optical-layer.png")
        self.assertEqual(opt_res.status_code, 200)
        self.assertEqual(opt_res.mimetype, "image/png")
        self.assertGreater(len(opt_res.data), 0)


if __name__ == "__main__":
    unittest.main()
