"""Web routes and API endpoints for managing application and feature configuration."""

from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("settings", __name__)


def _apply_scheduler_settings(app_container) -> dict[str, list[str]]:
    """Apply the scheduler settings that support live reconfiguration."""
    applied: list[str] = []
    errors: list[str] = []
    scheduler = getattr(app_container, "pass_scheduler", None)
    if scheduler is None:
        return {"applied": applied, "apply_errors": ["Scheduler is not configured"]}

    try:
        aoi_val = app_container.settings_repository.get("aoi_check_interval_seconds")
        if aoi_val is not None:
            scheduler.set_aoi_check_interval(float(aoi_val))
            applied.append("scheduler.aoi_check_interval_seconds")
    except Exception as exc:
        errors.append(f"scheduler.aoi_check_interval_seconds: {exc}")

    try:
        sar_val = app_container.settings_repository.get("sar_scan_interval_seconds")
        if sar_val is None:
            sar_val = app_container.settings_repository.get("poll_interval_seconds")
        if sar_val is not None:
            scheduler.set_sar_scan_interval(float(sar_val))
            applied.append("scheduler.sar_scan_interval_seconds")
    except Exception as exc:
        errors.append(f"scheduler.sar_scan_interval_seconds: {exc}")

    return {"applied": applied, "apply_errors": errors}


def _restart_required_settings(payload: dict) -> list[str]:
    restart_keys = {"port", "debug", "database_path", "output_root", "cache_root"}
    system_settings = payload.get("system") if isinstance(payload.get("system"), dict) else {}
    return [f"system.{key}" for key in system_settings if key in restart_keys]


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
        return jsonify({
            "status": "success",
            "settings": settings_data,
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
        restart_required = (
            [f"system.{key}" for key in ("port", "debug", "database_path", "output_root", "cache_root")]
            if section in (None, "system") else []
        )
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
