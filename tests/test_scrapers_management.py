from datetime import datetime, timezone
from pathlib import Path
import unittest

from sentinel_analysis.application.ports.ais import AISPlugin, AISTimeRange
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.application.use_cases.manage_scrapers import (
    GetScraperLogsUseCase,
    ListScrapers,
    ToggleScraper,
)
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository


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


class TestScrapersManagement(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("tests/runtime/test_scrapers_db.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        self.repo = SQLiteAISRepository(self.db_path)


    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_scraper_config_crud(self):
        self.assertIsNone(self.repo.get_scraper_config("VesselFinderPlugin"))

        self.repo.set_scraper_config("VesselFinderPlugin", False)
        config = self.repo.get_scraper_config("VesselFinderPlugin")
        self.assertIsNotNone(config)
        self.assertEqual(config["plugin_name"], "VesselFinderPlugin")
        self.assertFalse(config["enabled"])

        all_configs = self.repo.get_all_scraper_configs()
        self.assertEqual(all_configs.get("VesselFinderPlugin"), False)

        self.repo.set_scraper_config("VesselFinderPlugin", True)
        all_configs_updated = self.repo.get_all_scraper_configs()
        self.assertEqual(all_configs_updated.get("VesselFinderPlugin"), True)

    def test_scraper_stats_aggregation(self):
        self.repo.log_execution("PluginA", "SUCCESS", 25, None)
        self.repo.log_execution("PluginA", "SUCCESS", 15, None)
        self.repo.log_execution("PluginA", "FAILED", 0, "Network timeout")
        self.repo.log_execution("PluginB", "SUCCESS", 10, None)

        stats = self.repo.get_scraper_stats()
        self.assertIn("PluginA", stats)
        self.assertIn("PluginB", stats)

        stat_a = stats["PluginA"]
        self.assertEqual(stat_a["total_runs"], 3)
        self.assertEqual(stat_a["total_records"], 40)
        self.assertEqual(stat_a["success_runs"], 2)
        self.assertEqual(stat_a["failed_runs"], 1)

        stat_b = stats["PluginB"]
        self.assertEqual(stat_b["total_runs"], 1)
        self.assertEqual(stat_b["total_records"], 10)

    def test_list_scrapers_use_case(self):
        plugin_a = DummyPlugin("PluginA")
        plugin_b = DummyPlugin("PluginB")
        registry = DynamicAISPluginRegistry([plugin_a, plugin_b])

        self.repo.set_scraper_config("PluginA", True)
        self.repo.set_scraper_config("PluginB", False)
        self.repo.log_execution("PluginA", "SUCCESS", 12, None)

        use_case = ListScrapers(registry, self.repo)
        result = use_case.execute()

        scrapers = result["scrapers"]
        metrics = result["metrics"]

        self.assertEqual(len(scrapers), 2)
        self.assertEqual(metrics["total_scrapers"], 2)
        self.assertEqual(metrics["active_scrapers"], 1)
        self.assertEqual(metrics["total_records_ingested"], 12)

        scraper_a = next(s for s in scrapers if s["name"] == "PluginA")
        self.assertTrue(scraper_a["enabled"])
        self.assertEqual(scraper_a["total_runs"], 1)

        scraper_b = next(s for s in scrapers if s["name"] == "PluginB")
        self.assertFalse(scraper_b["enabled"])

    def test_toggle_scraper_use_case(self):
        plugin = DummyPlugin("TogglePlugin")
        registry = DynamicAISPluginRegistry([plugin])
        use_case = ToggleScraper(registry, self.repo)

        res_off = use_case.execute("TogglePlugin", False)
        self.assertFalse(res_off["enabled"])
        self.assertFalse(self.repo.get_scraper_config("TogglePlugin")["enabled"])

        res_on = use_case.execute("TogglePlugin", True)
        self.assertTrue(res_on["enabled"])
        self.assertTrue(self.repo.get_scraper_config("TogglePlugin")["enabled"])

    def test_ingest_ais_skips_disabled_scrapers(self):
        vessel = Vessel(imo="1234567", mmsi="123456789", name="TEST VESSEL")
        pos = VesselPosition(mmsi="123456789", latitude=1.25, longitude=103.85, timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc))
        rec = AISRecord(vessel, pos)

        plugin_active = DummyPlugin("ActivePlugin", [rec])
        plugin_disabled = DummyPlugin("DisabledPlugin", [rec])
        registry = DynamicAISPluginRegistry([plugin_active, plugin_disabled])

        self.repo.set_scraper_config("DisabledPlugin", False)
        self.repo.set_scraper_config("ActivePlugin", True)

        ingest = IngestAIS(registry, self.repo)
        result = ingest.execute(BoundingBox(103.0, 1.0, 104.0, 2.0), (None, None))

        self.assertEqual(result["total_inserted"], 1)
        self.assertTrue(plugin_active.fetch_called)
        self.assertFalse(plugin_disabled.fetch_called)

    def test_get_scraper_logs_use_case(self):
        self.repo.log_execution("PluginX", "SUCCESS", 5, None)
        self.repo.log_execution("PluginY", "FAILED", 0, "Connection refused")

        use_case = GetScraperLogsUseCase(self.repo)
        all_logs = use_case.execute()
        self.assertEqual(all_logs["count"], 2)
        self.assertEqual(all_logs["metrics"]["total_runs"], 2)

        # Filter by status
        failed_logs = use_case.execute(status="FAILED")
        self.assertEqual(failed_logs["count"], 1)
        self.assertEqual(failed_logs["logs"][0]["plugin_name"], "PluginY")


if __name__ == "__main__":
    unittest.main()
