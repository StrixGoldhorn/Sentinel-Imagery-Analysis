"""Web routes and API endpoints for managing application and feature configuration."""

from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("settings", __name__)


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
        if hasattr(app_container, "pass_scheduler") and app_container.pass_scheduler is not None:
            updated_interval = app_container.settings_repository.get("poll_interval_seconds")
            if updated_interval is not None:
                try:
                    app_container.pass_scheduler.set_poll_interval(float(updated_interval))
                except Exception:
                    pass
        return jsonify({
            "status": "success",
            "message": "Settings updated successfully",
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
        if hasattr(app_container, "pass_scheduler") and app_container.pass_scheduler is not None:
            updated_interval = app_container.settings_repository.get("poll_interval_seconds")
            if updated_interval is not None:
                try:
                    app_container.pass_scheduler.set_poll_interval(float(updated_interval))
                except Exception:
                    pass
        return jsonify({
            "status": "success",
            "message": f"Settings for '{section}' reset to defaults" if section else "All settings reset to defaults",
        }), 200
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500
