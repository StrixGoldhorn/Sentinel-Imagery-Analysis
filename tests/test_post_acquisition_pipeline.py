"""Tests for automated PostAcquisitionPipeline."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

from sentinel_analysis.application.ports.detection import DetectionResult
from sentinel_analysis.application.use_cases.post_acquisition_pipeline import PostAcquisitionPipeline
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan, ShipDetection
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository


class TestPostAcquisitionPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.tmp_dir.name) / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.scan_repo = FilesystemScanRepository(self.output_root)

        self.scan_folder = "test_pipeline_scan"
        scan_dir = self.scan_repo.prepare(self.scan_folder)
        self.img_path = scan_dir / "images" / f"{self.scan_folder}_stitched.png"

        # Write 100x100 test SAR image
        img_arr = np.full((100, 100), 120, dtype=np.uint8)
        Image.fromarray(img_arr).save(self.img_path)

        self.bbox = BoundingBox(103.5, 1.1, 104.2, 1.6)
        self.acq = Acquisition(
            datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
            "SENTINEL-1A",
            "sentinel-1-grd",
        )
        self.scan = Scan(
            folder_name=self.scan_folder,
            bbox=self.bbox,
            acquisition=self.acq,
            image_path=str(self.img_path),
            metadata={"provider": "copernicus", "custom_name": "Test Port"},
        )
        self.scan_repo.save(self.scan)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_full_pipeline_execution(self):
        mock_detect = MagicMock()
        mock_detect.execute.return_value = DetectionResult(
            image_width=100,
            image_height=100,
            detections=[
                ShipDetection(
                    x=10,
                    y=20,
                    width=15,
                    height=25,
                    confidence=0.92,
                    angle=45.0,
                    length=50.0,
                    beam=12.0,
                    center_x=17.5,
                    center_y=32.5,
                )
            ],
        )

        mock_correlate = MagicMock()
        mock_correlate.execute.return_value = [
            {
                "index": 0,
                "x": 10,
                "y": 20,
                "width": 15,
                "height": 25,
                "confidence": 0.92,
                "angle": 45.0,
                "length": 50.0,
                "beam": 12.0,
                "center_x": 17.5,
                "center_y": 32.5,
                "correlation_status": "inside_box",
                "is_correlated": True,
                "correlated_ais": {"name": "PACIFIC EXPLORER", "mmsi": "563000111", "speed": 12.4},
            }
        ]
        mock_correlate.last_ghost_vessels = []

        mock_saver = MagicMock()
        mock_saver.return_value = {
            "timestamp": "2026-09-20T10:05:00Z",
            "ship_count": 1,
            "correlated_count": 1,
            "inside_box_count": 1,
            "outside_box_count": 0,
            "uncorrelated_count": 0,
            "detected_image_name": f"{self.scan_folder}_detected.png",
            "detections_json_name": f"{self.scan_folder}_detections.json",
        }

        mock_briefing = MagicMock()
        pdf_path = self.img_path.parent / f"{self.scan_folder}_briefing.pdf"
        pdf_path.write_text("fake pdf content", encoding="utf-8")
        mock_briefing.execute.return_value = pdf_path

        mock_export = MagicMock()
        tif_path = self.img_path.parent / f"{self.scan_folder}.tif"
        tif_path.write_text("fake tif", encoding="utf-8")
        mock_export.export_geotiff.return_value = tif_path
        mock_export.export_stac_item.return_value = {"type": "Feature", "id": self.scan_folder}

        progress_reports = []

        def callback(pct, msg):
            progress_reports.append((pct, msg))

        pipeline = PostAcquisitionPipeline(
            scan_repository=self.scan_repo,
            detect_ships=mock_detect,
            correlate_ais=mock_correlate,
            generate_dem=None,
            generate_briefing=mock_briefing,
            export_geospatial=mock_export,
            output_root=self.output_root,
            detection_saver=mock_saver,
        )

        result = pipeline.execute(self.scan, progress_callback=callback)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["folderName"], self.scan_folder)
        self.assertEqual(result["customName"], "Test Port")
        self.assertEqual(len(result["detections"]), 1)
        self.assertEqual(result["ship_count"], 1)
        self.assertEqual(result["inside_box_count"], 1)
        self.assertEqual(result["outside_box_count"], 0)
        self.assertEqual(result["briefing_pdf_url"], f"/api/scan/{self.scan_folder}/briefing/pdf")
        self.assertEqual(result["geotiff_url"], f"/api/scan/{self.scan_folder}/geotiff")
        self.assertEqual(result["gis_bundle_url"], f"/api/scan/{self.scan_folder}/gis_bundle")

        # Verify detect, correlate, saver, briefing, export were called
        mock_detect.execute.assert_called_once()
        mock_correlate.execute.assert_called_once()
        mock_saver.assert_called_once()
        mock_briefing.execute.assert_called_once_with(self.scan_folder)
        mock_export.export_geotiff.assert_called_once_with(self.scan_folder)
        mock_export.export_stac_item.assert_called_once_with(self.scan_folder)

        # Check STAC item written
        stac_file = self.img_path.parent / f"{self.scan_folder}_stac.json"
        self.assertTrue(stac_file.is_file())

        # Check progress reports reached 100%
        self.assertTrue(any(pct == 100 for pct, _ in progress_reports))

        # Check scan repo was updated
        updated_scan = self.scan_repo.get(self.scan_folder)
        self.assertIn("latest_cv_results", updated_scan.metadata)
        self.assertEqual(updated_scan.metadata["latest_cv_results"]["ship_count"], 1)
        self.assertEqual(updated_scan.metadata["briefing_pdf"], f"{self.scan_folder}_briefing.pdf")
        self.assertEqual(updated_scan.metadata["geotiff"], f"{self.scan_folder}.tif")

    def test_pipeline_handles_optional_services_gracefully(self):
        pipeline = PostAcquisitionPipeline(
            scan_repository=self.scan_repo,
            detect_ships=None,
            correlate_ais=None,
            generate_dem=None,
            generate_briefing=None,
            export_geospatial=None,
            output_root=self.output_root,
            detection_saver=None,
        )

        result = pipeline.execute(self.scan)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["folderName"], self.scan_folder)
        self.assertEqual(result["ship_count"], 0)
        self.assertEqual(result["detections"], [])

    def test_pipeline_handles_exceptions_gracefully(self):
        mock_detect = MagicMock()
        mock_detect.execute.side_effect = RuntimeError("Detector failure")

        mock_briefing = MagicMock()
        mock_briefing.execute.side_effect = RuntimeError("PDF brief failure")

        pipeline = PostAcquisitionPipeline(
            scan_repository=self.scan_repo,
            detect_ships=mock_detect,
            correlate_ais=None,
            generate_dem=None,
            generate_briefing=mock_briefing,
            export_geospatial=None,
            output_root=self.output_root,
            detection_saver=None,
        )

        # Should not raise exception
        result = pipeline.execute(self.scan)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["ship_count"], 0)


if __name__ == "__main__":
    unittest.main()
