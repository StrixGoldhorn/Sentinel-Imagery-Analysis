"""Use case for cross-validating SAR ship detections with concurrent Sentinel-2 optical imagery."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.optical import (
    OpticalCrossValidator,
    OpticalScene,
    OpticalValidationResult,
)
from sentinel_analysis.application.ports.scan_repository import ScanRepository

logger = logging.getLogger(__name__)


class CrossValidateOptical:
    """Cross-validate SAR ship detections against concurrent Sentinel-2 optical scenes and NDWI."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        optical_validator: OpticalCrossValidator,
    ) -> None:
        self._scan_repo = scan_repository
        self._validator = optical_validator

    def execute(
        self,
        folder_name: str,
        time_window_hours: float = 48.0,
        max_cloud_cover: float = 50.0,
    ) -> dict[str, Any]:
        """Execute optical cross validation on an existing scan."""
        scan = self._scan_repo.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")

        image_path = Path(scan.image_path)
        scan_dir = image_path.parent

        # Locate detection results JSON
        json_path = scan_dir / "detection_results.json"
        if not json_path.exists():
            alt_path = scan_dir / f"{image_path.stem}_detections.json"
            if alt_path.exists():
                json_path = alt_path

        detections: list[dict[str, Any]] = []
        full_json_payload: dict[str, Any] = {}
        if json_path.exists():
            try:
                full_json_payload = json.loads(json_path.read_text(encoding="utf-8"))
                detections = full_json_payload.get("detections", [])
            except Exception as exc:
                logger.warning("Failed to read detections JSON in %s: %s", scan_dir, exc)

        # Query concurrent Sentinel-2 scenes
        scenes = self._validator.search_concurrent_optical(
            bbox=scan.bbox,
            target_datetime=scan.acquisition.acquired_at,
            time_window_hours=time_window_hours,
            max_cloud_cover=max_cloud_cover,
        )

        best_scene = scenes[0] if scenes else None

        # Cross validate each detection
        validation_results = []
        confirmed_count = 0
        land_alarm_count = 0
        cloud_obscured_count = 0

        for idx, det in enumerate(detections):
            val_res = self._validator.validate_detection_optical(
                detection_idx=idx,
                det=det,
                scene=best_scene,
            )
            validation_results.append(val_res.as_dict())

            # Enrich detection dictionary
            det["optical_status"] = val_res.status
            det["optical_confirmed"] = val_res.optical_confirmed
            det["optical_confidence"] = val_res.optical_confidence
            det["optical_scene_id"] = val_res.scene_id
            det["optical_cloud_cover"] = val_res.cloud_cover
            det["optical_time_delta_hours"] = val_res.time_delta_hours
            det["optical_details"] = val_res.details

            if val_res.optical_confirmed:
                confirmed_count += 1
            if val_res.status == "LAND_FALSE_ALARM":
                land_alarm_count += 1
            elif val_res.status == "CLOUD_OBSCURED":
                cloud_obscured_count += 1

        # Save updated detections JSON
        if json_path.exists() and full_json_payload:
            full_json_payload["detections"] = detections
            full_json_payload["optical_validation_summary"] = {
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "scenes_found": len(scenes),
                "best_scene_id": best_scene.scene_id if best_scene else None,
                "confirmed_count": confirmed_count,
                "land_false_alarm_count": land_alarm_count,
                "cloud_obscured_count": cloud_obscured_count,
            }
            try:
                json_path.write_text(json.dumps(full_json_payload, indent=2), encoding="utf-8")
                # Also write standard detection_results.json
                std_json = scan_dir / "detection_results.json"
                if std_json != json_path:
                    std_json.write_text(json.dumps(full_json_payload, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to update detection JSON with optical data: %s", exc)

        # Update GeoJSON properties
        geojson_path = scan_dir / "detections.geojson"
        if not geojson_path.exists():
            geojson_path = scan_dir / f"{image_path.stem}_detections.geojson"

        if geojson_path.exists():
            try:
                gj_data = json.loads(geojson_path.read_text(encoding="utf-8"))
                features = gj_data.get("features", [])
                for f, val in zip(features, validation_results):
                    if "properties" in f and isinstance(f["properties"], dict):
                        f["properties"]["optical_status"] = val["status"]
                        f["properties"]["optical_confirmed"] = val["optical_confirmed"]
                        f["properties"]["optical_confidence"] = val["optical_confidence"]
                        f["properties"]["optical_scene_id"] = val["scene_id"]
                geojson_path.write_text(json.dumps(gj_data, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to update GeoJSON with optical data: %s", exc)

        # Write standalone optical_validation.json
        summary = {
            "status": "success",
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "scan_folder": folder_name,
            "sar_acquired_at": scan.acquisition.acquired_at.isoformat(),
            "scenes_found": len(scenes),
            "best_scene": {
                "scene_id": best_scene.scene_id,
                "acquired_at": best_scene.acquired_at.isoformat(),
                "cloud_cover": best_scene.cloud_cover,
                "time_delta_hours": best_scene.time_delta_hours,
                "platform": best_scene.platform,
            } if best_scene else None,
            "ship_count": len(detections),
            "confirmed_count": confirmed_count,
            "land_false_alarm_count": land_alarm_count,
            "cloud_obscured_count": cloud_obscured_count,
            "results": validation_results,
        }

        opt_json_path = scan_dir / "optical_validation.json"
        try:
            opt_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write optical_validation.json: %s", exc)

        return summary
