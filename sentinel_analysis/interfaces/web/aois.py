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
from sentinel_analysis.interfaces.web.serialization import serialize_aoi


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
    if not force_refresh and request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            if body.get("refresh") or body.get("force_refresh"):
                force_refresh = True
        except Exception:
            pass

    api_key = container().settings.n2yo_api_key or "default_key"
    use_case = container().predict_aoi
    if hasattr(use_case, "execute_with_analysis"):
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
