"""Area-of-interest HTTP routes."""

from flask import Blueprint, current_app, jsonify, request

from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.exceptions import ExternalServiceError
from sentinel_analysis.interfaces.web.serialization import serialize_aoi


blueprint = Blueprint("aois", __name__)


def _container() -> ApplicationContainer:
    return current_app.extensions["sentinel_container"]


@blueprint.get("/api/aoi")
def list_aois():
    return jsonify([serialize_aoi(aoi) for aoi in _container().list_aois.execute()])


@blueprint.post("/api/aoi")
def add_aoi():
    try:
        payload = request.get_json(silent=True) or {}
        bbox = BoundingBox.from_sequence(payload.get("bbox", []))
        aoi_id = _container().add_aoi.execute(str(payload.get("name", "")), bbox)
        return jsonify(status="success", id=aoi_id)
    except (TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400


@blueprint.post("/api/aoi/<int:aoi_id>/predict")
def predict_aoi(aoi_id: int):
    api_key = _container().settings.n2yo_api_key
    if not api_key:
        return jsonify(error="N2YO API key missing"), 400
    try:
        predictions = _container().predict_aoi.execute(aoi_id, api_key)
        if not predictions:
            return jsonify(error="No upcoming scans found"), 404
        return jsonify(status="success", next_scan=predictions[0]["time"], predictions=predictions)
    except LookupError as exc:
        return jsonify(error=str(exc)), 404
    except ExternalServiceError as exc:
        return jsonify(error=str(exc)), 502

