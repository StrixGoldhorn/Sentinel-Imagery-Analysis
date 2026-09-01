"""Unit tests for planned scrapes scheduling, persistence, and worker status."""

from datetime import datetime, timedelta, timezone
import inspect
import unittest

from sentinel_analysis.application.use_cases.get_schedule import GetUpcomingScrapes
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.infrastructure.scheduler.pass_scheduler import PassSchedulerWorker


class StubPredictor:
    def __init__(self, predictions=None):
        self.predictions = predictions or []

    def predict(self, bbox, api_key):
        return self.predictions


class StubAOIRepo:
    def __init__(self, aois=None):
        self._aois = aois or []

    def list(self):
        return self._aois

    def get(self, aoi_id):
        for a in self._aois:
            if a.id == aoi_id:
                return a
        return None

    def update_prediction(self, aoi_id, next_scan, last_checked):
        pass


class MemoryAISRepository:
    def __init__(self):
        self._logs = []
        self._inserted_records = []

    def save_records(self, records, source_plugin: str = "") -> int:
        records_list = list(records)
        self._inserted_records.extend(records_list)
        return len(records_list)

    def log_execution(self, plugin_name, status, records_inserted=0, error_message=None):
        self._logs.insert(0, {
            "plugin_name": plugin_name,
            "status": status,
            "records_inserted": records_inserted,
            "error_message": error_message,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        })

    def get_scraper_logs(self, plugin_name=None, status=None, limit=50, offset=0):
        logs = self._logs
        if plugin_name:
            logs = [l for l in logs if l["plugin_name"] == plugin_name]
        if status:
            logs = [l for l in logs if l["status"] == status]
        return logs[offset : offset + limit]


def test_get_upcoming_scrapes_sorting_and_filtering() -> None:
    now = datetime.now(timezone.utc)
    pass1 = now + timedelta(hours=2)
    pass2 = now + timedelta(days=2)
    past_pass = now - timedelta(hours=1)

    aoi1 = AreaOfInterest(
        id=1,
        name="Singapore Port",
        bbox=BoundingBox(103.8, 1.2, 103.9, 1.3),
        auto_capture_enabled=True,
    )
    aoi2 = AreaOfInterest(
        id=2,
        name="Malacca North",
        bbox=BoundingBox(100.1, 4.2, 100.5, 4.5),
        auto_capture_enabled=False,
    )

    predictions = [
        {
            "time": pass2.isoformat(),
            "satellite": "Sentinel-1A",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 171,
            "confidence_score": 0.9,
            "max_elevation": 55,
            "source": "COMBINED",
        },
        {
            "time": pass1.isoformat(),
            "satellite": "Sentinel-1C",
            "orbit_direction": "DESCENDING",
            "relative_orbit": 45,
            "confidence_score": 0.85,
            "max_elevation": 68,
            "source": "COMBINED",
        },
        {
            "time": past_pass.isoformat(),
            "satellite": "Sentinel-1A",
        },
    ]

    predictor = StubPredictor(predictions)
    repo = StubAOIRepo([aoi1, aoi2])
    use_case = GetUpcomingScrapes(repo, predictor)

    result = use_case.execute(api_key="test_key", days_ahead=7)
    events = result["events"]
    metrics = result["metrics"]

    assert metrics["total_aois"] == 2
    assert metrics["auto_capture_count"] == 1
    assert len(events) == 4

    for i in range(len(events) - 1):
        assert events[i]["pass_time"] <= events[i + 1]["pass_time"]

    result_auto = use_case.execute(api_key="test_key", auto_capture_only=True)
    assert len(result_auto["events"]) == 2
    for e in result_auto["events"]:
        assert e["auto_capture_enabled"] is True
        assert e["aoi_id"] == 1


def test_missing_api_key_raises_error() -> None:
    repo = StubAOIRepo([])
    predictor = StubPredictor([])
    use_case = GetUpcomingScrapes(repo, predictor)
    try:
        use_case.execute(api_key="")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_scraper_logs_in_memory_repository() -> None:
    repo = MemoryAISRepository()
    repo.log_execution("MockPluginA", "SUCCESS", 42, None)
    repo.log_execution("MockPluginB", "FAILED", 0, "Network timeout")

    logs = repo.get_scraper_logs(limit=10)
    assert len(logs) == 2
    assert logs[0]["plugin_name"] == "MockPluginB"
    assert logs[0]["status"] == "FAILED"
    assert logs[0]["error_message"] == "Network timeout"
    assert logs[1]["plugin_name"] == "MockPluginA"
    assert logs[1]["records_inserted"] == 42


def test_pass_scheduler_worker_status_and_trigger() -> None:
    aoi_repo = StubAOIRepo([])
    check_aois = CheckAndScheduleAOIs(aoi_repo, StubPredictor([]))
    worker = PassSchedulerWorker(check_aois, api_key="dummy_key", poll_interval_seconds=60.0)

    status = worker.get_status()
    assert status["is_running"] is False
    assert status["api_key_configured"] is True
    assert status["last_run_at"] is None

    results = worker.trigger_check()
    assert results == []

    status_after = worker.get_status()
    assert status_after["last_run_at"] is not None
    assert status_after["last_error"] is None


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite

