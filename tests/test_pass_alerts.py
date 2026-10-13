"""Tests for missed satellite passes and delayed imagery ingestion alerts."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from sentinel_analysis.application.use_cases.evaluate_pass_alerts import EvaluatePassAlerts
from sentinel_analysis.application.use_cases.manage_alerts import DispatchMaritimeAlert
from sentinel_analysis.domain.entities import (
    Acquisition,
    AreaOfInterest,
    BoundingBox,
    MaritimeAlert,
    PostPassIngestionJob,
    Scan,
    WebhookConfig,
)
from sentinel_analysis.domain.pass_alerts import (
    PassAlertEventType,
    PassAlertSeverity,
    create_delayed_ingestion_alert,
    create_missed_pass_alert,
)
from sentinel_analysis.interfaces.web.application import create_app


class StubAOIRepository:
    def __init__(self, aois=None):
        self._aois = {a.id: a for a in (aois or [])}

    def list(self):
        return list(self._aois.values())

    def get(self, aoi_id):
        return self._aois.get(aoi_id)


class StubPostPassRepo:
    def __init__(self, jobs=None):
        self._jobs = list(jobs or [])

    def list_pending(self, limit=100):
        return [j for j in self._jobs if j.status in ("PENDING_PASS", "POLLING_CATALOG", "INGESTING")][:limit]

    def list(self):
        return list(self._jobs)


class StubWebhookRepository:
    def __init__(self, webhooks=None):
        self._webhooks = list(webhooks or [])

    def list(self, enabled_only=True):
        if enabled_only:
            return [w for w in self._webhooks if w.enabled]
        return list(self._webhooks)


class StubWebhookDispatcher:
    def __init__(self):
        self.dispatched = []

    def dispatch(self, webhook, alert):
        self.dispatched.append((webhook, alert))
        return True


class StubPassMonitor:
    def __init__(self, monitors=None):
        self._monitors = list(monitors or [])

    def get_active_monitors(self):
        return list(self._monitors)


class TestPassAlertsDomain(unittest.TestCase):
    def test_create_delayed_ingestion_alert(self):
        now = datetime.now(timezone.utc)
        pass_time = now - timedelta(hours=3)
        alert = create_delayed_ingestion_alert(
            job_id=42,
            aoi_id=1,
            aoi_name="Strait of Malacca",
            pass_time=pass_time,
            satellite="Sentinel-1A",
            elapsed_minutes=180.0,
            delay_threshold_minutes=120.0,
            attempts=3,
        )
        self.assertEqual(alert.event_type, PassAlertEventType.DELAYED_INGESTION.value)
        self.assertEqual(alert.severity, PassAlertSeverity.WARNING.value)
        self.assertIn("Delayed SAR Ingestion", alert.title)
        self.assertEqual(alert.details["job_id"], 42)
        self.assertEqual(alert.details["polling_attempts"], 3)
        self.assertEqual(alert.details["delay_threshold_minutes"], 120.0)

    def test_create_missed_pass_alert(self):
        now = datetime.now(timezone.utc)
        pass_time = now - timedelta(hours=7)
        alert = create_missed_pass_alert(
            aoi_id=1,
            aoi_name="Strait of Malacca",
            pass_time=pass_time,
            satellite="Sentinel-1B",
            reason="Exceeded maximum wait window of 6.0 hours",
            job_id=42,
            severity="CRITICAL",
        )
        self.assertEqual(alert.event_type, PassAlertEventType.MISSED_PASS.value)
        self.assertEqual(alert.severity, "CRITICAL")
        self.assertIn("Missed Satellite Pass", alert.title)
        self.assertEqual(alert.details["job_id"], 42)


class TestEvaluatePassAlertsUseCase(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.aoi = AreaOfInterest(id=10, name="Gibraltar Strait", bbox=BoundingBox(-6.0, 35.8, -5.2, 36.2))
        self.aoi_repo = StubAOIRepository([self.aoi])
        self.webhook = WebhookConfig(id="wh-1", url="http://webhook.test/alert", service_type="generic", min_severity="INFO")
        self.webhook_repo = StubWebhookRepository([self.webhook])
        self.webhook_dispatcher = StubWebhookDispatcher()
        self.dispatch_alert = DispatchMaritimeAlert(self.webhook_repo, self.webhook_dispatcher)

    def test_delayed_ingestion_detected_when_threshold_exceeded(self):
        delayed_job = PostPassIngestionJob(
            id=101,
            aoi_id=10,
            pass_time=self.now - timedelta(minutes=150),
            satellite="Sentinel-1A",
            orbit_direction="ASCENDING",
            status="POLLING_CATALOG",
            attempts=3,
            last_polled_at=self.now - timedelta(minutes=10),
            next_poll_at=self.now + timedelta(minutes=50),
        )
        post_pass_repo = StubPostPassRepo([delayed_job])
        evaluator = EvaluatePassAlerts(
            post_pass_repository=post_pass_repo,
            aoi_repository=self.aoi_repo,
            dispatch_alert=self.dispatch_alert,
            default_delay_threshold_minutes=120.0,
        )

        res = evaluator.execute(auto_dispatch=True)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_alerts_count"], 1)
        self.assertEqual(res["delayed_ingestion_count"], 1)
        self.assertEqual(res["dispatched_count"], 1)
        self.assertEqual(len(self.webhook_dispatcher.dispatched), 1)

    def test_no_alert_when_within_delay_threshold(self):
        recent_job = PostPassIngestionJob(
            id=102,
            aoi_id=10,
            pass_time=self.now - timedelta(minutes=45),
            satellite="Sentinel-1A",
            orbit_direction="DESCENDING",
            status="POLLING_CATALOG",
            attempts=1,
            last_polled_at=self.now - timedelta(minutes=5),
            next_poll_at=self.now + timedelta(minutes=55),
        )
        post_pass_repo = StubPostPassRepo([recent_job])
        evaluator = EvaluatePassAlerts(
            post_pass_repository=post_pass_repo,
            aoi_repository=self.aoi_repo,
            dispatch_alert=self.dispatch_alert,
            default_delay_threshold_minutes=120.0,
        )

        res = evaluator.execute(auto_dispatch=True)
        self.assertEqual(res["new_alerts_count"], 0)
        self.assertEqual(len(self.webhook_dispatcher.dispatched), 0)

    def test_missed_pass_alert_on_timed_out_job(self):
        timed_out_job = PostPassIngestionJob(
            id=103,
            aoi_id=10,
            pass_time=self.now - timedelta(hours=7),
            satellite="Sentinel-1A",
            orbit_direction="ASCENDING",
            status="TIMED_OUT",
            attempts=6,
            error_message="Wait window expired: Exceeded 6.0 hours",
        )
        post_pass_repo = StubPostPassRepo([timed_out_job])
        evaluator = EvaluatePassAlerts(
            post_pass_repository=post_pass_repo,
            aoi_repository=self.aoi_repo,
            dispatch_alert=self.dispatch_alert,
        )

        res = evaluator.execute(auto_dispatch=True)
        self.assertEqual(res["new_alerts_count"], 1)
        self.assertEqual(res["missed_pass_count"], 1)
        self.assertEqual(res["alerts"][0]["severity"], "CRITICAL")
        self.assertIn("Missed Satellite Pass", res["alerts"][0]["title"])

    def test_missed_pass_alert_on_failed_ais_pass_monitor(self):
        pass_time = self.now - timedelta(minutes=30)
        failed_monitor = {
            "aoi_id": 10,
            "aoi_name": "Gibraltar Strait",
            "pass_time": pass_time.isoformat(),
            "status": "COMPLETED",
            "scrape_attempts": 5,
            "scrapes_completed": 0,
            "scrapes_failed": 5,
            "records_ingested": 0,
            "error": "Upstream AIS API rate limited connection rejected",
        }
        monitor = StubPassMonitor([failed_monitor])
        evaluator = EvaluatePassAlerts(
            post_pass_repository=StubPostPassRepo([]),
            aoi_repository=self.aoi_repo,
            pass_monitor=monitor,
            dispatch_alert=self.dispatch_alert,
        )

        res = evaluator.execute(auto_dispatch=True)
        self.assertEqual(res["missed_pass_count"], 1)
        self.assertEqual(res["alerts"][0]["severity"], "WARNING")

    def test_alert_deduplication(self):
        delayed_job = PostPassIngestionJob(
            id=104,
            aoi_id=10,
            pass_time=self.now - timedelta(minutes=180),
            satellite="Sentinel-1A",
            orbit_direction="ASCENDING",
            status="POLLING_CATALOG",
            attempts=3,
        )
        evaluator = EvaluatePassAlerts(
            post_pass_repository=StubPostPassRepo([delayed_job]),
            aoi_repository=self.aoi_repo,
            dispatch_alert=self.dispatch_alert,
        )

        first_res = evaluator.execute(auto_dispatch=True)
        self.assertEqual(first_res["new_alerts_count"], 1)

        # Second call within same session deduplicates
        second_res = evaluator.execute(auto_dispatch=True)
        self.assertEqual(second_res["new_alerts_count"], 0)

        # After reset, it can alert again
        evaluator.reset_deduplication()
        third_res = evaluator.execute(auto_dispatch=True)
        self.assertEqual(third_res["new_alerts_count"], 1)


class TestPassAlertsWebRoutes(unittest.TestCase):
    def test_web_endpoints(self):
        from sentinel_analysis.bootstrap.config import Settings
        from sentinel_analysis.interfaces.web.security import rate_limiter

        rate_limiter.reset()
        mock_container = MagicMock()
        mock_container.settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=Path(__file__).resolve().parent / "runtime" / "pass_alert.db",
            output_root=Path(__file__).resolve().parent / "runtime" / "pass_alert_out",
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
            rate_limiting_enabled=False,
        )
        mock_container.evaluate_pass_alerts.execute.return_value = {
            "status": "success",
            "total_evaluations": 1,
            "new_alerts_count": 1,
            "delayed_ingestion_count": 1,
            "missed_pass_count": 0,
            "dispatched_count": 1,
            "alerts": [
                {
                    "alert_id": "alt-delay-1",
                    "event_type": "DELAYED_INGESTION",
                    "severity": "WARNING",
                    "title": "Delayed SAR Ingestion",
                }
            ],
        }

        app = create_app(container=mock_container, start_background_workers=False)
        client = app.test_client()

        # GET /api/alerts/pass-monitoring
        res = client.get("/api/alerts/pass-monitoring?threshold_minutes=90")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["delayed_ingestion_count"], 1)

        # POST /api/alerts/pass-monitoring/evaluate
        post_res = client.post("/api/alerts/pass-monitoring/evaluate", json={"threshold_minutes": 60, "auto_dispatch": True})
        self.assertEqual(post_res.status_code, 200)
        post_data = post_res.get_json()
        self.assertEqual(post_data["dispatched_count"], 1)
