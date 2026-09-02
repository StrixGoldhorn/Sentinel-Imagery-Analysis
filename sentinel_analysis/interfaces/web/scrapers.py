"""Web routes and API endpoints for managing AIS scrapers and viewing execution logs."""

from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("scrapers", __name__)


@blueprint.route("/scrapers", methods=["GET"])
def scrapers_dashboard() -> str:
    """Render the Scrapers management & toggle UI dashboard."""
    return render_template("scrapers.html")


@blueprint.route("/api/scrapers", methods=["GET"])
def list_scrapers_api():
    """Returns all registered scrapers, their enablement status, descriptions, and performance stats."""
    app_container = container()
    try:
        result = app_container.list_scrapers.execute()

        return jsonify({
            "status": "success",
            "scrapers": result["scrapers"],
            "metrics": result["metrics"],
        }), 200
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500


@blueprint.route("/api/scrapers/<name>", methods=["GET"])
def get_scraper_api(name: str):
    """Retrieve full detail, configuration, and runtime stats for an individual scraper."""
    app_container = container()
    try:
        scraper = app_container.get_scraper_detail.execute(name)
        return jsonify({
            "status": "success",
            "scraper": scraper,
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


@blueprint.route("/api/scrapers/<name>", methods=["PUT", "PATCH"])
def update_scraper_api(name: str):
    """Update custom settings, description, and enablement status for an individual scraper."""
    app_container = container()
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled") if "enabled" in data else None
    description = data.get("description") if "description" in data else None
    config = data.get("config") if "config" in data else None

    try:
        scraper = app_container.update_scraper.execute(
            plugin_name=name,
            enabled=enabled,
            description=description,
            config=config,
        )
        return jsonify({
            "status": "success",
            "scraper": scraper,
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


@blueprint.route("/api/scrapers/<name>/toggle", methods=["POST"])
def toggle_scraper_api(name: str):
    """Toggle the enabled status of an AIS scraper."""
    app_container = container()
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled", True)

    try:
        result = app_container.toggle_scraper.execute(name, enabled)
        return jsonify({
            "status": "success",
            "plugin_name": result["plugin_name"],
            "enabled": result["enabled"],
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


@blueprint.route("/api/scrapers/<name>/test", methods=["POST"])
def test_scraper_api(name: str):
    """Execute an on-demand test scrape using a single scraper."""
    app_container = container()
    data = request.get_json(silent=True) or {}
    bbox_raw = data.get("bbox", [103.8, 1.2, 103.9, 1.3])

    try:
        if isinstance(bbox_raw, list) and len(bbox_raw) == 4:
            bbox = BoundingBox(bbox_raw[0], bbox_raw[1], bbox_raw[2], bbox_raw[3])
        else:
            bbox = BoundingBox(103.8, 1.2, 103.9, 1.3)

        result = app_container.ingest_ais.execute(
            bbox=bbox,
            time_range=(None, None),
            plugin_name=name,
        )
        total_inserted = result.get("total_inserted", 0) if isinstance(result, dict) else getattr(result, "total_inserted", 0)
        logs = result.get("logs", []) if isinstance(result, dict) else getattr(result, "logs", [])

        return jsonify({
            "status": "success",
            "plugin_name": name,
            "total_inserted": total_inserted,
            "logs": logs,
        }), 200
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500


@blueprint.route("/api/scrapers/<name>/config", methods=["POST"])
def update_scraper_config_api(name: str):
    """Update custom proxy/credential configuration for a specific scraper."""
    app_container = container()
    data = request.get_json(silent=True) or {}
    try:
        result = app_container.update_scraper_config.execute(name, data)
        return jsonify({
            "status": "success",
            "plugin_name": result["plugin_name"],
            "config": result["config"],
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


@blueprint.route("/api/scrapers/<name>/reset_cooldown", methods=["POST"])
def reset_scraper_cooldown_api(name: str):
    """Manually clear active rate limit backoff cooldown for a scraper."""
    app_container = container()
    try:
        result = app_container.reset_scraper_cooldown.execute(name)
        return jsonify({
            "status": "success",
            "plugin_name": result["plugin_name"],
            "status_text": result["status"],
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


@blueprint.route("/logs", methods=["GET"])
def logs_dashboard() -> str:
    """Render the dedicated Scraper Logs audit UI."""
    return render_template("logs.html")


@blueprint.route("/api/logs", methods=["GET"])
def scraper_logs_api():
    """Query paginated scraper execution logs with plugin and status filtering."""
    app_container = container()
    plugin_name = request.args.get("plugin")
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    try:
        result = app_container.get_scraper_logs_use_case.execute(
            plugin_name=plugin_name,
            status=status,
            limit=limit,
            offset=offset,
        )
        return jsonify({
            "status": "success",
            "logs": result["logs"],
            "count": result["count"],
            "stats": result["stats"],
            "metrics": result["metrics"],
        }), 200
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500

