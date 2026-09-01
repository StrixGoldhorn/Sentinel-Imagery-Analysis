"""HTTP routes for planned satellite pass schedules and automated AIS scrape forecasts."""

from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import RequestValidationError


blueprint = Blueprint("schedule", __name__)


@blueprint.get("/schedule")
def schedule_page():
    """Render the planned scrapes and pass schedule dashboard."""
    return render_template("schedule.html")


@blueprint.get("/api/schedule/upcoming")
def get_upcoming_scrapes():
    """Return aggregated, chronological list of upcoming satellite passes and scrape windows."""
    api_key = container().settings.n2yo_api_key or "default_key"

    auto_capture_only_raw = request.args.get("auto_capture_only", "false").strip().lower()
    auto_capture_only = auto_capture_only_raw in ("true", "1", "yes")

    aoi_id_raw = request.args.get("aoi_id")
    aoi_id = None
    if aoi_id_raw:
        try:
            aoi_id = int(aoi_id_raw)
            if aoi_id <= 0:
                raise ValueError()
        except ValueError as exc:
            raise RequestValidationError("aoi_id must be a positive integer") from exc

    days_ahead_raw = request.args.get("days_ahead", "14")
    try:
        days_ahead = max(1, min(int(days_ahead_raw), 30))
    except ValueError:
        days_ahead = 14

    result = container().get_upcoming_scrapes.execute(
        api_key=api_key,
        auto_capture_only=auto_capture_only,
        aoi_id=aoi_id,
        days_ahead=days_ahead,
    )

    return jsonify(status="success", **result)


@blueprint.get("/api/schedule/status")
def get_scheduler_status():
    """Return the health and polling state of the background pass scheduler daemon."""
    scheduler = getattr(container(), "pass_scheduler", None)
    if scheduler is not None and hasattr(scheduler, "get_status"):
        status_info = scheduler.get_status()
    else:
        status_info = {
            "is_running": False,
            "api_key_configured": bool(container().settings.n2yo_api_key),
            "poll_interval_seconds": 60.0,
            "last_run_at": None,
            "last_error": None,
            "last_results_count": 0,
            "thread_alive": False,
        }
    return jsonify(status="success", scheduler=status_info)


@blueprint.get("/api/schedule/logs")
def get_scraper_logs():
    """Return recent execution logs from the AIS scraper."""
    limit_raw = request.args.get("limit", "50")
    try:
        limit = max(1, min(int(limit_raw), 200))
    except ValueError:
        limit = 50

    ais_repo = getattr(container(), "ais_repository", None)
    logs = []
    if ais_repo is not None and hasattr(ais_repo, "get_scraper_logs"):
        logs = ais_repo.get_scraper_logs(limit=limit)

    return jsonify(status="success", count=len(logs), logs=logs)


@blueprint.post("/api/schedule/trigger_poll")
def trigger_schedule_poll():
    """Manually trigger an immediate scheduler poll and check cycle."""
    scheduler = getattr(container(), "pass_scheduler", None)
    api_key = container().settings.n2yo_api_key or "default_key"

    if scheduler is not None and hasattr(scheduler, "trigger_check"):
        try:
            results = scheduler.trigger_check()
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
    else:
        results = container().schedule_aois.execute(api_key)

    return jsonify(status="success", results=results)
