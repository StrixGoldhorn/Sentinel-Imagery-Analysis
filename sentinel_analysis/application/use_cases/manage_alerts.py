"""Application use cases for maritime alerts and webhook subscriptions."""

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from sentinel_analysis.application.ports.alerting import WebhookDispatcher, WebhookRepository
from sentinel_analysis.domain.entities import MaritimeAlert, WebhookConfig

_SEVERITY_ORDER: dict[str, int] = {
    "INFO": 1,
    "WARNING": 2,
    "CRITICAL": 3,
}


def _severity_rank(sev: str) -> int:
    return _SEVERITY_ORDER.get(str(sev).upper(), 1)


class DispatchMaritimeAlert:
    """Dispatches a maritime alert to all subscribed webhooks matching minimum severity."""

    def __init__(
        self,
        webhook_repository: WebhookRepository,
        dispatcher: WebhookDispatcher,
    ) -> None:
        self.webhook_repository = webhook_repository
        self.dispatcher = dispatcher

    def execute(self, alert: MaritimeAlert) -> dict[str, Any]:
        """Dispatch alert to matching enabled webhooks."""
        webhooks = self.webhook_repository.list(enabled_only=True)
        alert_rank = _severity_rank(alert.severity)

        matched = [w for w in webhooks if alert_rank >= _severity_rank(w.min_severity)]

        dispatched_count = 0
        failed_count = 0
        results: list[dict[str, Any]] = []

        for webhook in matched:
            success = self.dispatcher.dispatch(webhook, alert)
            if success:
                dispatched_count += 1
            else:
                failed_count += 1
            results.append(
                {
                    "webhook_id": webhook.id,
                    "name": webhook.name,
                    "service_type": webhook.service_type,
                    "success": success,
                }
            )

        return {
            "alert_id": alert.alert_id,
            "event_type": alert.event_type,
            "severity": alert.severity,
            "total_webhooks": len(webhooks),
            "matched_webhooks": len(matched),
            "dispatched": dispatched_count,
            "failed": failed_count,
            "results": results,
        }


class ManageWebhooks:
    """Manages maritime alert webhook configurations and connectivity testing."""

    def __init__(
        self,
        webhook_repository: WebhookRepository,
        dispatcher: WebhookDispatcher,
    ) -> None:
        self.webhook_repository = webhook_repository
        self.dispatcher = dispatcher

    def create_webhook(
        self,
        url: str,
        service_type: str = "generic",
        name: str = "",
        min_severity: str = "INFO",
        secret_token: Optional[str] = None,
        enabled: bool = True,
        webhook_id: Optional[str] = None,
    ) -> WebhookConfig:
        """Create and store a new webhook configuration."""
        actual_id = webhook_id or f"wh-{uuid.uuid4().hex[:12]}"
        webhook = WebhookConfig(
            id=actual_id,
            url=url,
            service_type=service_type,
            name=name or actual_id,
            enabled=enabled,
            min_severity=min_severity,
            secret_token=secret_token,
            created_at=datetime.now(timezone.utc),
        )
        self.webhook_repository.save(webhook)
        return webhook

    def list_webhooks(self, enabled_only: bool = False) -> list[WebhookConfig]:
        """List webhook configurations."""
        return self.webhook_repository.list(enabled_only=enabled_only)

    def get_webhook(self, webhook_id: str) -> Optional[WebhookConfig]:
        """Get a webhook configuration by ID."""
        return self.webhook_repository.get(webhook_id)

    def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook configuration by ID."""
        return self.webhook_repository.delete(webhook_id)

    def test_webhook(self, webhook_id: str) -> bool:
        """Send a test ping to a webhook configuration."""
        webhook = self.webhook_repository.get(webhook_id)
        if webhook is None:
            return False
        return self.dispatcher.test_ping(webhook)
