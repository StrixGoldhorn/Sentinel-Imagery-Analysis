"""Scan and gallery HTTP routes."""

from pathlib import Path

from flask import Blueprint, jsonify, render_template, send_from_directory

from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    bounding_box,
    integer,
    json_object,
    optional_string,
    safe_folder_name,
)
from sentinel_analysis.interfaces.web.serialization import scan_image_url


blueprint = Blueprint("scans", __name__)


@blueprint.get("/")
def index():
    return render_template("index.html")


@blueprint.post("/scan")
def create_scan():
    payload = json_object()
    bbox = bounding_box(payload)
    scan = container().create_scan.execute(bbox)
    return jsonify(
        status="success",
        folderName=scan.folder_name,
        imageUrl=scan_image_url(scan, container().settings.output_root),
        bounds=[[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
        datetime=scan.acquisition.acquired_at.isoformat(),
    ), 201


@blueprint.post("/api/update_metadata/<folder_name>")
def update_metadata(folder_name: str):
    payload = json_object()
    container().rename_scan.execute(
        safe_folder_name(folder_name),
        optional_string(payload, "custom_name"),
    )
    return jsonify(status="success")


@blueprint.post("/api/run_cv/<folder_name>")
def run_cv(folder_name: str):
    payload = json_object()
    threshold = integer(payload, "threshold", 40)
    if not 0 <= threshold <= 255:
        raise RequestValidationError("threshold must be between 0 and 255")
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    dem_candidates = list(image_path.parent.glob("*_stitched_dem.png")) or list(image_path.parent.glob("*_dem.png"))
    result = container().detect_ships.execute(
        image_path,
        dem_candidates[0] if dem_candidates else None,
        threshold,
    )
    return jsonify(
        status="success",
        boxes=[(item.x, item.y, item.width, item.height) for item in result.detections],
        width=result.image_width,
        height=result.image_height,
    )


@blueprint.get("/api/scan/<folder_name>")
def get_scan(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    bbox = scan.bbox
    return jsonify(
        imageUrl=scan_image_url(scan, container().settings.output_root),
        bounds=[[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
        datetime=scan.acquisition.acquired_at.isoformat(),
        custom_name=scan.metadata.get("custom_name"),
    )


@blueprint.get("/media/scans/<path:filename>")
def scan_media(filename: str):
    if Path(filename).suffix.lower() != ".png":
        raise RequestValidationError("Only PNG scan imagery can be served")
    return send_from_directory(container().settings.output_root, filename, conditional=True)


@blueprint.get("/gallery")
def gallery():
    scans = [
        {
            "folder": scan.folder_name,
            "images": [scan_image_url(scan, container().settings.output_root)],
            "metadata": scan.metadata,
        }
        for scan in container().list_scans.execute()
    ]
    return render_template("gallery.html", scans=scans)
