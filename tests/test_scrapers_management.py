from datetime import datetime, timezone
import inspect
import unittest

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.application.use_cases.manage_scrapers import (
    GetScraperLogsUseCase,
    ListScrapers,
    ToggleScraper,
)
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry


class DummyPlugin:
    def __init__(self, name: str, records: list | None = None, fail: bool = False):
        self.name = name
        self.records = records or []
        self.fail = fail
        self.auth_called = False
        self.fetch_called = False

    def authenticate(self) -> None:
        self.auth_called = True
        if self.fail:
            raise RuntimeError(f"Auth failure in {self.name}")

    def fetch(self, bbox: BoundingBox, time_range: AISTimeRange):
        self.fetch_called = True
        if self.fail:
            raise RuntimeError(f"Fetch failure in {self.name}")
        return self.records


class MemoryAISRepository:
    def __init__(self):
        self._configs = {}
        self._logs = []
        self._inserted_records = []

    def get_scraper_config(self, plugin_name: str):
        if plugin_name not in self._configs:
            return None
        return {"plugin_name": plugin_name, "enabled": self._configs[plugin_name]}

    def set_scraper_config(self, plugin_name: str, enabled: bool):
        self._configs[plugin_name] = enabled

    def get_all_scraper_configs(self):
        return dict(self._configs)

    def log_execution(self, plugin_name: str, status: str, records_inserted: int = 0, error_message: str | None = None):
        self._logs.insert(0, {
            "plugin_name": plugin_name,
            "status": status,
            "records_inserted": records_inserted,
            "error_message": error_message,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        })

    def get_scraper_logs(self, plugin_name: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0):
        logs = self._logs
        if plugin_name:
            logs = [l for l in logs if l["plugin_name"] == plugin_name]
        if status:
            logs = [l for l in logs if l["status"] == status]
        return logs[offset : offset + limit]

    def get_scraper_stats(self):
        stats = {}
        for l in reversed(self._logs):
            name = l["plugin_name"]
            if name not in stats:
                stats[name] = {"total_runs": 0, "total_records": 0, "success_runs": 0, "failed_runs": 0, "last_run": None, "last_status": None}
            s = stats[name]
            s["total_runs"] += 1
            s["total_records"] += l["records_inserted"]
            if l["status"] == "SUCCESS":
                s["success_runs"] += 1
            elif l["status"] == "FAILED":
                s["failed_runs"] += 1
            s["last_run"] = l["executed_at"]
            s["last_status"] = l["status"]
        return stats

    def save_records(self, records, source_plugin: str = ""):
        records_list = list(records)
        self._inserted_records.extend(records_list)
        return len(records_list)

    def ingest_records(self, records):
        return self.save_records(records)


def test_scraper_config_crud() -> None:
    repo = MemoryAISRepository()
    assert repo.get_scraper_config("VesselFinderPlugin") is None

    repo.set_scraper_config("VesselFinderPlugin", False)
    config = repo.get_scraper_config("VesselFinderPlugin")
    assert config is not None
    assert config["plugin_name"] == "VesselFinderPlugin"
    assert config["enabled"] is False

    all_configs = repo.get_all_scraper_configs()
    assert all_configs.get("VesselFinderPlugin") is False

    repo.set_scraper_config("VesselFinderPlugin", True)
    all_configs_updated = repo.get_all_scraper_configs()
    assert all_configs_updated.get("VesselFinderPlugin") is True


def test_scraper_stats_aggregation() -> None:
    repo = MemoryAISRepository()
    repo.log_execution("PluginA", "SUCCESS", 25, None)
    repo.log_execution("PluginA", "SUCCESS", 15, None)
    repo.log_execution("PluginA", "FAILED", 0, "Network timeout")
    repo.log_execution("PluginB", "SUCCESS", 10, None)

    stats = repo.get_scraper_stats()
    assert "PluginA" in stats
    assert "PluginB" in stats

    stat_a = stats["PluginA"]
    assert stat_a["total_runs"] == 3
    assert stat_a["total_records"] == 40
    assert stat_a["success_runs"] == 2
    assert stat_a["failed_runs"] == 1

    stat_b = stats["PluginB"]
    assert stat_b["total_runs"] == 1
    assert stat_b["total_records"] == 10


def test_list_scrapers_use_case() -> None:
    repo = MemoryAISRepository()
    plugin_a = DummyPlugin("PluginA")
    plugin_b = DummyPlugin("PluginB")
    registry = DynamicAISPluginRegistry([plugin_a, plugin_b])

    repo.set_scraper_config("PluginA", True)
    repo.set_scraper_config("PluginB", False)
    repo.log_execution("PluginA", "SUCCESS", 12, None)

    use_case = ListScrapers(registry, repo)
    result = use_case.execute()

    scrapers = result["scrapers"]
    metrics = result["metrics"]

    assert len(scrapers) == 2
    assert metrics["total_scrapers"] == 2
    assert metrics["active_scrapers"] == 1
    assert metrics["total_records_ingested"] == 12

    scraper_a = next(s for s in scrapers if s["name"] == "PluginA")
    assert scraper_a["enabled"] is True
    assert scraper_a["total_runs"] == 1

    scraper_b = next(s for s in scrapers if s["name"] == "PluginB")
    assert scraper_b["enabled"] is False


def test_toggle_scraper_use_case() -> None:
    repo = MemoryAISRepository()
    plugin = DummyPlugin("TogglePlugin")
    registry = DynamicAISPluginRegistry([plugin])
    use_case = ToggleScraper(registry, repo)

    res_off = use_case.execute("TogglePlugin", False)
    assert res_off["enabled"] is False
    assert repo.get_scraper_config("TogglePlugin")["enabled"] is False

    res_on = use_case.execute("TogglePlugin", True)
    assert res_on["enabled"] is True
    assert repo.get_scraper_config("TogglePlugin")["enabled"] is True


def test_ingest_ais_skips_disabled_scrapers() -> None:
    repo = MemoryAISRepository()
    vessel = Vessel(imo="1234567", mmsi="123456789", name="TEST VESSEL")
    pos = VesselPosition(mmsi="123456789", latitude=1.25, longitude=103.85, timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc))
    rec = AISRecord(vessel, pos)

    plugin_active = DummyPlugin("ActivePlugin", [rec])
    plugin_disabled = DummyPlugin("DisabledPlugin", [rec])
    registry = DynamicAISPluginRegistry([plugin_active, plugin_disabled])

    repo.set_scraper_config("DisabledPlugin", False)
    repo.set_scraper_config("ActivePlugin", True)

    ingest = IngestAIS(registry, repo)
    result = ingest.execute(BoundingBox(103.0, 1.0, 104.0, 2.0), (None, None))

    assert result["total_inserted"] == 1
    assert plugin_active.fetch_called is True
    assert plugin_disabled.fetch_called is False


def test_get_scraper_logs_use_case() -> None:
    repo = MemoryAISRepository()
    repo.log_execution("PluginX", "SUCCESS", 5, None)
    repo.log_execution("PluginY", "FAILED", 0, "Connection refused")

    use_case = GetScraperLogsUseCase(repo)
    all_logs = use_case.execute()
    assert all_logs["count"] == 2
    assert all_logs["metrics"]["total_runs"] == 2

    failed_logs = use_case.execute(status="FAILED")
    assert failed_logs["count"] == 1
    assert failed_logs["logs"][0]["plugin_name"] == "PluginY"


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite

