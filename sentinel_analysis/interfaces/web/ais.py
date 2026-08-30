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


@blueprint.get("/api/ais/vessels")
def list_vessels():
    bbox_str = request.args.get("bbox")
    bbox = None
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
    time_range = None
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
    return jsonify(status="success", min_timestamp=None, max_timestamp=None, total_records=0)
