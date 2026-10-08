"""Application ports for maritime alerts and webhook dispatching."""

from typing import Optional, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import MaritimeAlert, WebhookConfig


@runtime_checkable
class WebhookDispatcher(Protocol):
    """Protocol for sending alert notifications to external webhook endpoints."""

    def dispatch(self, webhook: WebhookConfig, alert: MaritimeAlert) -> bool:
        """Deliver an alert to a specific external webhook service."""
        ...

    def test_ping(self, webhook: WebhookConfig) -> bool:
        """Send a test connectivity ping to the webhook endpoint."""
        ...


@runtime_checkable
class WebhookRepository(Protocol):
    """Protocol for persisting and managing webhook configurations."""

    def save(self, webhook: WebhookConfig) -> None:
        """Create or update a webhook configuration."""
        ...

    def get(self, webhook_id: str) -> Optional[WebhookConfig]:
        """Retrieve a webhook configuration by ID."""
        ...

    def list(self, enabled_only: bool = False) -> list[WebhookConfig]:
        """List configured webhooks."""
        ...

    def delete(self, webhook_id: str) -> bool:
        """Delete a webhook configuration by ID."""
        ...
