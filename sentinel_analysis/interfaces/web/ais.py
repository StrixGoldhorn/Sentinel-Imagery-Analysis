from datetime import datetime
from flask import Blueprint, jsonify, request

from sentinel_analysis.application.use_cases.scrape_aoi_ais import calculate_pass_window
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    bounding_box,
    json_object,
    optional_string,
)


blueprint = Blueprint("ais", __name__)


@blueprint.post("/api/ingest_ais")
def ingest_ais():
    payload = json_object()
    bbox = bounding_box(payload)
    plugin = optional_string(payload, "plugin")

    pass_time_str = optional_string(payload, "pass_time")
    if pass_time_str:
        try:
            pass_time = datetime.fromisoformat(pass_time_str.replace("Z", "+00:00"))
            time_range = calculate_pass_window(pass_time, window_minutes=5)
        except ValueError as exc:
            raise RequestValidationError("Invalid pass_time format, must be ISO datetime") from exc
    else:
        time_range = (None, None)

    results = container().ingest_ais.execute(bbox, time_range, plugin)
    return jsonify(status="success", results=results)


@blueprint.route("/api/ais/vessels", methods=["GET", "POST"])
def list_vessels():
    bbox = None
    time_range = None
    latest_only = True
    limit = 500

    if request.method == "POST" or (request.is_json and request.get_json(silent=True)):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise RequestValidationError("JSON request body must be an object")

        bbox_raw = payload.get("bbox")
        if bbox_raw is not None:
            if isinstance(bbox_raw, (list, tuple)):
                if len(bbox_raw) != 4:
                    raise RequestValidationError("bbox must contain four coordinates [min_lon, min_lat, max_lon, max_lat]")
                try:
                    coords = [float(c) for c in bbox_raw]
                    bbox = BoundingBox.from_sequence(coords)
                except Exception as exc:
                    raise RequestValidationError(f"Invalid bbox coordinates: {exc}") from exc
            elif isinstance(bbox_raw, str):
                try:
                    coords = [float(c.strip()) for c in bbox_raw.split(",")]
                    if len(coords) != 4:
                        raise ValueError("bbox must have 4 coordinates")
                    bbox = BoundingBox.from_sequence(coords)
                except Exception as exc:
                    raise RequestValidationError("Invalid bbox string, expected min_lon,min_lat,max_lon,max_lat") from exc
            elif isinstance(bbox_raw, dict):
                try:
                    min_lon = bbox_raw.get("min_longitude") if "min_longitude" in bbox_raw else bbox_raw.get("min_lon")
                    min_lat = bbox_raw.get("min_latitude") if "min_latitude" in bbox_raw else bbox_raw.get("min_lat")
                    max_lon = bbox_raw.get("max_longitude") if "max_longitude" in bbox_raw else bbox_raw.get("max_lon")
                    max_lat = bbox_raw.get("max_latitude") if "max_latitude" in bbox_raw else bbox_raw.get("max_lat")
                    bbox = BoundingBox(float(min_lon), float(min_lat), float(max_lon), float(max_lat))
                except Exception as exc:
                    raise RequestValidationError(f"Invalid bbox object format: {exc}") from exc
            else:
                raise RequestValidationError("Invalid bbox format, expected array of four coordinates")

        start_str = payload.get("start")
        end_str = payload.get("end")
        if start_str or end_str:
            try:
                start = datetime.fromisoformat(start_str.replace("Z", "+00:00")) if start_str else None
                end = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else None
                time_range = (start, end)
            except ValueError as exc:
                raise RequestValidationError("Invalid datetime format in start/end") from exc

        if "latest_only" in payload:
            val = payload["latest_only"]
            if isinstance(val, bool):
                latest_only = val
            elif isinstance(val, str):
                latest_only = val.strip().lower() in ("true", "1", "yes")
            else:
                latest_only = bool(val)

        if "limit" in payload:
            try:
                limit = int(payload["limit"])
            except (TypeError, ValueError):
                limit = 500
    else:
        bbox_str = request.args.get("bbox")
        if bbox_str:
            try:
                coords = [float(c.strip()) for c in bbox_str.split(",")]
                if len(coords) != 4:
                    raise ValueError("bbox must have 4 coordinates")
                bbox = BoundingBox.from_sequence(coords)
            except Exception as exc:
                raise RequestValidationError("Invalid bbox query parameter, expected min_lon,min_lat,max_lon,max_lat") from exc

        start_str = request.args.get("start")
        end_str = request.args.get("end")
        if start_str or end_str:
            try:
                start = datetime.fromisoformat(start_str.replace("Z", "+00:00")) if start_str else None
                end = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else None
                time_range = (start, end)
            except ValueError as exc:
                raise RequestValidationError("Invalid datetime format in start/end query parameter") from exc

        latest_only_str = request.args.get("latest_only", "true").strip().lower()
        latest_only = latest_only_str in ("true", "1", "yes")

        limit_str = request.args.get("limit", "500")
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 500

    vessels = container().get_vessels.execute(
        bbox=bbox,
        time_range=time_range,
        limit=limit,
        latest_only=latest_only,
    )
    return jsonify(status="success", count=len(vessels), vessels=vessels)


@blueprint.get("/api/ais/timeline")
def get_ais_timeline():
    repo = getattr(container(), "ais_repository", None)
    if repo is not None and hasattr(repo, "get_timeline_bounds"):
        bounds = repo.get_timeline_bounds()
        return jsonify(status="success", **bounds)
    return jsonify(status="success", min_timestamp=None, max_timestamp=None, total_records=0, count=0)


@blueprint.get("/api/ais/vessels/<int:vessel_id>")
def get_vessel(vessel_id: int):
    vessel = container().get_vessel_details.execute(vessel_id)
    return jsonify(status="success", vessel=vessel)


@blueprint.route("/api/ais/vessels/<int:vessel_id>", methods=["PUT", "PATCH", "POST"])
def update_vessel(vessel_id: int):
    payload = request.get_json(silent=True) or {}
    name = payload.get("name") if "name" in payload else payload.get("vessel_name")
    vessel_type = payload.get("type") if "type" in payload else payload.get("vessel_type")
    callsign = payload.get("callsign")
    imo = payload.get("imo")

    updated = container().update_vessel_details.execute(
        vessel_id=vessel_id,
        name=name,
        vessel_type=vessel_type,
        callsign=callsign,
        imo=imo,
    )
    return jsonify(status="success", vessel=updated)
