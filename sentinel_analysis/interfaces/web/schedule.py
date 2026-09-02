"""HTTP routes for planned satellite pass schedules and automated AIS scrape forecasts."""

from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import RequestValidationError, json_object


blueprint = Blueprint("schedule", __name__)


@blueprint.get("/schedule")
def schedule_page():
    """Render the planned scrapes and pass schedule dashboard."""
    return render_template("schedule.html")


@blueprint.get("/post-pass")
@blueprint.get("/post_pass")
@blueprint.get("/ingestion")
def post_pass_page():
    """Render the autonomous post-pass imagery ingestion pipeline dashboard."""
    return render_template("post_pass.html")


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


@blueprint.get("/api/schedule/post_pass_jobs")
def get_post_pass_jobs():
    """Return recent post-pass imagery ingestion jobs."""
    limit_raw = request.args.get("limit", "50")
    try:
        limit = max(1, min(int(limit_raw), 200))
    except ValueError:
        limit = 50

    repo = getattr(container(), "post_pass_repository", None)
    if repo is None:
        return jsonify(status="success", jobs=[], count=0)

    jobs = repo.list(limit=limit)
    status_filter = request.args.get("status")
    if status_filter:
        jobs = [j for j in jobs if j.status.upper() == status_filter.strip().upper()]

    def _job_dict(job):
        return {
            "id": job.id,
            "aoi_id": job.aoi_id,
            "aoi_name": job.aoi_name or f"AOI #{job.aoi_id}",
            "pass_time": job.pass_time.isoformat() if job.pass_time else None,
            "expected_imagery_time": job.expected_imagery_time.isoformat() if job.expected_imagery_time else (job.pass_time.isoformat() if job.pass_time else None),
            "satellite": job.satellite,
            "orbit_direction": job.orbit_direction,
            "status": job.status,
            "attempts": job.attempts,
            "last_polled_at": job.last_polled_at.isoformat() if job.last_polled_at else None,
            "next_poll_at": job.next_poll_at.isoformat() if job.next_poll_at else None,
            "scan_folder": job.scan_folder,
            "error_message": job.error_message,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    return jsonify(
        status="success",
        count=len(jobs),
        jobs=[_job_dict(j) for j in jobs],
    )


@blueprint.post("/api/schedule/post_pass_jobs")
def create_custom_post_pass_job():
    """Create a custom post-pass imagery ingestion job for a specified AOI."""
    payload = json_object()
    aoi_id_raw = payload.get("aoi_id")
    if aoi_id_raw is None:
        raise RequestValidationError("aoi_id is required")
    try:
        aoi_id = int(aoi_id_raw)
        if aoi_id <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise RequestValidationError("aoi_id must be a positive integer")

    aoi_repo = getattr(container(), "aoi_repository", None)
    if aoi_repo is None:
        raise RequestValidationError("AOI repository not configured")
    aoi = aoi_repo.get(aoi_id)
    if aoi is None:
        raise RequestValidationError(f"Area of interest #{aoi_id} not found")

    pass_time_raw = payload.get("pass_time")
    if not pass_time_raw:
        raise RequestValidationError("pass_time is required")
    try:
        pass_time = datetime.fromisoformat(str(pass_time_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as exc:
        raise RequestValidationError(f"Invalid pass_time format: {exc}")

    expected_imagery_time_raw = payload.get("expected_imagery_time")
    if expected_imagery_time_raw:
        try:
            expected_imagery_time = datetime.fromisoformat(str(expected_imagery_time_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception as exc:
            raise RequestValidationError(f"Invalid expected_imagery_time format: {exc}")
    else:
        expected_imagery_time = pass_time

    satellite = payload.get("satellite") or "Sentinel-1"
    orbit_direction = payload.get("orbit_direction") or None
    poll_immediately = bool(payload.get("poll_immediately", False))

    now = datetime.now(timezone.utc)
    is_completed = (expected_imagery_time + timedelta(minutes=5)) <= now
    if poll_immediately or is_completed:
        status = "POLLING_CATALOG"
        next_poll_at = now
    else:
        status = "PENDING_PASS"
        next_poll_at = expected_imagery_time + timedelta(minutes=5)

    repo = getattr(container(), "post_pass_repository", None)
    if repo is None:
        raise RequestValidationError("Post-pass repository not configured")

    from sentinel_analysis.domain.entities import PostPassIngestionJob

    job = PostPassIngestionJob(
        aoi_id=aoi_id,
        pass_time=pass_time,
        satellite=satellite,
        orbit_direction=orbit_direction,
        status=status,
        attempts=0,
        next_poll_at=next_poll_at,
        created_at=now,
        aoi_name=aoi.name,
        expected_imagery_time=expected_imagery_time,
    )

    job_id = repo.add(job)

    results = []
    if poll_immediately:
        ingest_use_case = getattr(container(), "ingest_post_pass", None)
        if ingest_use_case is not None:
            try:
                results = ingest_use_case.execute(job_id=job_id)
            except Exception:
                pass

    return jsonify(
        status="success",
        job_id=job_id,
        message=f"Post-pass job #{job_id} for '{aoi.name}' queued successfully",
        results=results,
    ), 201


@blueprint.post("/api/schedule/post_pass_jobs/<int:job_id>/poll")
def poll_post_pass_job(job_id: int):
    """Trigger an immediate catalog check for a specific post-pass ingestion job."""
    ingest_use_case = getattr(container(), "ingest_post_pass", None)
    if ingest_use_case is None:
        raise RequestValidationError("Post-pass ingestion use case not configured")

    try:
        results = ingest_use_case.execute(job_id=job_id)
        return jsonify(status="success", results=results)
    except Exception as exc:
        return jsonify(status="error", error=str(exc)), 500


@blueprint.post("/api/schedule/post_pass_jobs/<int:job_id>/retry")
def retry_post_pass_job(job_id: int):
    """Reset a failed or timed-out job to POLLING_CATALOG and immediately run check."""
    repo = getattr(container(), "post_pass_repository", None)
    if repo is None:
        raise RequestValidationError("Post-pass repository not configured")

    job = repo.get(job_id)
    if job is None:
        raise RequestValidationError(f"Post-pass job #{job_id} not found")

    from datetime import datetime, timezone
    from sentinel_analysis.domain.entities import PostPassIngestionJob

    reset_job = PostPassIngestionJob(
        id=job.id,
        aoi_id=job.aoi_id,
        pass_time=job.pass_time,
        satellite=job.satellite,
        orbit_direction=job.orbit_direction,
        status="POLLING_CATALOG",
        attempts=0,
        last_polled_at=None,
        next_poll_at=None,
        scan_folder=None,
        error_message=None,
        created_at=job.created_at or datetime.now(timezone.utc),
        completed_at=None,
        aoi_name=job.aoi_name,
        expected_imagery_time=job.expected_imagery_time or job.pass_time,
    )
    repo.update(reset_job)

    ingest_use_case = getattr(container(), "ingest_post_pass", None)
    results = []
    if ingest_use_case is not None:
        try:
            results = ingest_use_case.execute(job_id=job_id)
        except Exception:
            pass

    return jsonify(status="success", message="Job reset to polling", results=results)


@blueprint.delete("/api/schedule/post_pass_jobs/<int:job_id>")
def delete_post_pass_job(job_id: int):
    """Delete a post-pass ingestion job."""
    repo = getattr(container(), "post_pass_repository", None)
    if repo is None:
        raise RequestValidationError("Post-pass repository not configured")

    repo.delete(job_id)
    return jsonify(status="success", message=f"Job #{job_id} deleted successfully")


@blueprint.post("/api/schedule/post_pass_jobs/poll_all")
def poll_all_post_pass_jobs():
    """Trigger an immediate catalog check for all due post-pass ingestion jobs."""
    ingest_use_case = getattr(container(), "ingest_post_pass", None)
    if ingest_use_case is None:
        raise RequestValidationError("Post-pass ingestion use case not configured")

    try:
        results = ingest_use_case.execute()
        return jsonify(status="success", count=len(results), results=results)
    except Exception as exc:
        return jsonify(status="error", error=str(exc)), 500


