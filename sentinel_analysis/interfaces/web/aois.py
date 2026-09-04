from datetime import datetime
from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    boolean,
    bounding_box,
    json_object,
    optional_string,
    required_string,
)
from sentinel_analysis.interfaces.web.serialization import scan_image_url, serialize_aoi


blueprint = Blueprint("aois", __name__)


@blueprint.get("/aois")
def aois_page():
    return render_template("aois.html")


@blueprint.get("/api/aoi")
def list_aois():
    return jsonify([serialize_aoi(aoi) for aoi in container().list_aois.execute()])


@blueprint.post("/api/aoi")
def add_aoi():
    payload = json_object()
    bbox = bounding_box(payload)
    aoi_id = container().add_aoi.execute(required_string(payload, "name"), bbox)
    return jsonify(status="success", id=aoi_id), 201


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

    api_key = container().settings.n2yo_api_key or "default_key"
    use_case = container().predict_aoi
    if hasattr(use_case, "execute_with_analysis"):
        try:
            result = use_case.execute_with_analysis(
                aoi_id,
                api_key,
                force_refresh=force_refresh,
                cache_ttl_seconds=custom_ttl,
            )
        except TypeError:
            try:
                result = use_case.execute_with_analysis(aoi_id, api_key, force_refresh=force_refresh)
            except TypeError:
                result = use_case.execute_with_analysis(aoi_id, api_key)

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
    return jsonify(status="success", results=results)


@blueprint.post("/api/aoi/<int:aoi_id>/force_ais_scan")
def force_ais_scan(aoi_id: int):
    payload = request.get_json(silent=True) or {}
    plugin = optional_string(payload, "plugin") if isinstance(payload, dict) else None
    results = container().scrape_aoi_ais.execute(
        aoi_id,
        plugin_name=plugin,
        force_now=True,
    )
    return jsonify(status="success", results=results, forced=True)


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

    is_async = request.args.get("async", "").lower() in ("true", "1", "yes")
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict) and (payload.get("async") or payload.get("is_async")):
            is_async = True

    if is_async:
        queue = cnt.task_queue
        bbox = aoi.bbox
        aoi_name = aoi.name

        def _run_scan() -> dict[str, object]:
            scan = cnt.create_scan.execute(bbox, aoi_name=aoi_name)
            return {
                "folderName": scan.folder_name,
                "customName": scan.metadata.get("custom_name") or scan.folder_name,
                "imageUrl": scan_image_url(scan, cnt.settings.output_root),
                "bounds": [[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
                "datetime": scan.acquisition.acquired_at.isoformat(),
                "aoi_id": aoi_id,
                "aoi_name": aoi_name,
            }

        task = queue.submit("scan", None, _run_scan)
        return jsonify({
            "status": "success",
            "task_id": task.task_id,
            "task_status": task.status,
            "aoi_id": aoi.id,
            "aoi_name": aoi.name,
        }), 202

    scan = cnt.create_scan.execute(aoi.bbox, aoi_name=aoi.name)
    return jsonify(
        status="success",
        aoi_id=aoi.id,
        aoi_name=aoi.name,
        folderName=scan.folder_name,
        customName=scan.metadata.get("custom_name") or scan.folder_name,
        imageUrl=scan_image_url(scan, cnt.settings.output_root),
        bounds=[[aoi.bbox.min_latitude, aoi.bbox.min_longitude], [aoi.bbox.max_latitude, aoi.bbox.max_longitude]],
        datetime=scan.acquisition.acquired_at.isoformat(),
    ), 201
