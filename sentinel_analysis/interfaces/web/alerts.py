"""Web routes and API endpoints for maritime alerts and webhook dispatching."""

from datetime import datetime, timezone
import uuid
from flask import Blueprint, jsonify, request

from sentinel_analysis.domain.entities import MaritimeAlert
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    boolean,
    json_object,
    optional_string,
    required_string,
)

blueprint = Blueprint("alerts", __name__)


def _webhook_to_dict(wh) -> dict:
    return {
        "id": wh.id,
        "url": wh.url,
        "service_type": wh.service_type,
        "name": wh.name,
        "enabled": wh.enabled,
        "min_severity": wh.min_severity,
        "secret_token": wh.secret_token,
        "created_at": wh.created_at.isoformat() if wh.created_at else None,
    }


@blueprint.route("/api/alerts/webhooks", methods=["GET"])
def list_webhooks():
    """List configured maritime alert webhooks."""
    app_container = container()
    enabled_only_param = request.args.get("enabled_only", "").lower() in {"1", "true", "yes"}
    webhooks = app_container.manage_webhooks.list_webhooks(enabled_only=enabled_only_param)
    return jsonify({"webhooks": [_webhook_to_dict(w) for w in webhooks]})


@blueprint.route("/api/alerts/webhooks", methods=["POST"])
def create_webhook():
    """Register a new webhook subscription."""
    app_container = container()
    payload = json_object()

    url = required_string(payload, "url")
    service_type = optional_string(payload, "service_type") or "generic"
    name = optional_string(payload, "name") or ""
    min_severity = optional_string(payload, "min_severity") or "INFO"
    secret_token = optional_string(payload, "secret_token")
    enabled = boolean(payload, "enabled", default=True)
    webhook_id = optional_string(payload, "id")

    webhook = app_container.manage_webhooks.create_webhook(
        url=url,
        service_type=service_type,
        name=name,
        min_severity=min_severity,
        secret_token=secret_token,
        enabled=enabled,
        webhook_id=webhook_id,
    )
    return jsonify({"webhook": _webhook_to_dict(webhook)}), 201


@blueprint.route("/api/alerts/webhooks/<webhook_id>", methods=["GET"])
def get_webhook(webhook_id: str):
    """Retrieve details for a specific webhook."""
    app_container = container()
    webhook = app_container.manage_webhooks.get_webhook(webhook_id)
    if webhook is None:
        return jsonify({"error": f"Webhook '{webhook_id}' not found"}), 404
    return jsonify({"webhook": _webhook_to_dict(webhook)})


@blueprint.route("/api/alerts/webhooks/<webhook_id>", methods=["DELETE"])
def delete_webhook(webhook_id: str):
    """Remove a webhook subscription."""
    app_container = container()
    deleted = app_container.manage_webhooks.delete_webhook(webhook_id)
    if not deleted:
        return jsonify({"error": f"Webhook '{webhook_id}' not found"}), 404
    return jsonify({"deleted": True, "webhook_id": webhook_id})


@blueprint.route("/api/alerts/webhooks/<webhook_id>/test", methods=["POST"])
def test_webhook(webhook_id: str):
    """Send a connectivity test ping to a webhook."""
    app_container = container()
    webhook = app_container.manage_webhooks.get_webhook(webhook_id)
    if webhook is None:
        return jsonify({"error": f"Webhook '{webhook_id}' not found"}), 404

    success = app_container.manage_webhooks.test_webhook(webhook_id)
    return jsonify({
        "success": success,
        "webhook_id": webhook_id,
        "service_type": webhook.service_type,
    })


@blueprint.route("/api/alerts/dispatch", methods=["POST"])
def dispatch_alert():
    """Trigger dispatch of a maritime alert to matching subscribed webhooks."""
    app_container = container()
    payload = json_object()

    event_type = required_string(payload, "event_type")
    severity = optional_string(payload, "severity") or "INFO"
    title = required_string(payload, "title")
    summary = required_string(payload, "summary")
    details = payload.get("details", {})
    if not isinstance(details, dict):
        raise RequestValidationError("details must be a dictionary")

    alert_id = optional_string(payload, "alert_id") or f"alt-{uuid.uuid4().hex[:10]}"

    alert = MaritimeAlert(
        alert_id=alert_id,
        event_type=event_type,
        severity=severity,
        title=title,
        summary=summary,
        details=details,
        timestamp=datetime.now(timezone.utc),
    )

    result = app_container.dispatch_alert.execute(alert)
    return jsonify(result)
