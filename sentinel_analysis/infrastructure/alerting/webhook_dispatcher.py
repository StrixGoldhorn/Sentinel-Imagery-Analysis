"""Multi-service HTTP webhook dispatcher for maritime alerts."""

import hmac
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
import urllib.error
import urllib.request
import uuid

from sentinel_analysis.application.ports.alerting import WebhookDispatcher
from sentinel_analysis.domain.entities import MaritimeAlert, WebhookConfig

logger = logging.getLogger(__name__)


class HTTPWebhookDispatcher(WebhookDispatcher):
    """Dispatches maritime alerts to external endpoints (Generic JSON, Slack, Discord, Telegram)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = float(timeout)

    def dispatch(self, webhook: WebhookConfig, alert: MaritimeAlert) -> bool:
        """Deliver an alert to a specific external webhook service."""
        try:
            payload = self._build_payload(webhook, alert)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Sentinel-Maritime-Surveillance/1.0",
            }

            body_bytes = json.dumps(payload, default=str).encode("utf-8")

            if webhook.secret_token:
                signature = hmac.new(
                    webhook.secret_token.encode("utf-8"),
                    body_bytes,
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Signature"] = f"sha256={signature}"
                headers["X-Sentinel-Timestamp"] = (
                    alert.timestamp or datetime.now(timezone.utc)
                ).isoformat()

            req = urllib.request.Request(
                webhook.url,
                data=body_bytes,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                status = response.getcode()
                return 200 <= status < 300
        except Exception as exc:
            logger.warning(
                "Failed to dispatch alert %s to webhook %s (%s): %s",
                alert.alert_id,
                webhook.id,
                webhook.service_type,
                exc,
            )
            return False

    def test_ping(self, webhook: WebhookConfig) -> bool:
        """Send a test connectivity ping to the webhook endpoint."""
        ping_alert = MaritimeAlert(
            alert_id=f"ping-{uuid.uuid4().hex[:8]}",
            event_type="SYSTEM_PING",
            severity="INFO",
            title="Sentinel Webhook Test",
            summary=f"Connectivity test for webhook '{webhook.name or webhook.id}' succeeded.",
            details={"service_type": webhook.service_type, "status": "CONNECTED"},
            timestamp=datetime.now(timezone.utc),
        )
        return self.dispatch(webhook, ping_alert)

    def _build_payload(self, webhook: WebhookConfig, alert: MaritimeAlert) -> dict[str, Any]:
        st = webhook.service_type.lower()
        if st == "slack":
            return self._format_slack(alert)
        if st == "discord":
            return self._format_discord(alert)
        if st == "telegram":
            return self._format_telegram(webhook, alert)
        return self._format_generic(alert)

    def _format_generic(self, alert: MaritimeAlert) -> dict[str, Any]:
        ts = alert.timestamp or datetime.now(timezone.utc)
        return {
            "event": "maritime_alert",
            "alert_id": alert.alert_id,
            "event_type": alert.event_type,
            "severity": alert.severity,
            "title": alert.title,
            "summary": alert.summary,
            "details": alert.details,
            "timestamp": ts.isoformat(),
        }

    def _format_slack(self, alert: MaritimeAlert) -> dict[str, Any]:
        color_map = {"INFO": "#36a64f", "WARNING": "#ecb22e", "CRITICAL": "#e01e5a"}
        fields = [
            {"title": "Event Type", "value": alert.event_type, "short": True},
            {"title": "Severity", "value": alert.severity, "short": True},
        ]
        for k, v in list(alert.details.items())[:6]:
            fields.append({"title": k.replace("_", " ").title(), "value": str(v), "short": True})

        ts = alert.timestamp or datetime.now(timezone.utc)
        return {
            "text": f"[{alert.severity}] {alert.title}: {alert.summary}",
            "attachments": [
                {
                    "color": color_map.get(alert.severity, "#439FE0"),
                    "title": f"🚨 {alert.title}",
                    "text": alert.summary,
                    "fields": fields,
                    "footer": "Sentinel Maritime Surveillance",
                    "ts": int(ts.timestamp()),
                }
            ],
        }

    def _format_discord(self, alert: MaritimeAlert) -> dict[str, Any]:
        color_map = {"INFO": 0x3498DB, "WARNING": 0xF1C40F, "CRITICAL": 0xE74C3C}
        fields = [
            {"name": "Event Type", "value": alert.event_type, "inline": True},
            {"name": "Severity", "value": alert.severity, "inline": True},
        ]
        for k, v in list(alert.details.items())[:6]:
            fields.append({"name": k.replace("_", " ").title(), "value": str(v), "inline": True})

        ts = alert.timestamp or datetime.now(timezone.utc)
        return {
            "content": f"**[{alert.severity}] {alert.title}**\n{alert.summary}",
            "embeds": [
                {
                    "title": alert.title,
                    "description": alert.summary,
                    "color": color_map.get(alert.severity, 0x3498DB),
                    "fields": fields,
                    "footer": {"text": "Sentinel Maritime Surveillance"},
                    "timestamp": ts.isoformat(),
                }
            ],
        }

    def _format_telegram(self, webhook: WebhookConfig, alert: MaritimeAlert) -> dict[str, Any]:
        chat_id = webhook.secret_token or alert.details.get("chat_id", "@maritime_alerts")
        details_text = ""
        if alert.details:
            details_lines = [f"• *{k}*: `{v}`" for k, v in list(alert.details.items())[:5]]
            details_text = "\n" + "\n".join(details_lines)

        text = (
            f"🚨 *[{alert.severity}] {alert.title}*\n\n"
            f"{alert.summary}\n"
            f"*Event:* `{alert.event_type}`"
            f"{details_text}"
        )
        return {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
