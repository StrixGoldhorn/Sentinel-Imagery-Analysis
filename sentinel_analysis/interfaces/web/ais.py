"""AIS ingestion HTTP routes."""

from flask import Blueprint, jsonify

from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import bounding_box, json_object, optional_string


blueprint = Blueprint("ais", __name__)


@blueprint.post("/api/ingest_ais")
def ingest_ais():
    payload = json_object()
    bbox = bounding_box(payload)
    results = container().ingest_ais.execute(bbox, (None, None), optional_string(payload, "plugin"))
    return jsonify(status="success", results=results)
