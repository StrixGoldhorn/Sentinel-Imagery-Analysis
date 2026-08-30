"""AIS ingestion HTTP routes."""

from datetime import datetime
from flask import Blueprint, jsonify

from sentinel_analysis.application.use_cases.scrape_aoi_ais import calculate_pass_window
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
