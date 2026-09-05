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
from sentinel_analysis.infrastructure.ais.plugins import (
    AISFriendsPlugin,
    AprsFiPlugin,
    VesselFinderPlugin,
)


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
        self._custom_configs = {}
        self._failures = {}
        self._logs = []
        self._inserted_records = []

    def get_scraper_config(self, plugin_name: str):
        if plugin_name not in self._configs:
            return None
        f_info = self._failures.get(plugin_name, {})
        return {
            "plugin_name": plugin_name,
            "enabled": self._configs[plugin_name],
            "description": getattr(self, "_descriptions", {}).get(plugin_name),
            "tag": getattr(self, "_tags", {}).get(plugin_name),
            "config": self._custom_configs.get(plugin_name, {}),
            "cooldown_until": f_info.get("cooldown_until"),
            "consecutive_failures": f_info.get("consecutive_failures", 0),
            "last_failure_reason": f_info.get("reason"),
        }

    def get_scraper_detail(self, plugin_name: str):
        return self.get_scraper_config(plugin_name)

    def update_scraper(
        self,
        plugin_name: str,
        enabled: bool | None = None,
        description: str | None = None,
        tag: str | None = None,
        config: dict | None = None,
    ):
        if not hasattr(self, "_descriptions"):
            self._descriptions = {}
        if not hasattr(self, "_tags"):
            self._tags = {}
        if enabled is not None:
            self._configs[plugin_name] = enabled
        elif plugin_name not in self._configs:
            self._configs[plugin_name] = True
        if description is not None:
            self._descriptions[plugin_name] = description
        if tag is not None:
            self._tags[plugin_name] = tag
        if config is not None:
            self._custom_configs[plugin_name] = config
        return self.get_scraper_config(plugin_name)

    def set_scraper_config(self, plugin_name: str, enabled: bool):
        self._configs[plugin_name] = enabled

    def update_scraper_settings(self, plugin_name: str, config: dict):
        self._custom_configs[plugin_name] = config
        if plugin_name not in self._configs:
            self._configs[plugin_name] = True

    def record_scraper_failure(self, plugin_name: str, reason: str, cooldown_until: datetime | None, consecutive_failures: int):
        self._failures[plugin_name] = {
            "reason": reason,
            "cooldown_until": cooldown_until,
            "consecutive_failures": consecutive_failures,
        }

    def record_scraper_success(self, plugin_name: str):
        if plugin_name in self._failures:
            self._failures[plugin_name] = {
                "reason": None,
                "cooldown_until": None,
                "consecutive_failures": 0,
            }

    def reset_scraper_cooldown(self, plugin_name: str):
        if plugin_name in self._failures:
            self._failures[plugin_name]["cooldown_until"] = None
            self._failures[plugin_name]["consecutive_failures"] = 0
            self._failures[plugin_name]["reason"] = None

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


def test_update_scraper_tag_use_case() -> None:
    from sentinel_analysis.application.use_cases.manage_scrapers import GetScraperDetail, UpdateScraper
    repo = MemoryAISRepository()
    plugin = DummyPlugin("TagPlugin")
    registry = DynamicAISPluginRegistry([plugin])

    update_case = UpdateScraper(registry, repo)
    updated = update_case.execute("TagPlugin", tag="High-Priority Satellite", description="Custom satellite tag")

    assert updated["tag"] == "High-Priority Satellite"
    assert updated["category"] == "High-Priority Satellite"
    assert updated["description"] == "Custom satellite tag"

    get_case = GetScraperDetail(registry, repo)
    detail = get_case.execute("TagPlugin")
    assert detail["tag"] == "High-Priority Satellite"
    assert detail["category"] == "High-Priority Satellite"


def test_update_scraper_config_use_case() -> None:
    from sentinel_analysis.application.use_cases.manage_scrapers import UpdateScraperConfig
    repo = MemoryAISRepository()
    plugin = DummyPlugin("ConfigPlugin")
    registry = DynamicAISPluginRegistry([plugin])

    use_case = UpdateScraperConfig(registry, repo)
    res = use_case.execute("ConfigPlugin", {"proxy_url": "http://127.0.0.1:8080", "timeout": 45})

    assert res["status"] == "updated"
    assert res["config"]["proxy_url"] == "http://127.0.0.1:8080"
    assert res["config"]["timeout"] == 45

    loaded = repo.get_scraper_config("ConfigPlugin")
    assert loaded["config"]["proxy_url"] == "http://127.0.0.1:8080"


def test_reset_scraper_cooldown_use_case() -> None:
    from sentinel_analysis.application.use_cases.manage_scrapers import ResetScraperCooldown
    repo = MemoryAISRepository()
    plugin = DummyPlugin("CooldownPlugin")
    registry = DynamicAISPluginRegistry([plugin])

    repo.set_scraper_config("CooldownPlugin", True)
    future_time = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    repo.record_scraper_failure("CooldownPlugin", "429 Rate Limit", future_time, 2)

    cfg = repo.get_scraper_config("CooldownPlugin")
    assert cfg["cooldown_until"] == future_time
    assert cfg["consecutive_failures"] == 2

    use_case = ResetScraperCooldown(registry, repo)
    res = use_case.execute("CooldownPlugin")
    assert res["status"] == "reset"

    cfg_reset = repo.get_scraper_config("CooldownPlugin")
    assert cfg_reset["cooldown_until"] is None
    assert cfg_reset["consecutive_failures"] == 0


def test_anti_scraping_exponential_backoff_and_cooldown() -> None:
    repo = MemoryAISRepository()
    plugin_bot = DummyPlugin("BotPlugin")
    # Simulate a Cloudflare 403 Bot protection failure
    def failing_fetch(bbox, time_range):
        raise RuntimeError("HTTP 403 Forbidden: Cloudflare Turnstile bot detection triggered")
    plugin_bot.fetch = failing_fetch

    registry = DynamicAISPluginRegistry([plugin_bot])
    repo.set_scraper_config("BotPlugin", True)

    ingest = IngestAIS(registry, repo)
    bbox = BoundingBox(103.0, 1.0, 104.0, 2.0)

    # First bot failure -> 15 min cooldown
    res1 = ingest.execute(bbox, (None, None))
    cfg1 = repo.get_scraper_config("BotPlugin")
    assert cfg1["consecutive_failures"] == 1
    assert cfg1["cooldown_until"] is not None
    assert "Cloudflare Turnstile bot detection" in cfg1["last_failure_reason"]

    # In automated run, it should be skipped with COOLDOWN_SKIPPED status
    res2 = ingest.execute(bbox, (None, None))
    assert res2["total_inserted"] == 0
    assert len(res2["logs"]) == 1
    assert res2["logs"][0]["status"] == "COOLDOWN_SKIPPED"


def test_cooldown_skips_scraper_in_automated_ingest_but_runs_other_scrapers() -> None:
    repo = MemoryAISRepository()
    vessel = Vessel(imo="1234567", mmsi="123456789", name="TEST VESSEL")
    pos = VesselPosition(mmsi="123456789", latitude=1.25, longitude=103.85, timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc))
    rec = AISRecord(vessel, pos)

    plugin_healthy = DummyPlugin("HealthyPlugin", [rec])
    plugin_cooling = DummyPlugin("CoolingPlugin", [rec])

    registry = DynamicAISPluginRegistry([plugin_healthy, plugin_cooling])
    repo.set_scraper_config("HealthyPlugin", True)
    repo.set_scraper_config("CoolingPlugin", True)

    # Set CoolingPlugin to active cooldown
    future_time = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    repo.record_scraper_failure("CoolingPlugin", "HTTP 429 Too Many Requests", future_time, 1)

    ingest = IngestAIS(registry, repo)
    res = ingest.execute(BoundingBox(103.0, 1.0, 104.0, 2.0), (None, None))

    assert res["total_inserted"] == 1
    assert plugin_healthy.fetch_called is True
    assert plugin_cooling.fetch_called is False

    logs = res["logs"]
    cooling_log = next(l for l in logs if l["plugin"] == "CoolingPlugin")
    assert cooling_log["status"] == "COOLDOWN_SKIPPED"


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


def test_sqlite_ais_repository_settings_and_cooldown_lifecycle() -> None:
    from pathlib import Path
    import os
    from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository

    runtime_dir = Path(__file__).resolve().parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "test_ais_lifecycle.db"
    if db_path.exists():
        try:
            os.remove(db_path)
        except OSError:
            pass

    repo = SQLiteAISRepository(db_path)

    # 1. Update settings
    repo.update_scraper_settings("VesselFinderPlugin", {"proxy_url": "socks5://127.0.0.1:9050", "timeout": 30})
    details = repo.get_all_scraper_details()
    assert "VesselFinderPlugin" in details
    vf_detail = details["VesselFinderPlugin"]
    assert vf_detail["config"]["proxy_url"] == "socks5://127.0.0.1:9050"
    assert vf_detail["config"]["timeout"] == 30

    # 2. Record rate limit failure -> Cooldown
    cool_time = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    repo.record_scraper_failure("VesselFinderPlugin", "429 Too Many Requests", cool_time, 1)

    cfg = repo.get_scraper_config("VesselFinderPlugin")
    assert cfg["consecutive_failures"] == 1
    assert "429 Too Many Requests" in cfg["last_failure_reason"]
    assert cfg["cooldown_until"] is not None

    # 3. Reset cooldown
    repo.reset_scraper_cooldown("VesselFinderPlugin")
    cfg_reset = repo.get_scraper_config("VesselFinderPlugin")
    assert cfg_reset["cooldown_until"] is None
    assert cfg_reset["consecutive_failures"] == 0

    # 4. Record success
    repo.record_scraper_failure("VesselFinderPlugin", "temporary error", cool_time, 2)
    repo.record_scraper_success("VesselFinderPlugin")
    cfg_succ = repo.get_scraper_config("VesselFinderPlugin")
    assert cfg_succ["cooldown_until"] is None
    assert cfg_succ["consecutive_failures"] == 0

    if db_path.exists():
        try:
            os.remove(db_path)
        except OSError:
            pass


def test_plugins_zone_delay_and_size_configuration() -> None:
    vf = VesselFinderPlugin()
    assert vf.zone_delay == 0.0
    assert vf.zone_size_nm == 10.0
    vf.configure({"zone_delay_seconds": 2.5, "zone_size_nm": 15.0})
    assert vf.zone_delay == 2.5
    assert vf.zone_size_nm == 15.0

    aprs = AprsFiPlugin()
    assert aprs.zone_delay == 0.0
    assert aprs.zone_size_nm == 10.0
    aprs.configure({"zone_delay": 1.75, "zone_size_nm": 8.0})
    assert aprs.zone_delay == 1.75
    assert aprs.zone_size_nm == 8.0

    af = AISFriendsPlugin()
    assert af.zone_delay == 0.0
    assert af.zone_size_nm == 10.0
    af.configure({"zone_delay_seconds": 3.0, "zone_size_nm": 20.0})
    assert af.zone_delay == 3.0
    assert af.zone_size_nm == 20.0


def test_zone_scan_delay_pacing_during_multi_zone_fetch() -> None:
    from unittest.mock import MagicMock, patch

    # Create a large bounding box that splits into multiple zones
    large_bbox = BoundingBox(103.50, 1.00, 104.10, 1.60)
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    plugin = AISFriendsPlugin(session=mock_session, zone_delay=1.5, zone_size_nm=10.0)

    with patch("time.sleep") as mock_sleep:
        records = plugin.fetch(large_bbox)
        # Should have split into > 1 zones
        assert mock_session.get.call_count > 1
        # Should have slept with 1.5s between pieces (call_count == total_zones - 1)
        assert mock_sleep.call_count == mock_session.get.call_count - 1
        mock_sleep.assert_called_with(1.5)


def test_sqlite_ais_repository_log_execution_cooldown_skipped() -> None:
    from pathlib import Path
    import os
    from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository

    runtime_dir = Path(__file__).resolve().parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "test_ais_cooldown_logs.db"
    if db_path.exists():
        try:
            os.remove(db_path)
        except OSError:
            pass

    repo = SQLiteAISRepository(db_path)

    # 1. Log COOLDOWN_SKIPPED
    repo.log_execution("TestCooldownPlugin", "COOLDOWN_SKIPPED", 0, "Rate limited cooldown")
    repo.log_execution("TestCooldownPlugin", "SUCCESS", 25, None)

    # 2. Query logs filtered by status
    cooldown_logs = repo.get_scraper_logs(status="COOLDOWN_SKIPPED")
    assert len(cooldown_logs) == 1
    assert cooldown_logs[0]["plugin_name"] == "TestCooldownPlugin"
    assert cooldown_logs[0]["status"] == "COOLDOWN_SKIPPED"
    assert cooldown_logs[0]["records_inserted"] == 0
    assert "Rate limited" in cooldown_logs[0]["error_message"]

    all_logs = repo.get_scraper_logs()
    assert len(all_logs) == 2

    # 3. Check stats
    stats = repo.get_scraper_stats()
    assert "TestCooldownPlugin" in stats
    plugin_stats = stats["TestCooldownPlugin"]
    assert plugin_stats["total_runs"] == 2
    assert plugin_stats["success_runs"] == 1
    assert plugin_stats["cooldown_runs"] == 1
    assert plugin_stats["total_records"] == 25

    if db_path.exists():
        try:
            os.remove(db_path)
        except OSError:
            pass


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite

