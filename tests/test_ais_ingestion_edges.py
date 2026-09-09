"""Edge-case tests for provider configuration and failure handling."""

from datetime import datetime, timedelta, timezone
import unittest

from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.domain.entities import BoundingBox


BBOX = BoundingBox(103.0, 1.0, 104.0, 2.0)


class PluginStub:
    name = "EdgePlugin"

    def __init__(self, error=None):
        self.error = error
        self.authenticated = False
        self.config = None

    def configure(self, config):
        self.config = config

    def authenticate(self):
        self.authenticated = True
        if self.error is not None:
            raise self.error

    def fetch(self, bbox, time_range):
        return []


class RegistryStub:
    def __init__(self, plugin):
        self.plugin = plugin

    def get_plugins(self, name=None):
        return [self.plugin] if name is None or name == self.plugin.name else []


class RepositoryStub:
    def __init__(self, details):
        self.details = details
        self.execution_logs = []
        self.failures = []
        self.successes = []
        self.triggers = []
        self.updates = []

    def get_all_scraper_details(self):
        return {"EdgePlugin": self.details}

    def get_scraper_config(self, name):
        return self.details

    def save_records(self, records, source_plugin):
        return len(list(records))

    def log_trigger(self, plugin_name, trigger_reason=None, status="RUNNING"):
        self.triggers.append((plugin_name, trigger_reason, status))
        return 1

    def update_execution_log(self, log_id, status, records_inserted=0, error_message=None):
        self.updates.append((log_id, status, records_inserted, error_message))

    def log_execution(self, *args, **kwargs):
        self.execution_logs.append((args, kwargs))

    def record_scraper_failure(self, plugin_name, error, cooldown_until, consecutive_failures):
        self.failures.append((plugin_name, error, cooldown_until, consecutive_failures))

    def record_scraper_success(self, plugin_name):
        self.successes.append(plugin_name)


class TestAISIngestionEdges(unittest.TestCase):
    def test_disabled_provider_is_skipped_without_authentication(self):
        plugin = PluginStub()
        repo = RepositoryStub({"enabled": False})
        result = IngestAIS(RegistryStub(plugin), repo).execute(BBOX, (None, None))

        self.assertEqual(result["logs"][0]["status"], "DISABLED_SKIPPED")
        self.assertFalse(plugin.authenticated)
        self.assertEqual(repo.triggers, [])

    def test_provider_on_cooldown_is_skipped(self):
        plugin = PluginStub()
        repo = RepositoryStub({
            "enabled": True,
            "cooldown_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        result = IngestAIS(RegistryStub(plugin), repo).execute(BBOX, (None, None))

        self.assertEqual(result["logs"][0]["status"], "COOLDOWN_SKIPPED")
        self.assertFalse(plugin.authenticated)
        self.assertEqual(repo.triggers, [])

    def test_rate_limit_failure_records_backoff_and_configuration(self):
        plugin = PluginStub(RuntimeError("429 too many requests"))
        repo = RepositoryStub({"enabled": True, "config": {"zone_delay": 0.2}, "consecutive_failures": 1})
        result = IngestAIS(RegistryStub(plugin), repo).execute(BBOX, (None, None))

        self.assertEqual(result["logs"][0]["status"], "FAILED")
        self.assertEqual(plugin.config, {"zone_delay": 0.2})
        self.assertEqual(len(repo.failures), 1)
        _, error, cooldown_until, failures = repo.failures[0]
        self.assertIn("429", error)
        self.assertIsNotNone(cooldown_until)
        self.assertEqual(failures, 2)
        self.assertEqual(repo.updates[0][1], "FAILED")

    def test_success_records_success_callback_and_trigger_reason(self):
        plugin = PluginStub()
        repo = RepositoryStub({"enabled": True})
        result = IngestAIS(RegistryStub(plugin), repo).execute(
            BBOX,
            (None, None),
            trigger_reason="Automatic test scrape",
        )

        self.assertEqual(result["total_inserted"], 0)
        self.assertEqual(repo.successes, ["EdgePlugin"])
        self.assertEqual(repo.triggers[0][1], "Automatic test scrape")
        self.assertEqual(repo.updates[0][1], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
