"""Utility to save visual annotated images and structured JSON CV results in the image directory."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from sentinel_analysis.domain.entities import ShipDetection


def _serialize_detection(item: ShipDetection | dict[str, Any], index: int) -> dict[str, Any]:
    """Normalize a ShipDetection entity or dictionary into a serializable dictionary."""
    if isinstance(item, dict):
        data = dict(item)
        data.setdefault("index", index)
        return data

    pts = getattr(item, "polygon_points", None)
    if pts is not None:
        pts = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in pts]

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
        "correlation_status": "uncorrelated",
        "is_correlated": False,
        "correlated_ais": None,
    }


def save_detection_results(
    image_path: Path | str,
    detections: list[ShipDetection | dict[str, Any]],
    image_width: int | None = None,
    image_height: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render detection overlays directly onto a copy of the image and persist both visual PNG and JSON results.

    Saves in the same folder as image_path:
      - <image_stem>_detected.png
      - detected_ships.png
      - <image_stem>_detections.json
      - detection_results.json
    """
    target_img = Path(image_path).resolve()
    target_dir = target_img.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Normalize and structure detections
    serialized_detections: list[dict[str, Any]] = []
    for idx, det in enumerate(detections):
        serialized_detections.append(_serialize_detection(det, idx))

    # 2. Render visual annotations on the image
    img = cv2.imread(str(target_img), cv2.IMREAD_COLOR) if target_img.is_file() else None
    actual_h, actual_w = (img.shape[0], img.shape[1]) if img is not None else (image_height or 0, image_width or 0)

    if img is not None:
        annotated = img.copy()

        # Colors in BGR format
        color_inside_box = (129, 185, 16)   # #10b981 (green)
        color_outside_box = (212, 182, 6)   # #06b6d4 (cyan)
        color_uncorrelated = (51, 51, 255)  # #ff3333 (red)
        color_obb = (34, 126, 230)          # #e67e22 (orange)

        for det in serialized_detections:
            corr_status = det.get("correlation_status")
            polygon_pts = det.get("polygon_points")

            if corr_status == "inside_box":
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
        "detected_image": detected_image_name,
        "parameters": metadata or {},
        "detections": serialized_detections,
    }

    detections_json_name = f"{target_img.stem}_detections.json"
    detections_json_path = target_dir / detections_json_name
    standard_json_path = target_dir / "detection_results.json"

    json_str = json.dumps(json_payload, indent=2)
    detections_json_path.write_text(json_str, encoding="utf-8")
    standard_json_path.write_text(json_str, encoding="utf-8")

    return {
        "detected_image_path": str(detected_image_path) if detected_image_path else None,
        "standard_image_path": str(standard_image_path) if standard_image_path else None,
        "detections_json_path": str(detections_json_path),
        "standard_json_path": str(standard_json_path),
        "detected_image_name": detected_image_name,
        "detections_json_name": detections_json_name,
        "ship_count": len(serialized_detections),
        "correlated_count": correlated_count,
        "inside_box_count": inside_count,
        "outside_box_count": outside_count,
        "uncorrelated_count": uncorrelated_count,
        "timestamp": now_iso,
    }
