"""AIS ingestion HTTP routes."""

from flask import Blueprint, current_app, jsonify, request

from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import BoundingBox


blueprint = Blueprint("ais", __name__)


def _container() -> ApplicationContainer:
    return current_app.extensions["sentinel_container"]


@blueprint.post("/api/ingest_ais")
def ingest_ais():
    try:
        payload = request.get_json(silent=True) or {}
        bbox = BoundingBox.from_sequence(payload.get("bbox", []))
        results = _container().ingest_ais.execute(bbox, (None, None), payload.get("plugin"))
        return jsonify(status="success", results=results)
    except (TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        current_app.logger.exception("AIS ingestion failed")
        return jsonify(error="AIS ingestion failed"), 500

