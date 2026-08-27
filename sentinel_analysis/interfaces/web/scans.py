"""Scan and gallery HTTP routes."""

from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request
from werkzeug.utils import secure_filename

from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.exceptions import NoImageryFoundError, ScanNotFoundError, SentinelAnalysisError
from sentinel_analysis.interfaces.web.serialization import scan_image_url


blueprint = Blueprint("scans", __name__)


def _container() -> ApplicationContainer:
    return current_app.extensions["sentinel_container"]


@blueprint.get("/")
def index():
    return render_template("index.html")


@blueprint.post("/scan")
def create_scan():
    try:
        payload = request.get_json(silent=True) or {}
        bbox = BoundingBox.from_sequence(payload.get("bbox", []))
        scan = _container().create_scan.execute(bbox)
        return jsonify(
            status="success",
            folderName=scan.folder_name,
            imageUrl=scan_image_url(scan, _container()),
            bounds=[[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
            datetime=scan.acquisition.acquired_at.isoformat(),
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except NoImageryFoundError as exc:
        return jsonify(error=str(exc)), 404
    except SentinelAnalysisError as exc:
        return jsonify(error=str(exc)), 502
    except Exception:
        current_app.logger.exception("Scan creation failed")
        return jsonify(error="Scan creation failed"), 500


@blueprint.post("/api/update_metadata/<folder_name>")
def update_metadata(folder_name: str):
    name = secure_filename(folder_name)
    if not name:
        return jsonify(error="Invalid folder name"), 400
    try:
        payload = request.get_json(silent=True) or {}
        _container().rename_scan.execute(name, payload.get("custom_name"))
        return jsonify(status="success")
    except ScanNotFoundError as exc:
        return jsonify(error=str(exc)), 404
    except Exception:
        current_app.logger.exception("Metadata update failed")
        return jsonify(error="Metadata update failed"), 500


@blueprint.post("/api/run_cv/<folder_name>")
def run_cv(folder_name: str):
    name = secure_filename(folder_name)
    if not name:
        return jsonify(error="Invalid folder name"), 400
    try:
        payload = request.get_json(silent=True) or {}
        threshold = int(payload.get("threshold", 40))
        if not 0 <= threshold <= 255:
            raise ValueError("Threshold must be between 0 and 255")
        scan = _container().get_scan.execute(name)
        image_path = Path(scan.image_path)
        dem_candidates = list(image_path.parent.glob("*_stitched_dem.png")) or list(image_path.parent.glob("*_dem.png"))
        detections, width, height = _container().detect_ships.execute(
            image_path,
            dem_candidates[0] if dem_candidates else None,
            threshold,
        )
        return jsonify(
            status="success",
            boxes=[(item.x, item.y, item.width, item.height) for item in detections],
            width=width,
            height=height,
        )
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    except ScanNotFoundError as exc:
        return jsonify(error=str(exc)), 404
    except Exception:
        current_app.logger.exception("Ship detection failed")
        return jsonify(error="Ship detection failed"), 500


@blueprint.get("/api/scan/<folder_name>")
def get_scan(folder_name: str):
    name = secure_filename(folder_name)
    if not name:
        return jsonify(error="Invalid folder name"), 400
    try:
        scan = _container().get_scan.execute(name)
        bbox = scan.bbox
        return jsonify(
            imageUrl=scan_image_url(scan, _container()),
            bounds=[[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
            datetime=scan.acquisition.acquired_at.isoformat(),
            custom_name=scan.metadata.get("custom_name"),
        )
    except ScanNotFoundError as exc:
        return jsonify(error=str(exc)), 404


@blueprint.get("/gallery")
def gallery():
    scans = [
        {
            "folder": scan.folder_name,
            "images": [scan_image_url(scan, _container())],
            "metadata": scan.metadata,
        }
        for scan in _container().list_scans.execute()
    ]
    return render_template("gallery.html", scans=scans)


