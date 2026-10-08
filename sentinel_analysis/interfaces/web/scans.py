"""Scan and gallery HTTP routes."""

import base64
from datetime import datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
from typing import Any
import zipfile

import cv2
import numpy as np
from flask import Blueprint, Response, jsonify, render_template, request, send_file, send_from_directory
from PIL import Image

from sentinel_analysis.application.use_cases.correlate_ais_detections import interpolate_kinematic_track
from sentinel_analysis.domain.entities import BoundingBox, Scan
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
    provider = optional_string(payload, "provider") or "copernicus"
    aoi_name = optional_string(payload, "aoi_name")
    scan = container().create_scan.execute(
        bbox,
        days_ago=days_ago,
        aoi_name=aoi_name,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )
    return jsonify(
        status="success",
        folderName=scan.folder_name,
        customName=scan.metadata.get("custom_name") or scan.folder_name,
        imageUrl=scan_image_url(scan, container().settings.output_root),
        bounds=[[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
        datetime=scan.acquisition.acquired_at.isoformat(),
        provider=scan.metadata.get("provider", provider),
    ), 201


@blueprint.route("/api/umbra/scenes", methods=["GET", "POST"])
def list_umbra_scenes():
    cnt = container()
    client = getattr(cnt, "umbra_client", None)
    if client is None:
        from sentinel_analysis.infrastructure.satellite.umbra_client import UmbraOpenDataClient
        client = UmbraOpenDataClient()

    site_key = None
    bbox = None
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        site_key = payload.get("site_key") or payload.get("site")
        if "bbox" in payload:
            try:
                bbox = bounding_box(payload)
            except Exception:
                pass
    else:
        site_key = request.args.get("site") or request.args.get("site_key")
        min_lon = request.args.get("min_lon")
        if min_lon is not None:
            try:
                bbox = BoundingBox(
                    float(request.args.get("min_lon")),
                    float(request.args.get("min_lat")),
                    float(request.args.get("max_lon")),
                    float(request.args.get("max_lat")),
                )
            except Exception:
                pass

    if bbox and not site_key:
        site_key = client.search_nearby_site(bbox)

    scenes = client.fetch_site_scenes(site_key) if site_key else []
    return jsonify({
        "status": "success",
        "sites": client.get_maritime_sites(),
        "matched_site": site_key,
        "scenes": [s.to_dict() for s in scenes],
    })


@blueprint.route("/api/asf/search", methods=["GET", "POST"])
def search_asf():
    cnt = container()
    client = getattr(cnt, "asf_client", None)
    if client is None:
        from sentinel_analysis.infrastructure.satellite.asf_client import ASFSearchClient
        client = ASFSearchClient()

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        bbox = bounding_box(payload)
        platform = str(payload.get("platform") or "SENTINEL-1")
        beam_mode = str(payload.get("beam_mode") or "IW")
        max_results = int(payload.get("max_results") or 20)
        start_date = optional_datetime(payload, "start_date")
        end_date = optional_datetime(payload, "end_date")
    else:
        min_lon = float(request.args.get("min_lon", 103.5))
        min_lat = float(request.args.get("min_lat", 1.0))
        max_lon = float(request.args.get("max_lon", 104.5))
        max_lat = float(request.args.get("max_lat", 2.0))
        bbox = BoundingBox(min_lon, min_lat, max_lon, max_lat)
        platform = request.args.get("platform", "SENTINEL-1")
        beam_mode = request.args.get("beam_mode", "IW")
        max_results = int(request.args.get("max_results", 20))
        start_date = None
        end_date = None

    try:
        products = client.search(
            bbox=bbox,
            platform=platform,
            beam_mode=beam_mode,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        products = []

    return jsonify({
        "status": "success",
        "count": len(products),
        "products": [p.to_dict() for p in products],
    })


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


@blueprint.get("/api/scan/<folder_name>/chip/<int:detection_idx>")
def get_detection_chip_by_index(folder_name: str, detection_idx: int):
    return get_detection_crop(folder_name, requested_idx=detection_idx)


@blueprint.get("/api/scan/<folder_name>/crop")
def get_detection_crop(folder_name: str, requested_idx: int | None = None):
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    image_path = Path(scan.image_path)
    if not image_path.is_file():
        raise RequestValidationError(f"Scan image not found: {image_path}")

    # Check for existing detection results to enrich analytical payload
    detections: list[dict[str, Any]] = []
    det_json_path = image_path.parent / "detection_results.json"
    if det_json_path.is_file():
        try:
            det_data = json.loads(det_json_path.read_text(encoding="utf-8"))
            detections = det_data.get("detections", [])
        except Exception:
            pass

    idx_param = requested_idx
    if idx_param is None:
        idx_arg = request.args.get("detection_idx") or request.args.get("index")
        if idx_arg is not None:
            try:
                idx_param = int(idx_arg)
            except ValueError:
                pass

    target_detection: dict[str, Any] | None = None
    if idx_param is not None and detections:
        if 0 <= idx_param < len(detections):
            target_detection = detections[idx_param]
        elif 1 <= idx_param <= len(detections):
            target_detection = detections[idx_param - 1]

    bbox = request.args.get("bbox")
    if target_detection is not None:
        x = int(round(float(target_detection.get("x", 0))))
        y = int(round(float(target_detection.get("y", 0))))
        w = int(round(float(target_detection.get("width", 50))))
        h = int(round(float(target_detection.get("height", 50))))
        padding = max(0, int(request.args.get("padding", 35)))
    elif bbox:
        try:
            parts = [int(float(p.strip())) for p in bbox.split(",")]
            if len(parts) == 4:
                x, y, w, h = parts
            else:
                x, y, w, h = 0, 0, 50, 50
            padding = max(0, int(request.args.get("padding", 25)))
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("Coordinates and dimensions must be valid numbers") from exc
    else:
        try:
            x = int(request.args.get("x", 0))
            y = int(request.args.get("y", 0))
            w = int(request.args.get("width", 50))
            h = int(request.args.get("height", 50))
            padding = max(0, int(request.args.get("padding", 25)))
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("Coordinates and dimensions must be valid integers") from exc

    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise RequestValidationError("Crop dimensions must be positive non-negative integers")

    # If target_detection was not matched by index, attempt spatial match with detection list
    if target_detection is None and detections:
        best_match = None
        min_dist = float("inf")
        for d in detections:
            dx = float(d.get("x", 0)) - x
            dy = float(d.get("y", 0)) - y
            dist = math.hypot(dx, dy)
            if dist < min_dist and dist < 20.0:
                min_dist = dist
                best_match = d
        if best_match is not None:
            target_detection = best_match

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

    # Calculate analytical radar intensity distribution and signatures
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    h_crop, w_crop = gray.shape[:2]

    mean_intensity = float(np.mean(gray))
    max_intensity = float(np.max(gray))
    min_intensity = float(np.min(gray))
    std_intensity = float(np.std(gray))

    # Perimeter clutter estimation (outer 15% boundary of crop)
    border_x = max(1, int(w_crop * 0.15))
    border_y = max(1, int(h_crop * 0.15))
    clutter_mask = np.ones((h_crop, w_crop), dtype=bool)
    clutter_mask[border_y : h_crop - border_y, border_x : w_crop - border_x] = False
    clutter_pix = gray[clutter_mask]
    clutter_mean = float(np.mean(clutter_pix)) if clutter_pix.size > 0 else mean_intensity
    clutter_std = float(np.std(clutter_pix)) if clutter_pix.size > 0 else std_intensity

    # Target Peak SNR in dB vs clutter floor
    snr_linear = max(1e-3, max_intensity) / max(1e-3, clutter_mean)
    snr_db = round(10.0 * math.log10(snr_linear), 2)

    # Dynamic Range across chip in dB
    dynamic_range_db = round(10.0 * math.log10(max(1.0, max_intensity) / max(1.0, max(0.1, min_intensity))), 2)

    # Radar Cross-Section estimate in dBm^2 (Sentinel-1 10m x 10m IW pixel area)
    pixel_area_m2 = 100.0
    target_integral = float(np.sum(np.maximum(0.0, gray.astype(float) - clutter_mean)))
    rcs_linear = max(1.0, target_integral * (pixel_area_m2 / 255.0))
    estimated_rcs_dbsm = round(10.0 * math.log10(rcs_linear), 1)

    # 1D Cross-sectional Transect Profiles through target peak pixel
    peak_y, peak_x = np.unravel_index(np.argmax(gray), gray.shape)
    azimuth_profile = [round(float(v), 1) for v in gray[:, peak_x]]
    range_profile = [round(float(v), 1) for v in gray[peak_y, :]]

    # 32-bin intensity histogram
    hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten().tolist()

    # Crop-local coordinate mappings
    local_box = {
        "x": x - x1,
        "y": y - y1,
        "width": w,
        "height": h,
    }
    local_center = {
        "x": round(x + w / 2.0 - x1, 1),
        "y": round(y + h / 2.0 - y1, 1),
    }
    local_peak = {
        "x": int(peak_x),
        "y": int(peak_y),
    }

    # Extract OBB if available
    local_obb = None
    if target_detection and target_detection.get("obb"):
        obb = target_detection["obb"]
        if "corners" in obb:
            local_obb = {
                "corners": [[round(c[0] - x1, 1), round(c[1] - y1, 1)] for c in obb["corners"]],
                "angle": obb.get("angle", 0.0),
                "length_m": obb.get("length_m"),
                "beam_m": obb.get("beam_m"),
                "aspect_ratio": obb.get("aspect_ratio"),
            }
        elif "center" in obb:
            local_obb = {
                "center": [round(obb["center"][0] - x1, 1), round(obb["center"][1] - y1, 1)],
                "size": obb.get("size"),
                "angle": obb.get("angle", 0.0),
            }

    # Extract Wake if available
    wake_info = target_detection.get("wake") if target_detection else None
    local_wake = None
    if wake_info and wake_info.get("detected"):
        angle_rad = math.radians(wake_info.get("wake_angle", 0.0))
        wake_len = max(w, h) * 1.5
        local_wake = {
            "wake_angle": wake_info.get("wake_angle"),
            "confidence": wake_info.get("confidence"),
            "estimated_speed_knots": wake_info.get("estimated_speed_knots"),
            "ais_speed_discrepancy": wake_info.get("ais_speed_discrepancy", False),
            "start": [local_center["x"], local_center["y"]],
            "end": [
                round(local_center["x"] + wake_len * math.sin(angle_rad), 1),
                round(local_center["y"] - wake_len * math.cos(angle_rad), 1),
            ],
        }

    # Extract Classification if available
    classification_info = target_detection.get("classification") if target_detection else None

    # Extract Correlation if available
    correlation_info = target_detection.get("correlated_ais") if target_detection else None
    correlation_status = target_detection.get("correlation_status") if target_detection else None

    return jsonify({
        "data_uri": data_uri,
        "crop_width": crop.shape[1],
        "crop_height": crop.shape[0],
        "origin": {"x": x1, "y": y1},
        "local_box": local_box,
        "local_center": local_center,
        "local_peak": local_peak,
        "local_obb": local_obb,
        "local_wake": local_wake,
        "classification": classification_info,
        "correlation": correlation_info,
        "correlation_status": correlation_status,
        "stats": {
            "mean_intensity": round(mean_intensity, 2),
            "max_intensity": round(max_intensity, 2),
            "min_intensity": round(min_intensity, 2),
            "std_intensity": round(std_intensity, 2),
            "clutter_mean": round(clutter_mean, 2),
            "clutter_std": round(clutter_std, 2),
            "snr_db": snr_db,
            "dynamic_range_db": dynamic_range_db,
            "estimated_rcs_dbsm": estimated_rcs_dbsm,
            "azimuth_profile": azimuth_profile,
            "range_profile": range_profile,
            "histogram": [round(float(v), 1) for v in hist],
        },
    })


@blueprint.get("/api/scan/<folder_name>/ais_tracks")
def get_scan_ais_tracks(folder_name: str):
    """Retrieve temporal AIS tracks and kinematic interpolations centered on SAR pass time."""
    scan = container().get_scan.execute(safe_folder_name(folder_name))
    bbox = scan.bbox

    # Parse scan acquisition timestamp
    scan_dt: datetime | None = None
    if getattr(scan, "acquisition", None) and getattr(scan.acquisition, "acquired_at", None):
        scan_dt = scan.acquisition.acquired_at
    elif getattr(scan, "timestamp", None):
        try:
            scan_dt = datetime.fromisoformat(str(scan.timestamp).replace("Z", "+00:00"))
        except Exception:
            pass
    elif getattr(scan, "metadata", None) and isinstance(scan.metadata, dict):
        raw_ts = scan.metadata.get("acquired_at") or scan.metadata.get("timestamp")
        if raw_ts:
            try:
                scan_dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            except Exception:
                pass
    if scan_dt is None:
        scan_dt = datetime.now(timezone.utc)

    window_hours = float(request.args.get("window_hours", 2.0))
    time_start = scan_dt - timedelta(hours=window_hours)
    time_end = scan_dt + timedelta(hours=window_hours)

    ais_repo = getattr(container(), "ais_repository", None)
    vessels: list[dict[str, Any]] = []

    if ais_repo is not None and hasattr(ais_repo, "get_vessel_positions") and bbox:
        try:
            records = ais_repo.get_vessel_positions(
                bbox=bbox,
                time_range=(time_start, time_end),
                latest_only=False,
                limit=5000,
            )
        except Exception:
            records = []

        by_mmsi: dict[str, list[dict[str, Any]]] = {}
        for r in records:
            mmsi = str(r.get("mmsi") or r.get("vessel_id") or "")
            if mmsi:
                by_mmsi.setdefault(mmsi, []).append(r)

        for mmsi, pts in by_mmsi.items():
            pts.sort(key=lambda p: str(p.get("timestamp") or ""))
            interp = None
            try:
                interp = interpolate_kinematic_track(pts, scan_dt)
            except Exception:
                pass
            rep = pts[-1]
            vessels.append({
                "mmsi": mmsi,
                "name": rep.get("name") or rep.get("vessel_name") or f"Vessel {mmsi}",
                "ship_type": rep.get("type") or rep.get("ship_type") or "Vessel",
                "callsign": rep.get("callsign"),
                "points": [
                    {
                        "timestamp": p.get("timestamp"),
                        "latitude": float(p.get("latitude", 0.0)),
                        "longitude": float(p.get("longitude", 0.0)),
                        "speed": float(p.get("speed") or 0.0),
                        "heading": float(p.get("heading") or 0.0),
                    }
                    for p in pts
                    if p.get("latitude") is not None and p.get("longitude") is not None
                ],
                "interpolated_at_pass": interp,
            })

    # Load detections if available
    detections: list[dict[str, Any]] = []
    det_json_path = Path(scan.image_path).parent / "detection_results.json"
    if det_json_path.is_file():
        try:
            det_data = json.loads(det_json_path.read_text(encoding="utf-8"))
            detections = det_data.get("detections", [])
        except Exception:
            pass

    return jsonify({
        "scan_folder": folder_name,
        "scan_timestamp": scan_dt.isoformat(),
        "window_start": time_start.isoformat(),
        "window_end": time_end.isoformat(),
        "bbox": bbox.as_list() if bbox else None,
        "vessels": vessels,
        "detections": detections,
        "vessel_count": len(vessels),
        "detection_count": len(detections),
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
@blueprint.get("/api/scan/<folder_name>/export/geojson")
def get_scan_geojson(folder_name: str):
    safe_name = safe_folder_name(folder_name)
    cnt = container()
    scan = cnt.get_scan.execute(safe_name)
    image_path = Path(scan.image_path)
    candidates = [
        image_path.parent / f"{image_path.stem}_detections.geojson",
        image_path.parent / "detections.geojson",
    ]
    for c in candidates:
        if c.is_file():
            as_att = request.args.get("download") in ("1", "true")
            return send_file(c, mimetype="application/geo+json", as_attachment=as_att, download_name=c.name)

    try:
        fc = cnt.export_geospatial.export_geojson(safe_name)
        as_att = request.args.get("download") in ("1", "true")
        if as_att:
            buf = io.BytesIO(json.dumps(fc, indent=2).encode("utf-8"))
            return send_file(
                buf,
                mimetype="application/geo+json",
                as_attachment=True,
                download_name=f"{scan.folder_name}_detections.geojson",
            )
        return jsonify(fc)
    except Exception as exc:
        return jsonify(error=f"GeoJSON export failed: {exc}"), 500


@blueprint.get("/api/scan/<folder_name>/export/geotiff")
@blueprint.get("/api/scan/<folder_name>/geotiff")
def export_scan_geotiff(folder_name: str):
    safe_name = safe_folder_name(folder_name)
    cnt = container()
    try:
        tif_path = cnt.export_geospatial.export_geotiff(safe_name)
        as_att = request.args.get("download", "true").lower() in ("1", "true")
        return send_file(
            tif_path,
            mimetype="image/tiff",
            as_attachment=as_att,
            download_name=tif_path.name,
        )
    except Exception as exc:
        return jsonify(error=f"GeoTIFF export failed: {exc}"), 500


@blueprint.get("/api/scan/<folder_name>/export/stac")
@blueprint.get("/api/scan/<folder_name>/stac")
def export_scan_stac(folder_name: str):
    safe_name = safe_folder_name(folder_name)
    cnt = container()
    try:
        stac_item = cnt.export_geospatial.export_stac_item(safe_name, base_url=request.host_url.rstrip("/"))
        return jsonify(stac_item)
    except Exception as exc:
        return jsonify(error=f"STAC export failed: {exc}"), 500


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



@blueprint.route("/api/scan/<folder_name>/optical_validation", methods=["GET", "POST"])
def cross_validate_optical_scan(folder_name: str):
    payload = request.get_json(silent=True) or {}
    time_window = payload.get("time_window_hours")
    max_cloud = payload.get("max_cloud_cover")
    time_window_hours = float(time_window) if time_window is not None else 48.0
    max_cloud_cover = float(max_cloud) if max_cloud is not None else 50.0

    result = container().cross_validate_optical.execute(
        folder_name=safe_folder_name(folder_name),
        time_window_hours=time_window_hours,
        max_cloud_cover=max_cloud_cover,
    )
    return jsonify(result)


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
