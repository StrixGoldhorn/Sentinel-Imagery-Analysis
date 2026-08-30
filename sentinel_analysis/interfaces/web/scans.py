"""Scan and gallery HTTP routes."""

import base64
import io
from pathlib import Path

import cv2
import numpy as np
from flask import Blueprint, jsonify, render_template, request, send_from_directory
from PIL import Image

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
        detections=[
            {
                "x": item.x,
                "y": item.y,
                "width": item.width,
                "height": item.height,
                "confidence": item.confidence,
                "angle": item.angle,
                "length": item.length,
                "beam": item.beam,
                "center_x": item.center_x,
                "center_y": item.center_y,
                "polygon_points": item.polygon_points,
            }
            for item in result.detections
        ],
        width=result.image_width,
        height=result.image_height,
    )


@blueprint.get("/api/scan/<folder_name>/crop")
def get_detection_crop(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    try:
        x = int(request.args.get("x", 0))
        y = int(request.args.get("y", 0))
        w = int(request.args.get("width", 50))
        h = int(request.args.get("height", 50))
        padding = max(0, int(request.args.get("padding", 20)))
    except (TypeError, ValueError) as exc:
        raise RequestValidationError("Coordinates and dimensions must be valid integers") from exc

    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise RequestValidationError("Crop dimensions must be positive non-negative integers")

    image_path = Path(scan.image_path)
    if not image_path.is_file():
        raise RequestValidationError(f"Scan image not found: {image_path}")

    img = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RequestValidationError("Unable to load scan image for cropping")

    img_h, img_w = img.shape[:2]
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(img_w, x + w + padding)
    y2 = min(img_h, y + h + padding)

    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        raise RequestValidationError("Specified crop bounding box is outside image boundaries")

    # Encode crop to PNG data URI
    _, buffer = cv2.imencode(".png", crop)
    encoded = base64.b64encode(buffer).decode("utf-8")
    data_uri = f"data:image/png;base64,{encoded}"

    # Calculate intensity distribution profile
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten().tolist()
    mean_intensity = float(np.mean(gray))
    max_intensity = float(np.max(gray))
    min_intensity = float(np.min(gray))

    return jsonify({
        "data_uri": data_uri,
        "crop_width": crop.shape[1],
        "crop_height": crop.shape[0],
        "stats": {
            "mean_intensity": round(mean_intensity, 2),
            "max_intensity": round(max_intensity, 2),
            "min_intensity": round(min_intensity, 2),
            "histogram": [round(float(v), 1) for v in hist],
        },
    })


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
