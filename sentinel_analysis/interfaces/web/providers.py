"""Web blueprint for satellite provider health and quota dashboard."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.domain.correlation import get_current_correlation_id
from sentinel_analysis.domain.provider_fallback import AllProvidersExhaustedError
from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("providers", __name__)


@blueprint.get("/api/providers/fallback-chains")
def get_fallback_chains():
    """Return configured failover priority chains per modality."""
    c = container()
    chains = c.acquire_with_fallback.get_all_chains()
    return jsonify({
        "status": "success",
        "chains": chains,
        "correlation_id": get_current_correlation_id(),
    })


@blueprint.post("/api/providers/dispatch-with-fallback")
def dispatch_with_fallback():
    """Execute satellite acquisition dispatch with automatic failover."""
    c = container()
    payload = request.get_json(silent=True) or {}
    modality = payload.get("modality", "SAR")
    aoi_id = payload.get("aoi_id")
    bbox = payload.get("bbox")
    preferred_provider = payload.get("preferred_provider")
    allow_commercial = payload.get("allow_commercial", True)
    acquisition_params = payload.get("acquisition_params") or {}

    aoi_id_val = int(aoi_id) if aoi_id is not None else None

    try:
        result, resolution = c.acquire_with_fallback.execute_dispatch(
            modality=str(modality),
            aoi_id=aoi_id_val,
            bbox=bbox,
            preferred_provider=str(preferred_provider) if preferred_provider else None,
            allow_commercial=bool(allow_commercial),
            acquisition_params=acquisition_params,
        )
        return jsonify({
            "status": "success",
            "result": result,
            "resolution": resolution.to_dict(),
            "correlation_id": get_current_correlation_id(),
        })
    except AllProvidersExhaustedError as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
            "resolution": exc.resolution.to_dict() if exc.resolution else None,
            "correlation_id": get_current_correlation_id(),
        }), 503
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
            "correlation_id": get_current_correlation_id(),
        }), 400


@blueprint.get("/api/providers/health")
def get_providers_health():
    """Return JSON dashboard of all satellite provider health and quotas."""
    c = container()
    dashboard = c.get_provider_health.execute()
    data = dashboard.to_dict()
    data["correlation_id"] = get_current_correlation_id()
    return jsonify(data)


@blueprint.get("/api/providers/quotas")
def list_provider_quotas():
    """Return JSON list of all provider quota states."""
    c = container()
    quotas = c.provider_health_repository.list_quotas()
    return jsonify([q.to_dict() for q in quotas])


@blueprint.post("/api/providers/<provider_id>/check")
def probe_provider_health(provider_id: str):
    """Trigger a health check probe or status override for a specific provider."""
    c = container()
    payload = request.get_json(silent=True) or {}
    force_status = payload.get("force_status") or request.form.get("force_status")
    latency = payload.get("latency_ms") or request.form.get("latency_ms")
    latency_val = float(latency) if latency is not None else None

    result = c.check_provider_health.execute(
        provider_id=provider_id,
        force_status=force_status,
        latency_override_ms=latency_val,
    )
    return jsonify({
        "status": "success",
        "record": result.to_dict(),
        "correlation_id": get_current_correlation_id(),
    })


@blueprint.post("/api/providers/<provider_id>/quota")
def update_provider_quota(provider_id: str):
    """Update quota parameters or record usage for a specific provider."""
    c = container()
    payload = request.get_json(silent=True) or {}

    daily_limit = payload.get("daily_request_limit")
    monthly_limit = payload.get("monthly_gb_limit")
    credit_balance = payload.get("credit_balance_usd")
    reset_daily = payload.get("reset_daily_requests", False)

    daily_limit_val = int(daily_limit) if daily_limit is not None else None
    monthly_limit_val = float(monthly_limit) if monthly_limit is not None else None
    credit_balance_val = float(credit_balance) if credit_balance is not None else None

    record = c.update_provider_quota.execute(
        provider_id=provider_id,
        daily_request_limit=daily_limit_val,
        monthly_gb_limit=monthly_limit_val,
        credit_balance_usd=credit_balance_val,
        reset_daily_requests=bool(reset_daily),
    )
    return jsonify({
        "status": "success",
        "quota": record.to_dict(),
        "correlation_id": get_current_correlation_id(),
    })


@blueprint.get("/providers")
def providers_dashboard():
    """Render the HTML provider health and quota dashboard."""
    c = container()
    dashboard = c.get_provider_health.execute()
    return render_template(
        "providers.html",
        dashboard=dashboard,
        dashboard_dict=dashboard.to_dict(),
        correlation_id=get_current_correlation_id(),
    )
