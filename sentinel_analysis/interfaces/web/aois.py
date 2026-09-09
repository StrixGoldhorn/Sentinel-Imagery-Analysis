from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, render_template, request
import uuid

from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    boolean,
    bounding_box,
    json_object,
    optional_datetime,
    optional_string,
    required_string,
)
from sentinel_analysis.interfaces.web.serialization import scan_image_url, serialize_aoi
from sentinel_analysis.application.results import summarize_ingestion_outcome


blueprint = Blueprint("aois", __name__)


@blueprint.get("/aois")
def aois_page():
    return render_template("aois.html")


@blueprint.get("/api/aoi")
def list_aois():
    aois = sorted(container().list_aois.execute(), key=lambda a: (a.name or "").lower())
    return jsonify([serialize_aoi(aoi) for aoi in aois])


@blueprint.post("/api/aoi")
def add_aoi():
    payload = json_object()
    bbox = bounding_box(payload)
    auto_capture = (
        boolean(payload, "auto_capture_enabled")
        if "auto_capture_enabled" in payload else None
    )
    aoi_id = container().add_aoi.execute(
        required_string(payload, "name"),
        bbox,
        auto_capture_enabled=auto_capture,
    )
    return jsonify(status="success", id=aoi_id), 201


@blueprint.delete("/api/aoi/<int:aoi_id>")
@blueprint.post("/api/aoi/<int:aoi_id>/delete")
def delete_aoi(aoi_id: int):
    cnt = container()
    pass_monitor = getattr(cnt, "pass_monitor", None)
    if pass_monitor is not None and hasattr(pass_monitor, "stop_for_aoi"):
        pass_monitor.stop_for_aoi(aoi_id)
    cnt.delete_aoi.execute(aoi_id)
    return jsonify(status="success", message=f"Area of interest #{aoi_id} deleted successfully")


@blueprint.post("/api/aoi/<int:aoi_id>/predict")
def predict_aoi(aoi_id: int):
    force_refresh = request.args.get("refresh", "").lower() in ("true", "1", "yes")
    custom_ttl = None

    if "cache_ttl" in request.args:
        try:
            custom_ttl = int(request.args["cache_ttl"])
        except ValueError:
            pass
    elif "ttl_hours" in request.args:
        try:
            custom_ttl = int(float(request.args["ttl_hours"]) * 3600)
        except ValueError:
            pass

    if request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            if body.get("refresh") or body.get("force_refresh"):
                force_refresh = True
            if "cache_ttl_seconds" in body:
                custom_ttl = int(body["cache_ttl_seconds"])
            elif "ttl_hours" in body:
                custom_ttl = int(float(body["ttl_hours"]) * 3600)
        except Exception:
            pass

    api_key = container().settings.n2yo_api_key or ""
    use_case = container().predict_aoi
    if hasattr(use_case, "execute_with_analysis"):
        result = use_case.execute_with_analysis(
            aoi_id,
            api_key,
            force_refresh=force_refresh,
            cache_ttl_seconds=custom_ttl,
        )

        predictions = result.get("predictions", [])
        n2yo_predictions = result.get("n2yo_predictions", [])
        historical_predictions = result.get("historical_predictions", [])
        next_scan = result.get("next_scan")
        mission_analysis = result.get("mission_analysis")
        cached = result.get("cached", False)
        fetched_at = result.get("fetched_at")
        expires_at = result.get("expires_at")
    else:
        predictions = use_case.execute(aoi_id, api_key)
        n2yo_predictions = [p for p in predictions if p.get("source") in ("N2YO", "COMBINED")]
        historical_predictions = [p for p in predictions if p.get("source") in ("HISTORICAL_MISSION", "COMBINED")]
        next_scan = predictions[0]["time"] if predictions else None
        mission_analysis = None
        cached = False
        fetched_at = None
        expires_at = None

    if not predictions and not n2yo_predictions and not historical_predictions:
        return jsonify(error="No upcoming scans found"), 404
    return jsonify(
        status="success",
        next_scan=next_scan,
        predictions=predictions,
        n2yo_predictions=n2yo_predictions,
        historical_predictions=historical_predictions,
        mission_analysis=mission_analysis,
        cached=cached,
        fetched_at=fetched_at,
        expires_at=expires_at,
    )




@blueprint.get("/api/aoi/<int:aoi_id>/mission_history")
def aoi_mission_history(aoi_id: int):
    result = container().analyze_mission_passes.execute(aoi_id)
    return jsonify(status="success", **result)


@blueprint.post("/api/aoi/<int:aoi_id>/auto_capture")
def toggle_auto_capture(aoi_id: int):
    payload = json_object()
    enabled = boolean(payload, "enabled", True)
    repo = container().aoi_repository
    if hasattr(repo, "update_auto_capture"):
        repo.update_auto_capture(aoi_id, enabled)
    if not enabled:
        monitor = getattr(container(), "pass_monitor", None)
        if monitor is not None and hasattr(monitor, "stop_for_aoi"):
            monitor.stop_for_aoi(aoi_id)
    return jsonify(status="success", auto_capture_enabled=enabled)


@blueprint.post("/api/aoi/<int:aoi_id>/scrape_ais")
def scrape_aoi_ais(aoi_id: int):
    payload = request.get_json(silent=True) or {}
    plugin = optional_string(payload, "plugin") if isinstance(payload, dict) else None
    pass_time_str = optional_string(payload, "pass_time") if isinstance(payload, dict) else None
    force_now = False
    if isinstance(payload, dict):
        force_now = bool(payload.get("force") or payload.get("force_now"))

    pass_time = None
    if pass_time_str and not force_now:
        try:
            pass_time = datetime.fromisoformat(pass_time_str.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RequestValidationError("Invalid pass_time format, must be ISO datetime") from exc

    results = container().scrape_aoi_ais.execute(
        aoi_id,
        plugin_name=plugin,
        pass_time=pass_time,
        force_now=force_now,
    )
    return jsonify(
        status="success",
        ingestion_outcome=summarize_ingestion_outcome(results),
        results=results,
    )


@blueprint.post("/api/aoi/<int:aoi_id>/force_ais_scan")
def force_ais_scan(aoi_id: int):
    payload = request.get_json(silent=True) or {}
    plugin = optional_string(payload, "plugin") if isinstance(payload, dict) else None
    results = container().scrape_aoi_ais.execute(
        aoi_id,
        plugin_name=plugin,
        force_now=True,
    )
    return jsonify(
        status="success",
        ingestion_outcome=summarize_ingestion_outcome(results),
        results=results,
        forced=True,
    )


@blueprint.post("/api/aoi/<int:aoi_id>/scan")
def scan_aoi(aoi_id: int):
    cnt = container()
    repo = getattr(cnt, "aoi_repository", None)
    aoi = repo.get(aoi_id) if repo and hasattr(repo, "get") else None
    if aoi is None:
        list_use_case = getattr(cnt, "list_aois", None)
        if list_use_case and hasattr(list_use_case, "execute"):
            for candidate in list_use_case.execute():
                if getattr(candidate, "id", None) == aoi_id:
                    aoi = candidate
                    break
    if aoi is None:
        return jsonify(error=f"Area of Interest {aoi_id} not found"), 404

    payload = request.get_json(silent=True) or {} if request.is_json else {}
    if not isinstance(payload, dict):
        payload = {}

    is_async = request.args.get("async", "").lower() in ("true", "1", "yes")
    if payload.get("async") or payload.get("is_async"):
        is_async = True

    now = datetime.now(timezone.utc)
    default_start = now - timedelta(days=15)
    default_end = now

    start_raw = (
        request.args.get("start_datetime")
        or request.args.get("start_date")
        or request.args.get("date_from")
        or payload.get("start_datetime")
        or payload.get("start_date")
        or payload.get("date_from")
    )
    start_time_raw = (
        request.args.get("start_time")
        or request.args.get("time_from")
        or payload.get("start_time")
        or payload.get("time_from")
    )

    end_raw = (
        request.args.get("end_datetime")
        or request.args.get("end_date")
        or request.args.get("date_to")
        or payload.get("end_datetime")
        or payload.get("end_date")
        or payload.get("date_to")
    )
    end_time_raw = (
        request.args.get("end_time")
        or request.args.get("time_to")
        or payload.get("end_time")
        or payload.get("time_to")
    )

    days_ago = None
    if "days_ago" in payload or "days_ago" in request.args:
        try:
            days_ago = int(payload.get("days_ago") or request.args.get("days_ago"))
        except (TypeError, ValueError):
            pass

    if start_raw is not None:
        start_date = optional_datetime(
            {"start_date": start_raw, "start_time": start_time_raw},
            "start_date",
            time_field="start_time",
            is_end_of_day=False,
        )
    elif days_ago is not None:
        start_date = now - timedelta(days=days_ago)
    else:
        start_date = default_start

    if end_raw is not None:
        end_date = optional_datetime(
            {"end_date": end_raw, "end_time": end_time_raw},
            "end_date",
            time_field="end_time",
            is_end_of_day=True,
        )
    else:
        end_date = default_end

    if start_date > end_date:
        raise RequestValidationError("Start date/time cannot be after end date/time")

    if is_async:
        queue = cnt.task_queue
        bbox = aoi.bbox
        aoi_name = aoi.name
        task_id = str(uuid.uuid4())

        def _run_scan() -> dict[str, object]:
            scan = cnt.create_scan.execute(
                bbox,
                aoi_name=aoi_name,
                start_date=start_date,
                end_date=end_date,
                progress_callback=lambda progress, message: queue.update_progress(task_id, progress, message),
            )
            return {
                "folderName": scan.folder_name,
                "customName": scan.metadata.get("custom_name") or scan.folder_name,
                "imageUrl": scan_image_url(scan, cnt.settings.output_root),
                "bounds": [[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
                "datetime": scan.acquisition.acquired_at.isoformat(),
                "aoi_id": aoi_id,
                "aoi_name": aoi_name,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }

        task = queue.submit("scan", task_id, _run_scan)
        return jsonify({
            "status": "success",
            "task_id": task.task_id,
            "task_status": task.status,
            "aoi_id": aoi.id,
            "aoi_name": aoi.name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }), 202

    scan = cnt.create_scan.execute(
        aoi.bbox,
        aoi_name=aoi.name,
        start_date=start_date,
        end_date=end_date,
    )
    return jsonify(
        status="success",
        aoi_id=aoi.id,
        aoi_name=aoi.name,
        folderName=scan.folder_name,
        customName=scan.metadata.get("custom_name") or scan.folder_name,
        imageUrl=scan_image_url(scan, cnt.settings.output_root),
        bounds=[[aoi.bbox.min_latitude, aoi.bbox.min_longitude], [aoi.bbox.max_latitude, aoi.bbox.max_longitude]],
        datetime=scan.acquisition.acquired_at.isoformat(),
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    ), 201
