"""Area-of-interest HTTP routes."""

from flask import Blueprint, jsonify

from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import bounding_box, json_object, required_string
from sentinel_analysis.interfaces.web.serialization import serialize_aoi


blueprint = Blueprint("aois", __name__)


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
    api_key = container().settings.n2yo_api_key
    if not api_key:
        return jsonify(error="N2YO API key is not configured"), 503
    predictions = container().predict_aoi.execute(aoi_id, api_key)
    if not predictions:
        return jsonify(error="No upcoming scans found"), 404
    return jsonify(status="success", next_scan=predictions[0]["time"], predictions=predictions)
