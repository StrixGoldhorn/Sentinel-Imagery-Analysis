"""Unit and integration tests for automated maritime alerting and webhooks."""

from datetime import datetime, timezone
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sentinel_analysis.application.use_cases.manage_alerts import (
    DispatchMaritimeAlert,
    ManageWebhooks,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import MaritimeAlert, WebhookConfig
from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.infrastructure.alerting.webhook_dispatcher import HTTPWebhookDispatcher
from sentinel_analysis.infrastructure.persistence.sqlite_webhooks import SQLiteWebhookRepository
from sentinel_analysis.interfaces.web.application import create_app


class TestMaritimeAlertEntities(unittest.TestCase):
    def test_webhook_config_creation_and_normalization(self):
        wh = WebhookConfig(
            id="wh-1",
            url="https://example.com/hook",
            service_type="SLACK",
            name="Operations Slack",
            min_severity="warning",
        )
        self.assertEqual(wh.id, "wh-1")
        self.assertEqual(wh.service_type, "slack")
        self.assertEqual(wh.min_severity, "WARNING")
        self.assertTrue(wh.enabled)

    def test_webhook_config_validation_failures(self):
        with self.assertRaises(DomainValidationError):
            WebhookConfig(id="", url="https://example.com")
        with self.assertRaises(DomainValidationError):
            WebhookConfig(id="wh-1", url="")

    def test_maritime_alert_creation_and_validation(self):
        alert = MaritimeAlert(
            alert_id="alt-1",
            event_type="dark_vessel",
            severity="critical",
            title="Dark Vessel Encounter",
            summary="Uncorrelated SAR contact inside territorial waters.",
            details={"lat": 1.2, "lon": 103.8},
        )
        self.assertEqual(alert.alert_id, "alt-1")
        self.assertEqual(alert.event_type, "DARK_VESSEL")
        self.assertEqual(alert.severity, "CRITICAL")
        self.assertEqual(alert.details["lat"], 1.2)

        with self.assertRaises(DomainValidationError):
            MaritimeAlert(
                alert_id="alt-2",
                event_type="",
                severity="INFO",
                title="T",
                summary="S",
            )


class TestHTTPWebhookDispatcher(unittest.TestCase):
    def setUp(self):
        self.dispatcher = HTTPWebhookDispatcher(timeout=2.0)
        self.alert = MaritimeAlert(
            alert_id="alt-test",
            event_type="TRANSSHIPMENT_RENDEZVOUS",
            severity="WARNING",
            title="Suspected STS Transfer",
            summary="Two dark vessels anchored alongside each other.",
            details={"contact_a": "s1_01", "contact_b": "s1_02", "distance_meters": 18.5},
            timestamp=datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc),
        )

    def test_payload_formatting_generic(self):
        wh = WebhookConfig(id="wh-gen", url="https://example.com/api", service_type="generic")
        payload = self.dispatcher._build_payload(wh, self.alert)
        self.assertEqual(payload["event"], "maritime_alert")
        self.assertEqual(payload["event_type"], "TRANSSHIPMENT_RENDEZVOUS")
        self.assertEqual(payload["severity"], "WARNING")
        self.assertIn("contact_a", payload["details"])

    def test_payload_formatting_slack(self):
        wh = WebhookConfig(id="wh-slk", url="https://hooks.slack.com/services/test", service_type="slack")
        payload = self.dispatcher._build_payload(wh, self.alert)
        self.assertIn("Suspected STS Transfer", payload["text"])
        self.assertIn("attachments", payload)
        self.assertEqual(len(payload["attachments"]), 1)
        self.assertEqual(payload["attachments"][0]["color"], "#ecb22e")

    def test_payload_formatting_discord(self):
        wh = WebhookConfig(id="wh-dsc", url="https://discord.com/api/webhooks/test", service_type="discord")
        payload = self.dispatcher._build_payload(wh, self.alert)
        self.assertIn("embeds", payload)
        self.assertEqual(len(payload["embeds"]), 1)
        self.assertEqual(payload["embeds"][0]["title"], "Suspected STS Transfer")

    def test_payload_formatting_telegram(self):
        wh = WebhookConfig(
            id="wh-tg",
            url="https://api.telegram.org/botTOKEN/sendMessage",
            service_type="telegram",
            secret_token="-100123456789",
        )
        payload = self.dispatcher._build_payload(wh, self.alert)
        self.assertEqual(payload["chat_id"], "-100123456789")
        self.assertEqual(payload["parse_mode"], "Markdown")
        self.assertIn("Suspected STS Transfer", payload["text"])

    @patch("urllib.request.urlopen")
    def test_dispatch_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        wh = WebhookConfig(
            id="wh-sec",
            url="https://example.com/webhook",
            service_type="generic",
            secret_token="secret_key_123",
        )
        result = self.dispatcher.dispatch(wh, self.alert)
        self.assertTrue(result)
        self.assertTrue(mock_urlopen.called)
        req = mock_urlopen.call_args[0][0]
        self.assertTrue(req.has_header("X-signature"))
        self.assertTrue(req.get_header("X-signature").startswith("sha256="))

    @patch("urllib.request.urlopen", side_effect=Exception("Network unreachable"))
    def test_dispatch_failure_handling(self, mock_urlopen):
        wh = WebhookConfig(id="wh-fail", url="https://example.com/bad", service_type="generic")
        result = self.dispatcher.dispatch(wh, self.alert)
        self.assertFalse(result)

    @patch.object(HTTPWebhookDispatcher, "dispatch", return_value=True)
    def test_test_ping(self, mock_dispatch):
        wh = WebhookConfig(id="wh-ping", url="https://example.com/ping", service_type="generic")
        success = self.dispatcher.test_ping(wh)
        self.assertTrue(success)
        mock_dispatch.assert_called_once()
        alert_arg = mock_dispatch.call_args[0][1]
        self.assertEqual(alert_arg.event_type, "SYSTEM_PING")


class TestSQLiteWebhookRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.temp_dir.name}/webhooks_test.db"
        self.repo = SQLiteWebhookRepository(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_get_list_delete(self):
        wh1 = WebhookConfig(
            id="wh-1",
            url="https://example.com/hook1",
            service_type="slack",
            name="Slack Ops",
            enabled=True,
            min_severity="INFO",
        )
        wh2 = WebhookConfig(
            id="wh-2",
            url="https://example.com/hook2",
            service_type="generic",
            name="Raw Feeds",
            enabled=False,
            min_severity="CRITICAL",
        )

        self.repo.save(wh1)
        self.repo.save(wh2)

        fetched = self.repo.get("wh-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Slack Ops")
        self.assertEqual(fetched.service_type, "slack")

        all_hooks = self.repo.list(enabled_only=False)
        self.assertEqual(len(all_hooks), 2)

        enabled_hooks = self.repo.list(enabled_only=True)
        self.assertEqual(len(enabled_hooks), 1)
        self.assertEqual(enabled_hooks[0].id, "wh-1")

        deleted = self.repo.delete("wh-1")
        self.assertTrue(deleted)
        self.assertIsNone(self.repo.get("wh-1"))
        self.assertEqual(len(self.repo.list()), 1)


class TestAlertUseCases(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock(spec=SQLiteWebhookRepository)
        self.dispatcher = MagicMock(spec=HTTPWebhookDispatcher)
        self.dispatch_uc = DispatchMaritimeAlert(self.repo, self.dispatcher)
        self.manage_uc = ManageWebhooks(self.repo, self.dispatcher)

    def test_severity_filtering(self):
        wh_info = WebhookConfig(id="wh-info", url="http://info", min_severity="INFO")
        wh_warn = WebhookConfig(id="wh-warn", url="http://warn", min_severity="WARNING")
        wh_crit = WebhookConfig(id="wh-crit", url="http://crit", min_severity="CRITICAL")

        self.repo.list.return_value = [wh_info, wh_warn, wh_crit]
        self.dispatcher.dispatch.return_value = True

        # Test INFO alert -> only wh_info
        alert_info = MaritimeAlert(
            alert_id="a1",
            event_type="AIS_GAP",
            severity="INFO",
            title="Minor Gap",
            summary="Brief transponder loss",
        )
        res_info = self.dispatch_uc.execute(alert_info)
        self.assertEqual(res_info["matched_webhooks"], 1)
        self.assertEqual(res_info["dispatched"], 1)

        # Test CRITICAL alert -> all 3
        alert_crit = MaritimeAlert(
            alert_id="a2",
            event_type="DARK_COLLISION_RISK",
            severity="CRITICAL",
            title="Collision Warning",
            summary="Imminent collision with unlit vessel",
        )
        res_crit = self.dispatch_uc.execute(alert_crit)
        self.assertEqual(res_crit["matched_webhooks"], 3)
        self.assertEqual(res_crit["dispatched"], 3)

    def test_manage_webhooks_create_and_test(self):
        self.dispatcher.test_ping.return_value = True
        webhook = self.manage_uc.create_webhook(
            url="https://example.com/test",
            service_type="discord",
            name="Test Discord",
            min_severity="WARNING",
            webhook_id="fixed-id",
        )
        self.assertEqual(webhook.id, "fixed-id")
        self.repo.save.assert_called_once_with(webhook)

        self.repo.get.return_value = webhook
        ping_res = self.manage_uc.test_webhook("fixed-id")
        self.assertTrue(ping_res)
        self.dispatcher.test_ping.assert_called_once_with(webhook)


class TestAlertsWebAPI(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            copernicus_username="test",
            copernicus_password="pwd",
            n2yo_api_key="test_n2yo",
            project_root=Path(__file__).resolve().parent.parent,
            output_root=f"{self.temp_dir.name}/output",
            cache_root=f"{self.temp_dir.name}/cache",
            database_path=f"{self.temp_dir.name}/test.db",
            debug=True,
        )
        self.container = ApplicationContainer(self.settings)
        self.app = create_app(self.settings, self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_webhook_crud_and_dispatch_api(self):
        # 1. Create webhook
        create_res = self.client.post(
            "/api/alerts/webhooks",
            data=json.dumps({
                "url": "https://example.com/webhook",
                "service_type": "generic",
                "name": "Integration Test Hook",
                "min_severity": "WARNING",
            }),
            content_type="application/json",
        )
        self.assertEqual(create_res.status_code, 201)
        data = create_res.get_json()
        wh_id = data["webhook"]["id"]
        self.assertEqual(data["webhook"]["name"], "Integration Test Hook")

        # 2. List webhooks
        list_res = self.client.get("/api/alerts/webhooks")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.get_json()["webhooks"]), 1)

        # 3. Get webhook
        get_res = self.client.get(f"/api/alerts/webhooks/{wh_id}")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.get_json()["webhook"]["id"], wh_id)

        # 4. Test webhook ping with mocked dispatcher
        with patch.object(self.container.webhook_dispatcher, "test_ping", return_value=True):
            test_res = self.client.post(f"/api/alerts/webhooks/{wh_id}/test")
            self.assertEqual(test_res.status_code, 200)
            self.assertTrue(test_res.get_json()["success"])

        # 5. Dispatch maritime alert
        with patch.object(self.container.webhook_dispatcher, "dispatch", return_value=True):
            disp_res = self.client.post(
                "/api/alerts/dispatch",
                data=json.dumps({
                    "event_type": "DARK_VESSEL",
                    "severity": "CRITICAL",
                    "title": "Alert Test",
                    "summary": "Testing maritime alert dispatching endpoint",
                    "details": {"score": 98.5},
                }),
                content_type="application/json",
            )
            self.assertEqual(disp_res.status_code, 200)
            disp_data = disp_res.get_json()
            self.assertEqual(disp_data["matched_webhooks"], 1)
            self.assertEqual(disp_data["dispatched"], 1)

        # 6. Delete webhook
        del_res = self.client.delete(f"/api/alerts/webhooks/{wh_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertTrue(del_res.get_json()["deleted"])

        # Verify not found after delete
        not_found_res = self.client.get(f"/api/alerts/webhooks/{wh_id}")
        self.assertEqual(not_found_res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
