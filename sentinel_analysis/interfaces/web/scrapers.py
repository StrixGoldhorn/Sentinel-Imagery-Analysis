"""Web routes and API endpoints for managing AIS scrapers and viewing execution logs."""

from flask import Blueprint, current_app, jsonify, render_template, request

from sentinel_analysis.domain.entities import BoundingBox

blueprint = Blueprint("scrapers", __name__)


@blueprint.route("/scrapers", methods=["GET"])
def scrapers_dashboard() -> str:
    """Render the Scrapers management & toggle UI dashboard."""
    return render_template("scrapers.html")


@blueprint.route("/api/scrapers", methods=["GET"])
def list_scrapers_api():
    """Returns all registered scrapers, their enablement status, descriptions, and performance stats."""
    container = current_app.config["CONTAINER"]
    try:
        result = container.list_scrapers.execute()
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


@blueprint.route("/api/scrapers/<name>/toggle", methods=["POST"])
def toggle_scraper_api(name: str):
    """Toggle the enabled status of an AIS scraper."""
    container = current_app.config["CONTAINER"]
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled", True)

    try:
        result = container.toggle_scraper.execute(name, enabled)
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
    container = current_app.config["CONTAINER"]
    data = request.get_json(silent=True) or {}
    bbox_raw = data.get("bbox", [103.8, 1.2, 103.9, 1.3])

    try:
        if isinstance(bbox_raw, list) and len(bbox_raw) == 4:
            bbox = BoundingBox(bbox_raw[0], bbox_raw[1], bbox_raw[2], bbox_raw[3])
        else:
            bbox = BoundingBox(103.8, 1.2, 103.9, 1.3)

        result = container.ingest_ais.execute(
            bbox=bbox,
            time_range=(None, None),
            plugin_name=name,
        )
        return jsonify({
            "status": "success",
            "plugin_name": name,
            "total_inserted": result.total_inserted,
            "logs": result.logs,
        }), 200
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
    container = current_app.config["CONTAINER"]
    plugin_name = request.args.get("plugin")
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    try:
        result = container.get_scraper_logs_use_case.execute(
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
