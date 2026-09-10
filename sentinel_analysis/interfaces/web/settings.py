"""Web routes and API endpoints for managing application and feature configuration."""

from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("settings", __name__)


def _apply_scheduler_settings(app_container) -> dict[str, list[str]]:
    """Apply the scheduler settings that support live reconfiguration."""
    applied: list[str] = []
    errors: list[str] = []
    scheduler = getattr(app_container, "pass_scheduler", None)
    if scheduler is not None:
        try:
            aoi_val = app_container.settings_repository.get("aoi_check_interval_seconds")
            if aoi_val is not None:
                scheduler.set_aoi_check_interval(float(aoi_val))
                applied.append("scheduler.aoi_check_interval_seconds")
        except Exception as exc:
            errors.append(f"scheduler.aoi_check_interval_seconds: {exc}")

        try:
            worker_val = app_container.settings_repository.get("post_pass_worker_count")
            if worker_val is not None and hasattr(scheduler, "get_post_pass_worker_count"):
                scheduler.get_post_pass_worker_count()
                applied.append("scheduler.post_pass_worker_count")
        except Exception as exc:
            errors.append(f"scheduler.post_pass_worker_count: {exc}")

    ingest_post_pass = getattr(app_container, "ingest_post_pass", None)
    if ingest_post_pass is not None:
        try:
            wait_val = app_container.settings_repository.get("post_pass_max_wait_hours")
            if wait_val is not None:
                ingest_post_pass.configure_max_wait_hours(float(wait_val))
                applied.append("scheduler.post_pass_max_wait_hours")
        except Exception as exc:
            errors.append(f"scheduler.post_pass_max_wait_hours: {exc}")

    return {"applied": applied, "apply_errors": errors}


def _restart_required_settings(payload: dict) -> list[str]:
    # Deployment settings are intentionally owned by .env and are not part of
    # the settings API, so there are no restart-required UI settings here.
    return []


@blueprint.route("/settings", methods=["GET"])
def settings_page() -> str:
    """Render the Settings dashboard UI."""
    return render_template("settings.html")


@blueprint.route("/api/settings", methods=["GET"])
def get_settings_api():
    """Retrieve all configuration settings partitioned by feature section."""
    app_container = container()
    include_defs = request.args.get("definitions", "true").lower() in ("true", "1", "yes")
    section = request.args.get("section")
    try:
        settings_data = app_container.get_settings.execute(
            section=section,
            include_definitions=include_defs,
            mask_secrets=True,
        )
        runtime_settings = getattr(app_container, "settings", None)
        return jsonify({
            "status": "success",
            "settings": settings_data,
            "runtime": {
                "copernicus_configured": bool(
                    getattr(runtime_settings, "copernicus_username", None)
                    and getattr(runtime_settings, "copernicus_password", None)
                ),
                "n2yo_configured": bool(getattr(runtime_settings, "n2yo_api_key", None)),
                "environment_owned": True,
            },
        }), 200
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500


@blueprint.route("/api/settings", methods=["POST", "PUT"])
def update_settings_api():
    """Update configuration settings."""
    app_container = container()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "status": "error",
            "error": "Request body must be a JSON object",
        }), 400

    try:
        app_container.update_settings.execute(payload)
        apply_result = _apply_scheduler_settings(app_container)
        restart_required = _restart_required_settings(payload)
        return jsonify({
            "status": "success",
            "apply_status": "PARTIAL" if apply_result["apply_errors"] else "APPLIED",
            "message": "Settings saved",
            "applied": apply_result["applied"],
            "apply_errors": apply_result["apply_errors"],
            "requires_restart": restart_required,
        }), 200
    except ValueError as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 400
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500


@blueprint.route("/api/settings/reset", methods=["POST"])
def reset_settings_api():
    """Reset configuration settings back to factory defaults."""
    app_container = container()
    payload = request.get_json(silent=True) or {}
    section = payload.get("section")
    try:
        app_container.reset_settings.execute(section=section)
        apply_result = _apply_scheduler_settings(app_container)
        restart_required: list[str] = []
        return jsonify({
            "status": "success",
            "apply_status": "PARTIAL" if apply_result["apply_errors"] else "APPLIED",
            "message": f"Settings for '{section}' reset to defaults" if section else "All settings reset to defaults",
            "applied": apply_result["applied"],
            "apply_errors": apply_result["apply_errors"],
            "requires_restart": restart_required,
        }), 200
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500
