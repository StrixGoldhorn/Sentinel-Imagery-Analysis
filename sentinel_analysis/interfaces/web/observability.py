"""Web blueprint for operational observability, metrics, and correlation telemetry."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, render_template, request

from sentinel_analysis.domain.correlation import get_current_correlation_id
from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("observability", __name__)


@blueprint.get("/api/observability/metrics")
def get_metrics_json():
    """Return JSON operational metrics report covering all 8 telemetry domains."""
    c = container()
    report = c.get_operational_metrics.execute()
    data = report.to_dict()
    data["correlation_id"] = get_current_correlation_id()
    return jsonify(data)


@blueprint.get("/metrics")
def get_prometheus_metrics():
    """Prometheus exposition format endpoint (/metrics) for scraper monitoring."""
    c = container()
    fmt = request.args.get("format", "").lower()
    if fmt == "json" or "application/json" in request.headers.get("Accept", ""):
        report = c.get_operational_metrics.execute()
        return jsonify(report.to_dict())

    report = c.get_operational_metrics.execute()
    prom_text = report.to_prometheus_text()
    return Response(
        prom_text,
        mimetype="text/plain; version=0.0.4; charset=utf-8",
    )


@blueprint.get("/observability")
def observability_dashboard():
    """Render the human-facing operational observability dashboard."""
    c = container()
    report = c.get_operational_metrics.execute()
    return render_template(
        "observability.html",
        report=report,
        report_dict=report.to_dict(),
        correlation_id=get_current_correlation_id(),
    )
