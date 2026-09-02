"""Offline tests for concrete infrastructure adapters."""

import json
import random
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

_TMP_DIR = Path(__file__).resolve().parent / "runtime" / "tmp"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP_DIR)

from PIL import Image

from sentinel_analysis.application.ports import (
    AISPlugin,
    AISPluginRegistry,
    AISRepository,
    AreaOfInterestRepository,
    ImageStitcher,
    ImageryProvider,
    LocationResolver,
    PassPredictor,
    ScanRepository,
    ShipDetector,
    TaskQueue,
    TileCache,
)
from sentinel_analysis.application.exceptions import ExternalServiceError
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, ImageTile, Scan
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.ais.plugins.mock import MockAISPlugin
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.geocoding import NominatimLocationResolver
from sentinel_analysis.infrastructure.imagery.cache import FilesystemTileCache
from sentinel_analysis.infrastructure.imagery.copernicus import (
    CopernicusImageryProvider,
    CopernicusTokenProvider,
)
from sentinel_analysis.infrastructure.imagery.stitching import PillowImageStitcher
from sentinel_analysis.infrastructure.imagery.tiling import TileGridCalculator
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.infrastructure.satellite.n2yo import N2YOPassPredictor
from sentinel_analysis.infrastructure.tasks.queue import ThreadedTaskQueue



BBOX = BoundingBox(103, 1, 104, 2)
RUNTIME = Path(__file__).resolve().parent / "runtime" / "infrastructure"


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self.payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeHTTPClient:
    def __init__(self, *, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_responses.pop(0)


class StaticTokenProvider:
    def get(self):
        return "token"


class ByteResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._payload


def test_concrete_adapters_satisfy_application_ports() -> None:
    from unittest.mock import MagicMock, patch
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchall.return_value = []

    with patch("sqlite3.connect", return_value=mock_conn), patch("sentinel_analysis.infrastructure.persistence.migrations.runner.MigrationRunner.run_migrations", return_value=[]):
        scans = FilesystemScanRepository(RUNTIME / "port_scans")
        adapters_and_ports = (
            (DynamicAISPluginRegistry([]), AISPluginRegistry),
            (MockAISPlugin(), AISPlugin),
            (SQLiteAISRepository("virtual_port.db"), AISRepository),
            (SQLiteAreaOfInterestRepository("virtual_port.db"), AreaOfInterestRepository),
            (PillowImageStitcher(), ImageStitcher),
            (CopernicusImageryProvider(StaticTokenProvider()), ImageryProvider),
            (NominatimLocationResolver(), LocationResolver),
            (N2YOPassPredictor(), PassPredictor),
            (scans, ScanRepository),
            (ClassicalShipDetector(), ShipDetector),
            (FilesystemTileCache(RUNTIME / "port_cache"), TileCache),
            (ThreadedTaskQueue(), TaskQueue),
        )

        for adapter, port in adapters_and_ports:
            assert isinstance(adapter, port)


def test_registry_honors_explicit_empty_configuration_and_rejects_duplicates() -> None:
    assert DynamicAISPluginRegistry([]).get_plugins() == []
    plugin = MockAISPlugin()
    try:
        DynamicAISPluginRegistry([plugin, plugin])
        assert False, "Expected ValueError for duplicate plugins"
    except ValueError as e:
        assert "unique" in str(e).lower()


def test_mock_plugin_can_be_reproduced_with_injected_randomness_and_clock() -> None:
    clock = lambda: datetime(2026, 8, 27, tzinfo=timezone.utc)
    first = MockAISPlugin(random.Random(7), clock).fetch(BBOX, (None, None))
    second = MockAISPlugin(random.Random(7), clock).fetch(BBOX, (None, None))

    assert first == second


def test_copernicus_token_is_cached_until_safety_adjusted_expiry() -> None:
    now = [100.0]
    client = FakeHTTPClient(post_responses=[FakeResponse({"access_token": "abc", "expires_in": 120})])
    provider = CopernicusTokenProvider(
        "user",
        "password",
        http_client=client,
        monotonic_clock=lambda: now[0],
    )

    assert provider.get() == "abc"
    now[0] = 150
    assert provider.get() == "abc"
    assert len(client.post_calls) == 1


def test_copernicus_catalog_response_is_translated_to_domain_acquisition() -> None:
    response = FakeResponse(
        {
            "features": [
                {
                    "id": "product-1",
                    "properties": {"datetime": "2026-08-20T10:00:00Z"},
                }
            ]
        }
    )
    client = FakeHTTPClient(get_responses=[response])
    provider = CopernicusImageryProvider(
        StaticTokenProvider(),
        http_client=client,
        clock=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    acquisition = provider.find_latest_acquisition(BBOX, 10)

    assert acquisition.product_id == "product-1"
    assert acquisition.acquired_at == datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
    assert "2026-08-17" in client.get_calls[0][1]["params"]["datetime"]

    client.get_responses.append(response)
    latest_ever = provider.find_latest_acquisition(BBOX)
    assert latest_ever.product_id == "product-1"
    assert "2014-01-01" in client.get_calls[1][1]["params"]["datetime"]


def test_n2yo_adapter_normalizes_provider_payload_and_rejects_invalid_shape() -> None:
    valid_client = FakeHTTPClient(
        get_responses=[FakeResponse({"info": {}, "passes": [{"maxUTC": 1787792400, "maxElev": 44}]})]
    )
    predictions = N2YOPassPredictor(http_client=valid_client).predict(BBOX, "key")

    assert predictions[0]["max_elevation"] == 44
    assert predictions[0]["time"].endswith("+00:00")
    invalid = N2YOPassPredictor(http_client=FakeHTTPClient(get_responses=[FakeResponse([])]))
    try:
        invalid.predict(BBOX, "key")
        assert False, "Expected ExternalServiceError"
    except ExternalServiceError:
        pass


def test_geocoder_parses_city_and_falls_back_for_transport_errors() -> None:
    payload = json.dumps({"address": {"city": "Singapore"}}).encode("utf-8")
    resolver = NominatimLocationResolver(opener=lambda request, timeout: ByteResponse(payload))
    failing = NominatimLocationResolver(
        opener=lambda request, timeout: (_ for _ in ()).throw(urllib.error.URLError("offline"))
    )

    assert resolver.resolve(1.3, 103.8) == "Singapore"
    assert failing.resolve(1.3, 103.8) == "Area at 1.30N, 103.80E"


def test_tiler_validates_configuration() -> None:
    try:
        TileGridCalculator(max_image_size=0)
        assert False, "Expected ValueError"
    except ValueError:
        pass

    try:
        TileGridCalculator(resolution_meters=float("nan"))
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_stitcher_rejects_incomplete_grid_and_writes_complete_grid_atomically() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        bbox = BoundingBox(103, 1, 104, 2)
        incomplete = [(ImageTile(bbox, 2, 2, 1, 0), temp_path / "missing.png")]
        try:
            PillowImageStitcher().stitch(incomplete, temp_path / "unused.png")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "rectangular" in str(e).lower()

        tile_path = temp_path / "tile.png"
        output_path = temp_path / "stitched.png"
        Image.new("RGBA", (2, 2), (255, 255, 255, 255)).save(tile_path)
        PillowImageStitcher().stitch(
            [(ImageTile(bbox, 2, 2, 0, 0), tile_path)],
            output_path,
        )
        assert output_path.is_file()
        assert not (temp_path / "stitched.png.tmp").exists()


def test_filesystem_repository_merges_required_metadata_and_rejects_external_image() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        repository = FilesystemScanRepository(temp_path / "scans")
        folder_name = "adapter_test_scan"
        repository.delete(folder_name)
        workspace = repository.prepare(folder_name)
        image_path = workspace / "images" / "scan.png"
        Image.new("RGBA", (1, 1), (255, 255, 255, 255)).save(image_path)
        acquisition = Acquisition(datetime(2026, 8, 27, tzinfo=timezone.utc), "Sentinel-1", "sar", "p1")
        scan = Scan(folder_name, BBOX, acquisition, str(image_path), {"settings": {"evalscript": "SAR"}})

        repository.save(scan)
        loaded = repository.get(folder_name)
        assert loaded.metadata["settings"]["bbox"] == BBOX.as_list()
        assert loaded.metadata["settings"]["datasource"] == "sar"
        external = Scan(folder_name, BBOX, acquisition, str(temp_path / "outside.png"))
        try:
            repository.save(external)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "workspace" in str(e).lower()
        repository.delete(folder_name)


def test_sqlite_adapter_validates_log_status_without_db() -> None:
    from unittest.mock import MagicMock, patch
    mock_conn = MagicMock()
    with patch("sentinel_analysis.infrastructure.persistence.migrations.runner.MigrationRunner.run_migrations", return_value=[]):
        repository = SQLiteAISRepository("virtual_validation.db")
        try:
            repository.log_execution("plugin", "PARTIAL", 0)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "invalid execution status" in str(e).lower()


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

