"""Focused tests for application orchestration and failure semantics."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel_analysis.application.exceptions import (
    AreaOfInterestNotFoundError,
    InvalidPredictionError,
    NoImageryFoundError,
    PluginNotFoundError,
    ScanNotFoundError,
)
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.application.use_cases.manage_aois import PredictAreaOfInterest
from sentinel_analysis.application.use_cases.manage_scans import DeleteScan, GetScan, RenameScan
from sentinel_analysis.application.use_cases.predict_passes import PredictPasses
from sentinel_analysis.domain.entities import (
    AISRecord,
    Acquisition,
    AreaOfInterest,
    BoundingBox,
    Scan,
    ShipDetection,
    Vessel,
    VesselPosition,
)


BBOX = BoundingBox(103, 1, 104, 2)


class EmptyImageryProvider:
    def find_latest_acquisition(self, bbox, days_ago=None):
        return Acquisition(datetime(2026, 8, 27, tzinfo=timezone.utc), "Sentinel-1", "sentinel-1-grd")


    def calculate_tiles(self, bbox):
        return []

    def download_tile(self, tile, acquisition, output_path):
        raise AssertionError("No tile should be downloaded")


class TrackingScanRepository:
    def __init__(self, prepare_error=None):
        self.prepare_error = prepare_error
        self.deleted = []
        self.scans = {}
        self.renames = []

    def prepare(self, folder_name):
        if self.prepare_error:
            raise self.prepare_error
        return Path("workspace")

    def save(self, scan):
        self.scans[scan.folder_name] = scan

    def get(self, folder_name):
        return self.scans.get(folder_name)

    def list(self):
        return list(self.scans.values())

    def update_custom_name(self, folder_name, custom_name):
        self.renames.append((folder_name, custom_name))

    def delete(self, folder_name):
        self.deleted.append(folder_name)


class NoOpStitcher:
    def stitch(self, tiles, output_path):
        raise AssertionError("Empty tiles must be rejected before stitching")


class NoOpLocationResolver:
    def resolve(self, latitude, longitude):
        return "Unknown"


class FakePredictor:
    def __init__(self, predictions):
        self.predictions = predictions
        self.api_key = None

    def predict(self, bbox, api_key):
        self.api_key = api_key
        return list(self.predictions)


class MemoryAOIRepository:
    def __init__(self, aoi=None):
        self.aoi = aoi
        self.updated = None

    def list(self):
        return [self.aoi] if self.aoi else []

    def add(self, aoi):
        return 1

    def get(self, aoi_id):
        return self.aoi if aoi_id == 1 else None

    def update_prediction(self, aoi_id, next_scan, last_checked):
        self.updated = (aoi_id, next_scan, last_checked)


class StaticRegistry:
    def __init__(self, plugins):
        self.plugins = plugins

    def get_plugins(self, name=None):
        if name is None:
            return list(self.plugins)
        return [plugin for plugin in self.plugins if plugin.name == name]


class SuccessfulPlugin:
    name = "working"

    def __init__(self):
        self.time_range = None

    def authenticate(self):
        return None

    def fetch(self, bbox, time_range):
        self.time_range = time_range
        return [
            AISRecord(
                Vessel("1234567", "123456789"),
                VesselPosition("123456789", 1.5, 103.5, datetime.now(timezone.utc)),
            )
        ]


class FailingPlugin:
    name = "failing"

    def authenticate(self):
        raise RuntimeError("provider unavailable")

    def fetch(self, bbox, time_range):
        return []


class MemoryAISRepository:
    def __init__(self):
        self.logs = []

    def save_records(self, records, source_plugin):
        return len(list(records))

    def log_execution(self, plugin_name, status, records_inserted, error_message=None):
        self.logs.append((plugin_name, status, records_inserted, error_message))


def test_create_scan_rejects_empty_tiles_and_rolls_back_prepared_workspace() -> None:
    repository = TrackingScanRepository()
    use_case = CreateScan(EmptyImageryProvider(), NoOpStitcher(), repository, NoOpLocationResolver())

    try:
        use_case.execute(BBOX)
        assert False, "Expected NoImageryFoundError"
    except NoImageryFoundError:
        pass

    assert len(repository.deleted) == 1


def test_create_scan_does_not_delete_when_workspace_preparation_fails() -> None:
    repository = TrackingScanRepository(OSError("cannot prepare"))
    use_case = CreateScan(EmptyImageryProvider(), NoOpStitcher(), repository, NoOpLocationResolver())

    try:
        use_case.execute(BBOX)
        assert False, "Expected OSError"
    except OSError:
        pass

    assert repository.deleted == []


def test_detect_ships_validates_threshold_and_returns_named_result() -> None:
    class Detector:
        def detect(self, image_path, dem_path=None, threshold=40):
            return [ShipDetection(1, 2, 3, 4)], 100, 50

    use_case = DetectShips(Detector())
    result = use_case.execute(Path("scan.png"), threshold=20)

    assert result.image_width == 100
    assert result[0][0].width == 3
    try:
        use_case.execute(Path("scan.png"), threshold=256)
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_predictions_are_validated_normalized_and_sorted() -> None:
    predictor = FakePredictor(
        [
            {"time": "2026-08-28T10:00:00+08:00", "max_elevation": "42.5"},
            {"time": "2026-08-27T01:00:00Z", "max_elevation": None},
        ]
    )

    result = PredictPasses(predictor).execute(BBOX, " key ")

    assert predictor.api_key == "key"
    assert result[0]["time"] == "2026-08-27T01:00:00+00:00"
    assert result[1]["max_elevation"] == 42.5


def test_invalid_provider_prediction_is_an_expected_application_error() -> None:
    try:
        PredictPasses(FakePredictor([{"time": "invalid", "max_elevation": 10}])).execute(BBOX, "key")
        assert False, "Expected InvalidPredictionError"
    except InvalidPredictionError:
        pass


def test_predict_aoi_updates_repository_with_earliest_pass() -> None:
    repository = MemoryAOIRepository(AreaOfInterest("Harbour", BBOX, id=1))
    predictor = FakePredictor(
        [
            {"time": "2026-08-29T00:00:00Z", "max_elevation": 20},
            {"time": "2026-08-28T00:00:00Z", "max_elevation": 30},
        ]
    )

    predictions = PredictAreaOfInterest(repository, predictor).execute(1, "key")

    assert predictions[0]["time"] == "2026-08-28T00:00:00+00:00"
    assert repository.updated[1] == datetime(2026, 8, 28, tzinfo=timezone.utc)
    try:
        PredictAreaOfInterest(repository, predictor).execute(2, "key")
        assert False, "Expected AreaOfInterestNotFoundError"
    except AreaOfInterestNotFoundError:
        pass


def test_ais_ingestion_isolates_plugins_and_normalizes_time_range() -> None:
    successful = SuccessfulPlugin()
    repository = MemoryAISRepository()
    use_case = IngestAIS(StaticRegistry([FailingPlugin(), successful]), repository)
    start = datetime(2026, 8, 27)
    end = datetime(2026, 8, 27, 9, tzinfo=timezone(timedelta(hours=8)))

    result = use_case.execute(BBOX, (start, end))

    assert result["total_inserted"] == 1
    assert [log["status"] for log in result["logs"]] == ["FAILED", "SUCCESS"]
    assert successful.time_range[0].tzinfo == timezone.utc
    assert successful.time_range[1].tzinfo == timezone.utc


def test_ais_ingestion_rejects_invalid_range_and_unknown_plugin() -> None:
    use_case = IngestAIS(StaticRegistry([]), MemoryAISRepository())

    try:
        use_case.execute(BBOX, (datetime(2026, 8, 28), datetime(2026, 8, 27)))
        assert False, "Expected ValueError"
    except ValueError:
        pass

    try:
        use_case.execute(BBOX, (None, None), "missing")
        assert False, "Expected PluginNotFoundError"
    except PluginNotFoundError:
        pass


def test_scan_commands_normalize_names_and_report_missing_scans() -> None:
    repository = TrackingScanRepository()

    try:
        GetScan(repository).execute("missing")
        assert False, "Expected ScanNotFoundError"
    except ScanNotFoundError:
        pass

    try:
        RenameScan(repository).execute("missing", " name ")
        assert False, "Expected ScanNotFoundError"
    except ScanNotFoundError:
        pass

    try:
        DeleteScan(repository).execute("missing")
        assert False, "Expected ScanNotFoundError"
    except ScanNotFoundError:
        pass


def test_delete_scan_deletes_existing_scan() -> None:
    repository = TrackingScanRepository()
    scan = Scan("scan_1", BBOX, Acquisition(datetime(2026, 8, 27, tzinfo=timezone.utc), "Sentinel-1", "sar"), "scan.png", {})
    repository.save(scan)

    DeleteScan(repository).execute(" scan_1 ")

    assert repository.deleted == ["scan_1"]


def test_check_and_schedule_aois_triggers_scans() -> None:
    from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs

    now = datetime.now(timezone.utc)
    due_aoi = AreaOfInterest("Due AOI", BBOX, id=1, auto_capture_enabled=True)
    disabled_aoi = AreaOfInterest("Disabled AOI", BBOX, id=2, auto_capture_enabled=False)

    class MultiAOIRepository:
        def __init__(self):
            self.aois = [due_aoi, disabled_aoi]
            self.updated = []
        def list(self):
            return self.aois
        def update_prediction(self, aoi_id, next_scan, last_checked):
            self.updated.append((aoi_id, next_scan, last_checked))

    repo = MultiAOIRepository()
    pass_time = (now + timedelta(days=2)).isoformat()
    predictor = FakePredictor([{"time": pass_time}])
    scheduler = CheckAndScheduleAOIs(repo, predictor)
    results = scheduler.execute("api_key")

    assert len(results) == 1
    assert results[0]["aoi_id"] == 1
    assert results[0]["status"] == "SCHEDULED"


def test_get_vessel_positions_returns_only_latest_per_unique_mmsi() -> None:
    from sentinel_analysis.application.use_cases.get_vessels import GetVesselPositions


    class FakeAISRepo:
        def get_vessel_positions(self, bbox=None, time_range=None, limit=500, latest_only=True):
            return [
                {"mmsi": "111222333", "name": "Older Position", "latitude": 1.25, "longitude": 103.85, "timestamp": "2026-08-30T10:00:00Z"},
                {"mmsi": "111222333", "name": "Latest Position", "latitude": 1.28, "longitude": 103.89, "timestamp": "2026-08-30T12:00:00Z"},
                {"mmsi": "444555666", "name": "Single Position", "latitude": 1.30, "longitude": 103.90, "timestamp": "2026-08-30T11:00:00Z"},
            ]

    use_case = GetVesselPositions(FakeAISRepo())
    results = use_case.execute(latest_only=True)

    assert len(results) == 2
    mmsi_map = {r["mmsi"]: r for r in results}
    assert "111222333" in mmsi_map
    assert "444555666" in mmsi_map
    assert mmsi_map["111222333"]["name"] == "Latest Position"
    assert mmsi_map["111222333"]["timestamp"] == "2026-08-30T12:00:00Z"


def load_tests(loader, standard_tests, pattern):

    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()



