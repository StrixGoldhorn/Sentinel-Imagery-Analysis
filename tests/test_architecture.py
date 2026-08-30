import ast
import dataclasses
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import AISRecord, Acquisition, BoundingBox, ImageTile, Vessel, VesselPosition
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.imagery.stitching import PillowImageStitcher
from sentinel_analysis.infrastructure.imagery.tiling import TileGridCalculator
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.interfaces.web.application import create_app


class FakeImageryProvider:
    def __init__(self) -> None:
        self.acquisition = Acquisition(datetime(2026, 8, 1, tzinfo=timezone.utc), "Sentinel-1", "sentinel-1-grd")

    def find_latest_acquisition(self, bbox, days_ago=30):
        return self.acquisition

    def calculate_tiles(self, bbox):
        midpoint = (bbox.min_longitude + bbox.max_longitude) / 2
        return [
            ImageTile(BoundingBox(bbox.min_longitude, bbox.min_latitude, midpoint, bbox.max_latitude), 4, 3, 0, 0),
            ImageTile(BoundingBox(midpoint, bbox.min_latitude, bbox.max_longitude, bbox.max_latitude), 4, 3, 1, 0),
        ]

    def download_tile(self, tile, acquisition, output_path):
        color = (255, 0, 0, 255) if tile.x == 0 else (0, 255, 0, 255)
        Image.new("RGBA", (tile.width, tile.height), color).save(output_path)


class FakeLocationResolver:
    def resolve(self, latitude, longitude):
        return "Test Sea"


class StaticAISPlugin:
    name = "StaticAIS"

    def authenticate(self):
        return None

    def fetch(self, bbox, time_range):
        return [
            AISRecord(
                Vessel("1234567", "123456789", "Test Vessel"),
                VesselPosition("123456789", 1.5, 103.5, datetime.now(timezone.utc)),
            )
        ]


class InMemoryAOIRepository:
    def __init__(self):
        self._aois = {}
        self._id = 0

    def add(self, aoi):
        self._id += 1
        stored = dataclasses.replace(aoi, id=self._id)
        self._aois[self._id] = stored
        return self._id

    def get(self, aoi_id):
        return self._aois.get(aoi_id)

    def list_all(self):
        return list(self._aois.values())


class InMemoryAISRepository:
    def __init__(self):
        self.records = []
        self.locations = []

    def save_records(self, records, plugin_name):
        recs = list(records)
        self.records.extend(recs)
        self.locations.extend(recs)
        return len(recs)

    def log_execution(self, plugin_name, status, count, err=None):
        pass


def test_bounding_box_validation_and_tiling() -> None:
    bbox = BoundingBox.from_sequence([103, 1, 104, 2])
    assert bbox.center == (1.5, 103.5)
    assert len(TileGridCalculator().calculate(bbox)) > 1
    try:
        BoundingBox.from_sequence([103, 1, 103, 2])
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_inner_layers_do_not_import_outer_layers() -> None:
    package = Path(__file__).resolve().parents[1] / "sentinel_analysis"
    forbidden = {"flask", "requests", "sqlite3", "cv2", "PIL"}
    for layer in (package / "domain", package / "application"):
        for source_path in layer.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            assert not any(name.split(".")[0] in forbidden for name in imports), f"Outer dependency imported by {source_path}"
            assert not any(
                name.startswith("sentinel_analysis.infrastructure") or name.startswith("sentinel_analysis.interfaces")
                for name in imports
            ), f"Dependency direction violated by {source_path}"


def test_every_package_obeys_clean_architecture_dependency_direction() -> None:
    package = Path(__file__).resolve().parents[1] / "sentinel_analysis"
    allowed_dependencies = {
        "domain": ("sentinel_analysis.domain",),
        "application": ("sentinel_analysis.application", "sentinel_analysis.domain"),
        "infrastructure": (
            "sentinel_analysis.infrastructure",
            "sentinel_analysis.application",
            "sentinel_analysis.domain",
        ),
    }

    for layer, allowed in allowed_dependencies.items():
        for source_path in (package / layer).rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            internal_imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    internal_imports.extend(
                        alias.name for alias in node.names if alias.name.startswith("sentinel_analysis.")
                    )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith("sentinel_analysis.")
                ):
                    internal_imports.append(node.module)
            invalid = [name for name in internal_imports if not name.startswith(allowed)]
            assert invalid == [], f"{layer} dependency violation in {source_path}: {invalid}"


def test_canonical_package_does_not_import_removed_legacy_modules() -> None:
    package = Path(__file__).resolve().parents[1] / "sentinel_analysis"
    forbidden_roots = {
        "basic_classical_cv",
        "batch_annotate",
        "copernicus_get_image",
        "get_images_area",
        "ingestion",
        "predict_scans",
        "run_scraper",
        "utils",
    }
    for source_path in package.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(name.split(".")[0] in forbidden_roots for name in imports), f"Legacy module imported by {source_path}"


def test_classical_detector_adapter() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / "detector_test.png"
        image = Image.new("L", (100, 100), 0)
        for x in range(40, 51):
            for y in range(40, 51):
                image.putpixel((x, y), 255)
        image.save(image_path)

        detections, width, height = ClassicalShipDetector().detect(image_path, threshold=40)
        assert (width, height) == (100, 100)
        assert len(detections) == 1


def test_create_scan_end_to_end_offline() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        repository = FilesystemScanRepository(Path(temp_dir) / "scan_output")
        use_case = CreateScan(FakeImageryProvider(), PillowImageStitcher(), repository, FakeLocationResolver())
        scan = use_case.execute(BoundingBox(103, 1, 104, 2))

        stored = repository.get(scan.folder_name)
        assert stored is not None
        assert stored.metadata["location"] == "Test Sea"
        with Image.open(stored.image_path) as image:
            assert image.size == (8, 3)
            assert image.getpixel((0, 0))[:3] == (255, 0, 0)
            assert image.getpixel((7, 0))[:3] == (0, 255, 0)
        assert list(Path(stored.image_path).parent.glob("tile_*.png")) == []


def test_in_memory_repositories_and_ais_use_case() -> None:
    from sentinel_analysis.domain.entities import AreaOfInterest

    ais_repository = InMemoryAISRepository()
    result = IngestAIS(DynamicAISPluginRegistry([StaticAISPlugin()]), ais_repository).execute(
        BoundingBox(103, 1, 104, 2), (None, None)
    )
    assert result["total_inserted"] == 1
    assert len(ais_repository.locations) == 1

    aoi_repository = InMemoryAOIRepository()
    identifier = aoi_repository.add(AreaOfInterest("Test AOI", BoundingBox(103, 1, 104, 2)))
    assert aoi_repository.get(identifier).name == "Test AOI"


def test_flask_factory_routes() -> None:
    from unittest.mock import MagicMock
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=Path(temp_dir) / "virtual.db",
            output_root=Path(temp_dir) / "web_output",
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
        )
        container = MagicMock()
        container.settings = settings
        container.aoi_repository.list_all.return_value = []
        container.ais_repository.get_vessel_positions.return_value = []
        container.scan_repository.list_recent.return_value = []
        container.task_queue.submit = MagicMock()

        app = create_app(settings, container)
        client = app.test_client()
        assert client.get("/").status_code == 200
        assert client.get("/api/aoi").status_code == 200
        assert client.post("/scan", json={"bbox": [1, 2, 1, 3]}).status_code == 400
        assert client.post("/api/ingest_ais", json={"bbox": [103, 1, 104, 2], "plugin": "missing"}).status_code == 400


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

