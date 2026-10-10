import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.correlation import (
    ensure_correlation_id,
    generate_correlation_id,
    get_current_correlation_id,
    set_current_correlation_id,
)
from sentinel_analysis.domain.observability import (
    AlertDeliveryMetrics,
    DatabaseLockMetrics,
    DetectionCountMetrics,
    ImageryAgeMetrics,
    OperationalMetricsReport,
    ProviderFailureMetrics,
    QueueDepthMetrics,
    SchedulerLeadershipMetrics,
    ScanLatencyMetrics,
)
from sentinel_analysis.interfaces.web.application import create_app


class TestCorrelationDomain(unittest.TestCase):
    """Test correlation ID generation, context management, and propagation."""

    def test_generate_correlation_id(self):
        cid1 = generate_correlation_id()
        cid2 = generate_correlation_id()
        self.assertTrue(cid1.startswith("corr-"))
        self.assertTrue(cid2.startswith("corr-"))
        self.assertNotEqual(cid1, cid2)

    def test_context_var_flow(self):
        set_current_correlation_id("test-corr-42")
        try:
            self.assertEqual(get_current_correlation_id(), "test-corr-42")
            self.assertEqual(ensure_correlation_id(), "test-corr-42")
        finally:
            set_current_correlation_id(None)

        self.assertIsNone(get_current_correlation_id())
        cid = ensure_correlation_id()
        self.assertTrue(cid.startswith("corr-"))
        self.assertEqual(get_current_correlation_id(), cid)
        set_current_correlation_id(None)


class TestObservabilityMetricsDomain(unittest.TestCase):
    """Test metrics entities, dictionary conversion, and Prometheus text rendering."""

    def setUp(self):
        self.report = OperationalMetricsReport(
            timestamp=datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc),
            system_uptime_seconds=3600.0,
            queue_depth=QueueDepthMetrics(
                background_tasks_active=2,
                background_tasks_queued=5,
                post_pass_polling=1,
                post_pass_querying=0,
                post_pass_ingesting=0,
                review_queue_pending=3,
                total_queue_depth=11,
            ),
            scan_latency=ScanLatencyMetrics(
                count=25,
                mean_seconds=12.5,
                p95_seconds=22.0,
                min_seconds=3.2,
                max_seconds=45.0,
                latest_scan_duration_seconds=10.1,
            ),
            provider_failures=ProviderFailureMetrics(
                total_failures=3,
                by_provider={"copernicus": 2, "asf": 1},
                recent_failures=[{"provider": "copernicus", "error": "timeout"}],
            ),
            imagery_age=ImageryAgeMetrics(
                overall_newest_hours=1.5,
                overall_oldest_hours=48.0,
                by_aoi=[],
            ),
            scheduler_leadership=SchedulerLeadershipMetrics(
                is_leader=True,
                owner_id="node-1",
                lease_expires_at="2026-10-10T12:05:00+00:00",
                heartbeat_at="2026-10-10T12:00:00+00:00",
                time_until_expiry_seconds=300.0,
            ),
            database_locks=DatabaseLockMetrics(
                journal_mode="wal",
                busy_timeout_ms=5000,
                busy_contention_count=2,
                lock_status="normal",
                database_size_bytes=1048576,
                table_counts={"scans": 25, "aoi": 4},
            ),
            detection_counts=DetectionCountMetrics(
                cumulative_detections=120,
                by_vessel_class={"Cargo": 80, "Tanker": 40},
                anomalies={"dark_vessel": 5},
            ),
            alert_delivery=AlertDeliveryMetrics(
                total_attempts=50,
                success_count=48,
                failure_count=2,
                success_rate_percent=96.0,
                mean_latency_ms=145.0,
                p95_latency_ms=250.0,
            ),
        )

    def test_to_dict(self):
        d = self.report.to_dict()
        self.assertIn("timestamp", d)
        self.assertEqual(d["system_uptime_seconds"], 3600.0)
        self.assertEqual(d["queue_depth"]["background_tasks_queued"], 5)
        self.assertEqual(d["scan_latency"]["mean_seconds"], 12.5)
        self.assertEqual(d["provider_failures"]["total_failures"], 3)
        self.assertEqual(d["imagery_age"]["overall_newest_hours"], 1.5)
        self.assertTrue(d["scheduler_leadership"]["is_leader"])
        self.assertEqual(d["database_locks"]["busy_contention_count"], 2)
        self.assertEqual(d["detection_counts"]["cumulative_detections"], 120)
        self.assertEqual(d["alert_delivery"]["success_count"], 48)

    def test_to_prometheus_text(self):
        prom = self.report.to_prometheus_text()
        self.assertIn("sentinel_system_uptime_seconds 3600.0", prom)
        self.assertIn("sentinel_queue_depth_total 11", prom)
        self.assertIn("sentinel_scan_latency_mean_seconds 12.5", prom)
        self.assertIn('sentinel_provider_failures{provider="copernicus"} 2', prom)
        self.assertIn("sentinel_scheduler_is_leader 1", prom)
        self.assertIn("sentinel_database_busy_contention_total 2", prom)
        self.assertIn("sentinel_detections_cumulative_total 120", prom)
        self.assertIn("sentinel_alert_delivery_attempts_total 50", prom)


class TestOperationalMetricsCollectorAndWeb(unittest.TestCase):
    """Integration test for collector, migrations, container and Flask web routes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_observability.db"
        output_root = Path(self.temp_dir.name) / "outputs"
        output_root.mkdir(parents=True, exist_ok=True)

        settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=db_path,
            output_root=output_root,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
            debug=True,
        )
        self.container = ApplicationContainer(settings)

        self.app = create_app(settings=settings, container=self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self):
        self.container.shutdown()
        self.temp_dir.cleanup()

    def test_collector_records_and_collects(self):
        collector = self.container.observability_collector

        # Record provider failures
        collector.record_provider_failure("asf", "search", "Connection timeout 504")
        collector.record_provider_failure("copernicus", "download", "HTTP 429 Too Many Requests")

        # Record alert deliveries
        collector.record_alert_delivery("alert-1", "webhook", "success", 120.5, http_status=200)
        collector.record_alert_delivery("alert-2", "webhook", "failure", 450.0, http_status=500, error_message="Internal Server Error")

        report = collector.collect()
        self.assertIsInstance(report, OperationalMetricsReport)
        self.assertEqual(report.provider_failures.total_failures, 2)
        self.assertEqual(report.provider_failures.by_provider.get("asf"), 1)
        self.assertEqual(report.provider_failures.by_provider.get("copernicus"), 1)

        self.assertEqual(report.alert_delivery.total_attempts, 2)
        self.assertEqual(report.alert_delivery.success_count, 1)
        self.assertEqual(report.alert_delivery.failure_count, 1)

    def test_web_metrics_json_and_prometheus(self):
        # 1. API endpoint
        res = self.client.get("/api/observability/metrics")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("queue_depth", data)
        self.assertIn("scan_latency", data)
        self.assertIn("provider_failures", data)
        self.assertIn("imagery_age", data)
        self.assertIn("scheduler_leadership", data)
        self.assertIn("database_locks", data)
        self.assertIn("detection_counts", data)
        self.assertIn("alert_delivery", data)

        # 2. Prometheus /metrics endpoint
        res_prom = self.client.get("/metrics")
        self.assertEqual(res_prom.status_code, 200)
        self.assertIn("text/plain", res_prom.mimetype)
        text = res_prom.get_data(as_text=True)
        self.assertIn("# HELP sentinel_queue_depth_total", text)
        self.assertIn("# TYPE sentinel_queue_depth_total gauge", text)

        # 3. HTML Dashboard
        res_html = self.client.get("/observability")
        self.assertEqual(res_html.status_code, 200)
        self.assertIn("Operational Observability", res_html.get_data(as_text=True))

    def test_correlation_id_header_propagation(self):
        # Without incoming header: server generates one (prefix 'req-')
        res = self.client.get("/api/observability/metrics")
        self.assertIn("X-Correlation-ID", res.headers)
        generated_cid = res.headers["X-Correlation-ID"]
        self.assertTrue(generated_cid.startswith("req-") or generated_cid.startswith("corr-"))

        # With incoming header: server propagates it
        res_custom = self.client.get(
            "/api/observability/metrics",
            headers={"X-Correlation-ID": "trace-custom-999"},
        )
        self.assertEqual(res_custom.headers.get("X-Correlation-ID"), "trace-custom-999")


if __name__ == "__main__":
    unittest.main()
