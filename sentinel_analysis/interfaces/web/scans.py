"""Scan and gallery HTTP routes."""

import base64
import io
import json
from pathlib import Path
from typing import Any
import zipfile

import cv2
import numpy as np
from flask import Blueprint, Response, jsonify, render_template, request, send_file, send_from_directory
from PIL import Image

from sentinel_analysis.domain.entities import Scan
from sentinel_analysis.infrastructure.detection.detection_saver import save_detection_results
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    bounding_box,
    integer,
    json_object,
    optional_datetime,
    optional_string,
    safe_folder_name,
)
from sentinel_analysis.interfaces.web.serialization import scan_image_url


blueprint = Blueprint("scans", __name__)


def _get_setting(key: str, default: Any) -> Any:
    cnt = container()
    repo = getattr(cnt, "settings_repository", None)
    if repo is not None and hasattr(repo, "get"):
        return repo.get(key, default)
    return default


@blueprint.get("/")
def index():
    return render_template("index.html")


@blueprint.post("/scan")
def create_scan():
    payload = json_object()
    bbox = bounding_box(payload)
    start_date = (
        optional_datetime(payload, "start_datetime", is_end_of_day=False)
        or optional_datetime(payload, "start_date", time_field="start_time", is_end_of_day=False)
        or optional_datetime(payload, "date_from", time_field="time_from", is_end_of_day=False)
    )
    end_date = (
        optional_datetime(payload, "end_datetime", is_end_of_day=True)
        or optional_datetime(payload, "end_date", time_field="end_time", is_end_of_day=True)
        or optional_datetime(payload, "date_to", time_field="time_to", is_end_of_day=True)
    )
    if start_date is not None and end_date is not None and start_date > end_date:
        raise RequestValidationError("Start date/time cannot be after end date/time")
    days_ago = payload.get("days_ago")
    if days_ago is None:
        days_ago = _get_setting("search_window_days", 30)
    else:
        try:
            days_ago = int(days_ago)
        except (TypeError, ValueError):
            days_ago = 30
    aoi_name = optional_string(payload, "aoi_name")
    scan = container().create_scan.execute(
        bbox,
        days_ago=days_ago,
        aoi_name=aoi_name,
        start_date=start_date,
        end_date=end_date,
    )
    return jsonify(
        status="success",
        folderName=scan.folder_name,
        customName=scan.metadata.get("custom_name") or scan.folder_name,
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
    default_threshold = _get_setting("threshold", 40)
    threshold = integer(payload, "threshold", default_threshold)
    if not 0 <= threshold <= 255:
        raise RequestValidationError("threshold must be between 0 and 255")

    raw_buffer = payload.get("coastal_buffer")
    if raw_buffer is not None:
        try:
            coastal_buffer = int(raw_buffer)
            if coastal_buffer < 0:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("coastal_buffer must be a non-negative integer") from exc
    else:
        coastal_buffer = _get_setting("coastal_buffer_pixels", 81)

    raw_dist = payload.get("ais_correlation_distance")
    if raw_dist is not None:
        try:
            ais_distance = float(raw_dist)
            if not 0.0 <= ais_distance <= 5000.0:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("ais_correlation_distance must be a number between 0 and 5000 meters") from exc
    else:
        ais_distance = _get_setting("ais_correlation_distance_meters", 100.0)

    dem_enabled = payload.get("dem_land_mask_enabled")
    if dem_enabled is None:
        dem_enabled = _get_setting("dem_land_mask_enabled", True)
    else:
        dem_enabled = bool(dem_enabled)

    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    dem_candidates = list(image_path.parent.glob("*_stitched_dem.png")) or list(image_path.parent.glob("*_dem.png"))

    if dem_enabled and not dem_candidates:
        cnt = container()
        if hasattr(cnt, "generate_dem"):
            dem_target = image_path.parent / f"{scan.folder_name}_stitched_dem.png"
            if cnt.generate_dem.execute(scan.bbox, dem_target):
                dem_candidates = [dem_target]

    dem_path = dem_candidates[0] if (dem_enabled and dem_candidates) else None

    result = container().detect_ships.execute(
        image_path,
        dem_path,
        threshold,
        coastal_buffer=coastal_buffer,
    )

    cnt = container()
    enriched_detections = []
    ghost_vessels = []
    if hasattr(cnt, "correlate_ais_detections") and cnt.correlate_ais_detections is not None:
        try:
            raw_kinematics = payload.get("enable_kinematics")
            enable_kinematics = bool(raw_kinematics) if raw_kinematics is not None else None
            enriched = cnt.correlate_ais_detections.execute(
                result.detections,
                scan,
                result.image_width,
                result.image_height,
                tolerance_meters=ais_distance,
                enable_kinematics=enable_kinematics,
            )
            if isinstance(enriched, list) and (len(enriched) == len(result.detections) or not result.detections):
                enriched_detections = enriched
            ghost_vessels = getattr(cnt.correlate_ais_detections, "last_ghost_vessels", [])
        except Exception:
            pass

    if not enriched_detections and result.detections:
        for idx, item in enumerate(result.detections):
            enriched_detections.append({
                "index": idx,
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
                "polygon_points": getattr(item, "polygon_points", None),
                "correlation_status": "uncorrelated",
                "is_correlated": False,
                "is_dark_vessel": False,
                "dark_vessel_risk": "NOMINAL",
                "dark_vessel_score": 0.0,
                "correlated_ais": None,
            })

    inside_box_count = sum(1 for d in enriched_detections if d.get("correlation_status") == "inside_box")
    outside_box_count = sum(1 for d in enriched_detections if d.get("correlation_status") == "outside_box")
    uncorrelated_count = sum(1 for d in enriched_detections if d.get("correlation_status") == "uncorrelated")
    correlated_count = inside_box_count + outside_box_count

    saved_meta = {
        "threshold": threshold,
        "coastal_buffer": coastal_buffer,
        "land_masked": bool(dem_path is not None),
        "ais_correlation_distance": ais_distance,
    }
    saved_info = save_detection_results(
        image_path=image_path,
        detections=enriched_detections,
        image_width=result.image_width,
        image_height=result.image_height,
        metadata=saved_meta,
        bbox=scan.bbox,
        ghost_vessels=ghost_vessels,
    )

    scan_meta = dict(scan.metadata)
    scan_meta["latest_cv_results"] = {
        "detected_at": saved_info["timestamp"],
        "ship_count": saved_info["ship_count"],
        "correlated_count": saved_info["correlated_count"],
        "inside_box_count": saved_info["inside_box_count"],
        "outside_box_count": saved_info["outside_box_count"],
        "uncorrelated_count": saved_info["uncorrelated_count"],
        "dark_vessel_count": saved_info.get("dark_vessel_count", 0),
        "critical_dark_count": saved_info.get("critical_dark_count", 0),
        "ghost_vessel_count": saved_info.get("ghost_vessel_count", 0),
        "threshold": threshold,
        "coastal_buffer": coastal_buffer,
        "land_masked": bool(dem_path is not None),
        "detected_image": saved_info.get("detected_image_name"),
        "detections_json": saved_info.get("detections_json_name"),
    }

    if hasattr(cnt, "scan_repository") and hasattr(cnt.scan_repository, "save"):
        try:
            cnt.scan_repository.save(
                Scan(
                    folder_name=scan.folder_name,
                    bbox=scan.bbox,
                    acquisition=scan.acquisition,
                    image_path=scan.image_path,
                    metadata=scan_meta,
                )
            )
        except Exception:
            pass

    meta_file = image_path.parent.parent / "metadata.json"
    if meta_file.is_file():
        try:
            curr_data = json.loads(meta_file.read_text(encoding="utf-8"))
            curr_data["latest_cv_results"] = scan_meta["latest_cv_results"]
            meta_file.write_text(json.dumps(curr_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    return jsonify(
        status="success",
        land_masked=bool(dem_path is not None),
        coastal_buffer=coastal_buffer,
        ais_correlation_distance=ais_distance,
        boxes=[(item.x, item.y, item.width, item.height) for item in result.detections],
        detections=enriched_detections,
        correlated_count=correlated_count,
        inside_box_count=inside_box_count,
        outside_box_count=outside_box_count,
        uncorrelated_count=uncorrelated_count,
        dark_vessel_count=saved_info.get("dark_vessel_count", 0),
        critical_dark_count=saved_info.get("critical_dark_count", 0),
        ghost_vessel_count=saved_info.get("ghost_vessel_count", 0),
        ghost_vessels=ghost_vessels,
        width=result.image_width,
        height=result.image_height,
        saved_image=saved_info.get("detected_image_name"),
        saved_json=saved_info.get("detections_json_name"),
        detection_image_url=f"/api/scan/{folder_name}/detection_image",
        detections_url=f"/api/scan/{folder_name}/detections",
        geojson_url=f"/api/scan/{folder_name}/geojson",
        gis_bundle_url=f"/api/scan/{folder_name}/gis_bundle",
    )


@blueprint.get("/api/scan/<folder_name>/crop")
def get_detection_crop(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    bbox = request.args.get("bbox")
    if bbox:
        try:
            parts = [int(float(p.strip())) for p in bbox.split(",")]
            if len(parts) == 4:
                x, y, w, h = parts
            else:
                x, y, w, h = 0, 0, 50, 50
            padding = max(0, int(request.args.get("padding", 20)))
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("Coordinates and dimensions must be valid numbers") from exc
    else:
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

    # Encode crop to PNG
    _, buffer = cv2.imencode(".png", crop)

    if request.args.get("raw") in ("1", "true") or request.headers.get("Accept", "").startswith("image/"):
        return Response(buffer.tobytes(), mimetype="image/png")

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
    latest_cv = scan.metadata.get("latest_cv_results")
    if not latest_cv:
        det_json_path = Path(scan.image_path).parent / "detection_results.json"
        if det_json_path.is_file():
            try:
                data = json.loads(det_json_path.read_text(encoding="utf-8"))
                latest_cv = {
                    "detected_at": data.get("timestamp"),
                    "ship_count": data.get("ship_count", 0),
                    "correlated_count": data.get("correlated_count", 0),
                    "inside_box_count": data.get("inside_box_count", 0),
                    "outside_box_count": data.get("outside_box_count", 0),
                    "uncorrelated_count": data.get("uncorrelated_count", 0),
                    "dark_vessel_count": data.get("dark_vessel_count", 0),
                    "critical_dark_count": data.get("critical_dark_count", 0),
                    "ghost_vessel_count": data.get("ghost_vessel_count", 0),
                    "detected_image": data.get("detected_image"),
                    "detections_json": "detection_results.json",
                }
            except Exception:
                pass
    return jsonify(
        imageUrl=scan_image_url(scan, container().settings.output_root),
        bounds=[[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
        datetime=scan.acquisition.acquired_at.isoformat(),
        custom_name=scan.metadata.get("custom_name"),
        latest_cv_results=latest_cv,
    )


@blueprint.get("/api/scan/<folder_name>/detection_image")
def get_detection_image(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    candidates = [
        image_path.parent / f"{image_path.stem}_detected.png",
        image_path.parent / "detected_ships.png",
    ]
    for c in candidates:
        if c.is_file():
            return send_file(c, mimetype="image/png")
    return jsonify(error="Detection image not found"), 404


@blueprint.get("/api/scan/<folder_name>/detections")
def get_scan_detections(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    candidates = [
        image_path.parent / f"{image_path.stem}_detections.json",
        image_path.parent / "detection_results.json",
    ]
    for c in candidates:
        if c.is_file():
            try:
                data = json.loads(c.read_text(encoding="utf-8"))
                return jsonify(data)
            except Exception:
                pass
    return jsonify(error="Detection results not found"), 404


@blueprint.get("/api/scan/<folder_name>/geojson")
def get_scan_geojson(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    candidates = [
        image_path.parent / f"{image_path.stem}_detections.geojson",
        image_path.parent / "detections.geojson",
    ]
    for c in candidates:
        if c.is_file():
            as_att = request.args.get("download") in ("1", "true")
            return send_file(c, mimetype="application/geo+json", as_attachment=as_att, download_name=c.name)
    return jsonify(error="GeoJSON detections not found"), 404


@blueprint.get("/api/scan/<folder_name>/gis_bundle")
def get_scan_gis_bundle(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    folder_dir = image_path.parent

    extensions = [".pgw", ".prj", ".geojson", ".json"]
    files_to_pack: list[Path] = []

    for img_cand in [folder_dir / f"{image_path.stem}_detected.png", folder_dir / "detected_ships.png", image_path]:
        if img_cand.is_file():
            files_to_pack.append(img_cand)
            break

    for ext in extensions:
        for p in folder_dir.glob(f"*{ext}"):
            if p.is_file() and p not in files_to_pack:
                files_to_pack.append(p)

    if not files_to_pack:
        return jsonify(error="No GIS assets found for scan"), 404

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for f in files_to_pack:
            zip_file.write(f, arcname=f.name)

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{scan.folder_name}_gis_bundle.zip",
    )


@blueprint.delete("/api/scan/<folder_name>")
@blueprint.post("/api/scan/<folder_name>/delete")
def delete_scan(folder_name: str):
    container().delete_scan.execute(safe_folder_name(folder_name))
    return jsonify(status="success", message="Scan deleted successfully")


@blueprint.post("/api/scan/<folder_name>/generate_dem")
def generate_scan_dem(folder_name: str):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    dem_target = image_path.parent / f"{scan.folder_name}_stitched_dem.png"
    success = container().generate_dem.execute(scan.bbox, dem_target)
    if not success:
        return jsonify(status="error", message="Failed to generate DEM imagery"), 500
    return jsonify(status="success", dem_path=str(dem_target))



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
