"""Contract tests for application-owned ports."""

import unittest
from pathlib import Path

from sentinel_analysis.application.ports import (
    AISPlugin,
    AISPluginRegistry,
    AISRepository,
    AreaOfInterestRepository,
    AnnotationEditor,
    AnnotationProgressRepository,
    AnnotationTileSource,
    ImageStitcher,
    ImageryProvider,
    LocationResolver,
    PassPredictor,
    ScanRepository,
    ShipDetector,
    TaskQueue,
    TileCache,
)


class CompleteAdapter:
    """A structural test double implementing every port method."""

    name = "complete"

    def find_latest_acquisition(self, bbox, days_ago=None):
        return None


    def calculate_tiles(self, bbox):
        return []

    def download_tile(self, tile, acquisition, output_path):
        return None

    def detect(self, image_path, dem_path=None, threshold=40):
        return [], 0, 0

    def predict(self, bbox, api_key):
        return []

    def stitch(self, tiles, output_path):
        return None

    def resolve(self, latitude, longitude):
        return "Unknown"

    def authenticate(self):
        return None

    def fetch(self, bbox, time_range):
        return []

    def get_plugins(self, name=None):
        return [self]

    def prepare(self, folder_name):
        return Path(folder_name)

    def save(self, scan):
        return None

    def get(self, identifier):
        return None

    def list(self):
        return []

    def update_custom_name(self, folder_name, custom_name):
        return None

    def delete(self, folder_name):
        return None

    def add(self, aoi):
        return 1

    def update_prediction(self, aoi_id, next_scan, last_checked):
        return None

    def save_records(self, records, source_plugin):
        return 0

    def log_execution(self, plugin_name, status, records_inserted, error_message=None):
        return None

    def list_tiles(self, tile_folder):
        return []

    def load(self):
        return None

    def edit(self, tile, label_path, use_lee_filter):
        return True

    def set(self, key, data):
        return None

    def has(self, key):
        return True

    def submit(self, task_type, task_id, func, *args, **kwargs):
        return None

    def get_task(self, task_id):
        return None

    def update_progress(self, task_id, progress, message=""):
        return None



class ApplicationPortTests(unittest.TestCase):
    def test_ports_support_structural_runtime_checks(self) -> None:
        adapter = CompleteAdapter()
        ports = (
            AISPlugin,
            AISPluginRegistry,
            AISRepository,
            AreaOfInterestRepository,
            AnnotationEditor,
            AnnotationProgressRepository,
            AnnotationTileSource,
            ImageStitcher,
            ImageryProvider,
            LocationResolver,
            PassPredictor,
            ScanRepository,
            ShipDetector,
            TaskQueue,
            TileCache,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertIsInstance(adapter, port)

    def test_incomplete_adapter_fails_runtime_contract(self) -> None:
        self.assertNotIsInstance(object(), ImageryProvider)


if __name__ == "__main__":
    unittest.main()
