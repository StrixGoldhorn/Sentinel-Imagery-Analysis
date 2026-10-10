"""Utility to save visual annotated images and structured JSON CV results in the image directory."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from sentinel_analysis.domain.coordinates import (
    build_geojson_geometry,
    project_detection_coordinates,
    resolve_image_transform,
)
from sentinel_analysis.domain.entities import ShipDetection
from sentinel_analysis.domain.provenance import build_preprocessing_provenance


def _serialize_detection(
    item: ShipDetection | dict[str, Any],
    index: int,
    default_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a ShipDetection entity or dictionary into a serializable dictionary with complete provenance."""
    if isinstance(item, dict):
        data = dict(item)
        data.setdefault("index", index)
        # Harmonize coordinate representations
        if data.get("lat") is None and data.get("latitude") is not None:
            data["lat"] = round(float(data["latitude"]), 6)
        if data.get("lng") is None and data.get("longitude") is not None:
            data["lng"] = round(float(data["longitude"]), 6)
        if data.get("latitude") is None and data.get("lat") is not None:
            data["latitude"] = round(float(data["lat"]), 7)
        if data.get("longitude") is None and data.get("lng") is not None:
            data["longitude"] = round(float(data["lng"]), 7)

        prov_dict = data.get("provenance") or default_provenance or {}
        normalized_prov = build_preprocessing_provenance(base_provenance=prov_dict, metadata=data)
        data["provenance"] = normalized_prov
        for k in (
            "orbit",
            "product_id",
            "polarization",
            "processing_baseline",
            "calibration_method",
            "terrain_correction",
            "dem",
            "speckle_filtering",
            "pixel_spacing",
            "model_version",
            "thresholds",
            "source_checksum",
            "source_checksums",
        ):
            data.setdefault(k, normalized_prov[k])
        return data

    pts = getattr(item, "polygon_points", None)
    if pts is not None:
        pts = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in pts]

    geo_poly = getattr(item, "geo_polygon", None)
    if geo_poly is not None:
        geo_poly = [[round(float(p[0]), 7), round(float(p[1]), 7)] for p in geo_poly]

    lat_val = getattr(item, "latitude", None)
    lon_val = getattr(item, "longitude", None)
    geo_box = getattr(item, "geo_bbox", None)
    if geo_box and isinstance(geo_box, dict):
        geo_box = dict(geo_box)

    item_prov = getattr(item, "provenance", None) or default_provenance or {}
    normalized_prov = build_preprocessing_provenance(base_provenance=item_prov)

    return {
        "index": index,
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
        "polygon_points": pts,
        "lat": round(float(lat_val), 6) if lat_val is not None else None,
        "lng": round(float(lon_val), 6) if lon_val is not None else None,
        "latitude": round(float(lat_val), 7) if lat_val is not None else None,
        "longitude": round(float(lon_val), 7) if lon_val is not None else None,
        "geo_bbox": geo_box,
        "geo_polygon": geo_poly,
        "wake_detected": getattr(item, "wake_detected", None),
        "wake_heading": getattr(item, "wake_heading", None),
        "wake_speed_knots": getattr(item, "wake_speed_knots", None),
        "wake_confidence": getattr(item, "wake_confidence", None),
        "is_speed_spoofed": getattr(item, "is_speed_spoofed", None),
        "is_course_spoofed": getattr(item, "is_course_spoofed", None),
        "vessel_class": getattr(item, "vessel_class", None),
        "classification_confidence": getattr(item, "classification_confidence", None),
        "optical_status": getattr(item, "optical_status", None),
        "optical_confirmed": getattr(item, "optical_confirmed", None),
        "optical_confidence": getattr(item, "optical_confidence", None),
        "temporal_change_type": getattr(item, "temporal_change_type", None),
        "correlation_status": "uncorrelated",
        "is_correlated": False,
        "correlated_ais": None,
        "spatial_uncertainty": getattr(item, "spatial_uncertainty", None),
        "dimension_uncertainty": getattr(item, "dimension_uncertainty", None),
        "association_likelihood": getattr(item, "association_likelihood", None),
        "reason_codes": list(getattr(item, "reason_codes", None)) if getattr(item, "reason_codes", None) is not None else [],
        "cep_meters": (getattr(item, "spatial_uncertainty", None) or {}).get("cep_meters") if isinstance(getattr(item, "spatial_uncertainty", None), dict) else None,
        "provenance": normalized_prov,
        "orbit": normalized_prov["orbit"],
        "product_id": normalized_prov["product_id"],
        "polarization": normalized_prov["polarization"],
        "processing_baseline": normalized_prov["processing_baseline"],
        "calibration_method": normalized_prov["calibration_method"],
        "terrain_correction": normalized_prov["terrain_correction"],
        "dem": normalized_prov["dem"],
        "speckle_filtering": normalized_prov["speckle_filtering"],
        "pixel_spacing": normalized_prov["pixel_spacing"],
        "model_version": normalized_prov["model_version"],
        "thresholds": normalized_prov["thresholds"],
        "source_checksum": normalized_prov["source_checksum"],
        "source_checksums": normalized_prov["source_checksums"],
    }


WGS84_ESRI_PRJ = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]\n'
)


def _extract_bbox_coords(bbox: Any) -> tuple[float, float, float, float] | None:
    """Extract (min_lat, max_lat, min_lon, max_lon) from a BoundingBox or dictionary."""
    if bbox is None:
        return None
    if hasattr(bbox, "min_latitude") and hasattr(bbox, "max_latitude"):
        return (
            float(bbox.min_latitude),
            float(bbox.max_latitude),
            float(bbox.min_longitude),
            float(bbox.max_longitude),
        )
    if isinstance(bbox, dict):
        min_lat = bbox.get("min_latitude", bbox.get("min_lat"))
        max_lat = bbox.get("max_latitude", bbox.get("max_lat"))
        min_lon = bbox.get("min_longitude", bbox.get("min_lon"))
        max_lon = bbox.get("max_longitude", bbox.get("max_lon"))
        if None not in (min_lat, max_lat, min_lon, max_lon):
            return float(min_lat), float(max_lat), float(min_lon), float(max_lon)
    return None


def _build_geojson_feature_collection(
    detections: list[dict[str, Any]],
    image_name: str | None = None,
    ghost_vessels: list[dict[str, Any]] | None = None,
    bbox: Any | None = None,
    image_width: int = 1,
    image_height: int = 1,
    transform_fn: Any | None = None,
) -> dict[str, Any]:
    """Generate RFC 7946 GeoJSON FeatureCollection from serialized detections and ghost vessels."""
    features = []
    for d in detections:
        geometry = build_geojson_geometry(
            d,
            bbox=bbox,
            image_width=image_width,
            image_height=image_height,
            transform_fn=transform_fn,
        )

        lat = d.get("latitude") if d.get("latitude") is not None else d.get("lat")
        lng = d.get("longitude") if d.get("longitude") is not None else d.get("lng")

        ais = d.get("correlated_ais") or {}
        properties = {
            "index": d.get("index"),
            "confidence": d.get("confidence"),
            "length_m": d.get("length"),
            "beam_m": d.get("beam"),
            "angle_deg": d.get("angle"),
            "center_pixel": [d.get("center_x"), d.get("center_y")],
            "lat": round(float(lat), 6) if lat is not None else None,
            "lng": round(float(lng), 6) if lng is not None else None,
            "latitude": round(float(lat), 7) if lat is not None else None,
            "longitude": round(float(lng), 7) if lng is not None else None,
            "geo_bbox": d.get("geo_bbox"),
            "geo_polygon": d.get("geo_polygon"),
            "correlation_status": d.get("correlation_status", "uncorrelated"),
            "is_correlated": bool(d.get("is_correlated", False)),
            "is_dark_vessel": bool(d.get("is_dark_vessel", False)),
            "dark_vessel_risk": d.get("dark_vessel_risk", "NOMINAL"),
            "dark_vessel_score": d.get("dark_vessel_score", 0.0),
            "estimated_class": d.get("vessel_class") or d.get("estimated_class"),
            "vessel_class": d.get("vessel_class"),
            "classification_confidence": d.get("classification_confidence"),
            "dark_vessel_reasons": d.get("dark_vessel_reasons", []),
            "vessel_name": ais.get("vessel_name") or ais.get("name"),
            "mmsi": ais.get("mmsi"),
            "vessel_type": ais.get("vessel_type") or ais.get("type"),
            "speed_knots": ais.get("speed"),
            "heading_deg": ais.get("heading"),
            "dead_reckoned": ais.get("dead_reckoned", False),
            "distance_to_shape_meters": ais.get("distance_to_box_meters"),
            "optical_status": d.get("optical_status"),
            "optical_confirmed": d.get("optical_confirmed"),
            "optical_confidence": d.get("optical_confidence"),
            "temporal_change_type": d.get("temporal_change_type"),
            # Preprocessing Provenance
            "orbit": d.get("orbit"),
            "product_id": d.get("product_id"),
            "polarization": d.get("polarization"),
            "processing_baseline": d.get("processing_baseline"),
            "calibration_method": d.get("calibration_method"),
            "terrain_correction": d.get("terrain_correction"),
            "dem": d.get("dem"),
            "speckle_filtering": d.get("speckle_filtering"),
            "pixel_spacing": d.get("pixel_spacing"),
            "model_version": d.get("model_version"),
            "thresholds": d.get("thresholds"),
            "source_checksum": d.get("source_checksum"),
            "source_checksums": d.get("source_checksums"),
            "provenance": d.get("provenance"),
            # Calibrated Contact Uncertainty & Reason Codes
            "spatial_uncertainty": d.get("spatial_uncertainty"),
            "cep_meters": (
                (d.get("spatial_uncertainty") or {}).get("cep_meters")
                if isinstance(d.get("spatial_uncertainty"), dict)
                else d.get("cep_meters")
            ),
            "dimension_uncertainty": d.get("dimension_uncertainty"),
            "association_likelihood": d.get("association_likelihood"),
            "reason_codes": d.get("reason_codes", []),
        }

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": properties,
        })

    if ghost_vessels:
        for gv in ghost_vessels:
            glat = gv.get("latitude")
            glon = gv.get("longitude")
            if glat is not None and glon is not None:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(float(glon), 6), round(float(glat), 6)],
                    },
                    "properties": {
                        "feature_type": "ghost_vessel",
                        "anomaly_type": "GHOST_VESSEL",
                        "mmsi": gv.get("mmsi"),
                        "vessel_name": gv.get("vessel_name") or gv.get("name"),
                        "vessel_type": gv.get("vessel_type") or gv.get("type"),
                        "callsign": gv.get("callsign"),
                        "speed_knots": gv.get("speed"),
                        "heading_deg": gv.get("heading"),
                        "dead_reckoned": gv.get("dead_reckoned", False),
                        "reason": gv.get("reason"),
                    },
                })

    return {
        "type": "FeatureCollection",
        "name": f"Detections - {image_name}" if image_name else "Sentinel-1 Ship Detections",
        "crs": {
            "type": "name",
            "properties": {
                "name": "urn:ogc:def:crs:OGC:1.3:CRS84",
            },
        },
        "features": features,
    }


def save_detection_results(
    image_path: Path | str,
    detections: list[ShipDetection | dict[str, Any]],
    image_width: int | None = None,
    image_height: int | None = None,
    metadata: dict[str, Any] | None = None,
    bbox: Any | None = None,
    ghost_vessels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Render detection overlays directly onto a copy of the image and persist visual PNG, JSON, and GeoJSON results.

    Saves in the same folder as image_path:
      - <image_stem>_detected.png
      - detected_ships.png
      - <image_stem>_detections.json
      - detection_results.json
      - <image_stem>_detections.geojson
      - detections.geojson
      - ESRI World Files (.pgw, .prj) when bbox coordinates are available.
    """
    target_img = Path(image_path).resolve()
    target_dir = target_img.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    default_prov = build_preprocessing_provenance(
        image_path=target_img,
        metadata=metadata,
        dem=metadata.get("dem") if metadata else None,
        thresholds=metadata if metadata else None,
    )

    # 1. Normalize and structure detections
    serialized_detections: list[dict[str, Any]] = []
    for idx, det in enumerate(detections):
        serialized_detections.append(_serialize_detection(det, idx, default_provenance=default_prov))

    # 2. Render visual annotations on the image
    img = cv2.imread(str(target_img), cv2.IMREAD_COLOR) if target_img.is_file() else None
    actual_h, actual_w = (img.shape[0], img.shape[1]) if img is not None else (image_height or 0, image_width or 0)

    # 3. Resolve projection coordinates for detections if missing
    effective_bbox = bbox or (metadata.get("bbox") if metadata else None)
    transform_fn = resolve_image_transform(target_img, bbox=effective_bbox, image_width=actual_w, image_height=actual_h)
    for det_dict in serialized_detections:
        if (det_dict.get("lat") is None or det_dict.get("geo_bbox") is None) and (effective_bbox is not None or transform_fn is not None) and actual_w > 0 and actual_h > 0:
            coords = project_detection_coordinates(
                det_dict,
                bbox=effective_bbox,
                image_width=actual_w,
                image_height=actual_h,
                transform_fn=transform_fn,
            )
            if coords.get("lat") is not None:
                det_dict["lat"] = coords["lat"]
                det_dict["lng"] = coords["lng"]
                det_dict["latitude"] = coords["latitude"]
                det_dict["longitude"] = coords["longitude"]
                det_dict["geo_bbox"] = coords["geo_bbox"]
                if coords.get("geo_polygon") is not None:
                    det_dict["geo_polygon"] = coords["geo_polygon"]

    if img is not None:
        annotated = img.copy()

        # Colors in BGR format
        color_inside_box = (129, 185, 16)   # #10b981 (green)
        color_outside_box = (212, 182, 6)   # #06b6d4 (cyan)
        color_uncorrelated = (51, 51, 255)  # #ff3333 (red)
        color_obb = (34, 126, 230)          # #e67e22 (orange)
        color_dark_vessel = (0, 0, 230)     # High-alert crimson

        for det in serialized_detections:
            corr_status = det.get("correlation_status")
            polygon_pts = det.get("polygon_points")
            is_dark = det.get("is_dark_vessel", False)

            if is_dark:
                stroke_color = color_dark_vessel
            elif corr_status == "inside_box":
                stroke_color = color_inside_box
            elif corr_status == "outside_box":
                stroke_color = color_outside_box
            elif polygon_pts and len(polygon_pts) == 4:
                stroke_color = color_obb
            else:
                stroke_color = color_uncorrelated

            # Draw polygon or rectangle
            if polygon_pts and len(polygon_pts) >= 3:
                pts_arr = np.array(polygon_pts, np.int32).reshape((-1, 1, 2))
                cv2.polylines(annotated, [pts_arr], isClosed=True, color=stroke_color, thickness=2)
            else:
                x = int(det.get("x", 0))
                y = int(det.get("y", 0))
                w = int(det.get("width", 0))
                h = int(det.get("height", 0))
                cv2.rectangle(annotated, (x, y), (x + w, y + h), stroke_color, 2)

            # Label text
            idx_num = det.get("index", 0) + 1
            conf = det.get("confidence")
            conf_str = f" {int(conf * 100)}%" if conf is not None else ""
            label_text = f"Ship {idx_num}{conf_str}"
            ais = det.get("correlated_ais")
            if ais and isinstance(ais, dict):
                v_name = ais.get("vessel_name") or ais.get("name")
                if v_name:
                    label_text += f": {v_name}"
            elif is_dark:
                risk = det.get("dark_vessel_risk", "ALERT")
                len_val = det.get("length")
                len_str = f" [{risk} DARK {int(len_val)}m]" if len_val else f" [{risk} DARK]"
                label_text += len_str

            v_class = det.get("vessel_class")
            if v_class and not (ais and isinstance(ais, dict) and (ais.get("vessel_name") or ais.get("name"))):
                if not is_dark:
                    label_text += f" ({v_class})"

            x_text = int(det.get("x", 0))
            y_text = max(15, int(det.get("y", 0)) - 5)
            cv2.putText(
                annotated,
                label_text,
                (x_text, y_text),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                stroke_color,
                1,
                lineType=cv2.LINE_AA,
            )

        # Write visual files
        detected_image_name = f"{target_img.stem}_detected.png"
        detected_image_path = target_dir / detected_image_name
        standard_image_path = target_dir / "detected_ships.png"

        cv2.imwrite(str(detected_image_path), annotated)
        cv2.imwrite(str(standard_image_path), annotated)
    else:
        detected_image_name = None
        detected_image_path = None
        standard_image_path = None

    # 3. Create structured JSON results
    now_iso = datetime.now(timezone.utc).isoformat()
    inside_count = sum(1 for d in serialized_detections if d.get("correlation_status") == "inside_box")
    outside_count = sum(1 for d in serialized_detections if d.get("correlation_status") == "outside_box")
    uncorrelated_count = sum(1 for d in serialized_detections if d.get("correlation_status") == "uncorrelated")
    correlated_count = inside_count + outside_count
    dark_vessel_count = sum(1 for d in serialized_detections if d.get("is_dark_vessel"))
    critical_dark_count = sum(1 for d in serialized_detections if d.get("dark_vessel_risk") == "CRITICAL")

    json_payload = {
        "timestamp": now_iso,
        "image_file": target_img.name,
        "image_path": str(target_img),
        "image_width": actual_w,
        "image_height": actual_h,
        "ship_count": len(serialized_detections),
        "correlated_count": correlated_count,
        "inside_box_count": inside_count,
        "outside_box_count": outside_count,
        "uncorrelated_count": uncorrelated_count,
        "dark_vessel_count": dark_vessel_count,
        "critical_dark_count": critical_dark_count,
        "ghost_vessel_count": len(ghost_vessels or []),
        "detected_image": detected_image_name,
        "parameters": metadata or {},
        "detections": serialized_detections,
    }
    if ghost_vessels:
        json_payload["ghost_vessels"] = ghost_vessels

    detections_json_name = f"{target_img.stem}_detections.json"
    detections_json_path = target_dir / detections_json_name
    standard_json_path = target_dir / "detection_results.json"

    json_str = json.dumps(json_payload, indent=2)
    detections_json_path.write_text(json_str, encoding="utf-8")
    standard_json_path.write_text(json_str, encoding="utf-8")

    # 4. Create standard GeoJSON FeatureCollection
    geojson_payload = _build_geojson_feature_collection(
        serialized_detections,
        target_img.name,
        ghost_vessels=ghost_vessels,
        bbox=effective_bbox,
        image_width=actual_w,
        image_height=actual_h,
        transform_fn=transform_fn,
    )
    geojson_str = json.dumps(geojson_payload, indent=2)
    detections_geojson_path = target_dir / f"{target_img.stem}_detections.geojson"
    standard_geojson_path = target_dir / "detections.geojson"
    detections_geojson_path.write_text(geojson_str, encoding="utf-8")
    standard_geojson_path.write_text(geojson_str, encoding="utf-8")

    # 5. Create ESRI World Files (.pgw and .prj) when bbox coordinates are present
    bbox_coords = _extract_bbox_coords(bbox or (metadata.get("bbox") if metadata else None))
    world_files_created = False
    if bbox_coords and actual_w > 0 and actual_h > 0:
        min_lat, max_lat, min_lon, max_lon = bbox_coords
        dx = (max_lon - min_lon) / float(actual_w)
        dy = -(max_lat - min_lat) / float(actual_h)
        x_center = min_lon + dx / 2.0
        y_center = max_lat + dy / 2.0
        pgw_content = f"{dx:.10f}\n0.0000000000\n0.0000000000\n{dy:.10f}\n{x_center:.10f}\n{y_center:.10f}\n"

        target_stems = []
        if detected_image_path:
            target_stems.append(detected_image_path.stem)
        if standard_image_path:
            target_stems.append(standard_image_path.stem)
        if target_img.is_file():
            target_stems.append(target_img.stem)

        for stem in set(target_stems):
            (target_dir / f"{stem}.pgw").write_text(pgw_content, encoding="utf-8")
            (target_dir / f"{stem}.prj").write_text(WGS84_ESRI_PRJ, encoding="utf-8")
        world_files_created = True

    return {
        "detected_image_path": str(detected_image_path) if detected_image_path else None,
        "standard_image_path": str(standard_image_path) if standard_image_path else None,
        "detections_json_path": str(detections_json_path),
        "standard_json_path": str(standard_json_path),
        "detections_geojson_path": str(detections_geojson_path),
        "standard_geojson_path": str(standard_geojson_path),
        "detected_image_name": detected_image_name,
        "detections_json_name": detections_json_name,
        "detections_geojson_name": detections_geojson_path.name,
        "world_files_created": world_files_created,
        "ship_count": len(serialized_detections),
        "correlated_count": correlated_count,
        "inside_box_count": inside_count,
        "outside_box_count": outside_count,
        "uncorrelated_count": uncorrelated_count,
        "dark_vessel_count": dark_vessel_count,
        "critical_dark_count": critical_dark_count,
        "ghost_vessel_count": len(ghost_vessels or []),
        "ghost_vessels": ghost_vessels or [],
        "timestamp": now_iso,
    }
