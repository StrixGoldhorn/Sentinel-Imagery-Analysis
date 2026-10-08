"""Application use case for multi-temporal SAR coherence and change detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.sar_change_detector import SARChangeDetector
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import (
    BoundingBox,
    MultiTemporalChangeReport,
    TemporalChangePoint,
)


class ComputeSARChangeDetection:
    """Compare repeat SAR passes to identify vessel arrivals, departures, and persistent structures."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        change_detector: SARChangeDetector,
    ) -> None:
        self._scan_repository = scan_repository
        self._change_detector = change_detector

    def execute(
        self,
        target_folder: str,
        reference_folder: str,
        *,
        threshold_db: float = 4.5,
    ) -> dict[str, Any]:
        """Compute multi-temporal change detection between reference pass (T1) and target pass (T2)."""
        scan_getter = getattr(self._scan_repository, "get", None) or getattr(self._scan_repository, "get_scan", None)
        if scan_getter is None:
            raise RuntimeError("ScanRepository must provide get() or get_scan()")

        target_scan = scan_getter(target_folder)
        if target_scan is None:
            raise ValueError(f"Target scan '{target_folder}' not found")

        reference_scan = scan_getter(reference_folder)
        if reference_scan is None:
            raise ValueError(f"Reference scan '{reference_folder}' not found")

        target_img_path = Path(target_scan.image_path)
        ref_img_path = Path(reference_scan.image_path)
        output_change_img = target_img_path.parent / f"change_map_vs_{reference_folder}.png"

        raw_res = self._change_detector.compute_change_map(
            reference_image_path=ref_img_path,
            target_image_path=target_img_path,
            output_path=output_change_img,
            threshold_db=threshold_db,
        )

        dim_w = raw_res.get("dimensions", {}).get("width", 1000)
        dim_h = raw_res.get("dimensions", {}).get("height", 1000)
        bbox: Optional[BoundingBox] = getattr(target_scan, "bbox", None)

        def _project(cx: float, cy: float) -> tuple[Optional[float], Optional[float]]:
            if bbox is not None and dim_w > 0 and dim_h > 0:
                lat_scale = (bbox.max_latitude - bbox.min_latitude) / dim_h
                lon_scale = (bbox.max_longitude - bbox.min_longitude) / dim_w
                proj_lat = bbox.max_latitude - cy * lat_scale
                proj_lon = bbox.min_longitude + cx * lon_scale
                return round(proj_lat, 6), round(proj_lon, 6)
            return None, None

        change_points: list[TemporalChangePoint] = []
        geojson_features: list[dict[str, Any]] = []

        for p in raw_res.get("change_points", []):
            cx = float(p.get("x", 0.0))
            cy = float(p.get("y", 0.0))
            change_type = str(p.get("change_type", "UNKNOWN"))
            mag = float(p.get("magnitude_db", 0.0))
            conf = float(p.get("confidence", 0.5))
            narrative = str(p.get("narrative", ""))
            plat, plon = _project(cx, cy)

            pt = TemporalChangePoint(
                x=cx,
                y=cy,
                change_type=change_type,
                magnitude_db=mag,
                confidence=conf,
                lat=plat,
                lon=plon,
                narrative=narrative,
            )
            change_points.append(pt)

            if plat is not None and plon is not None:
                geojson_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [plon, plat],
                    },
                    "properties": {
                        "feature_type": "SAR_CHANGE",
                        "change_type": change_type,
                        "magnitude_db": mag,
                        "confidence": conf,
                        "pixel_x": cx,
                        "pixel_y": cy,
                        "narrative": narrative,
                    },
                })

        report_entity = MultiTemporalChangeReport(
            reference_scan=reference_folder,
            target_scan=target_folder,
            arrived_count=raw_res.get("arrived_count", 0),
            departed_count=raw_res.get("departed_count", 0),
            persistent_structures_count=raw_res.get("persistent_structures_count", 0),
            change_map_path=str(output_change_img),
            timestamp_t1=getattr(getattr(reference_scan, "acquisition", None), "acquired_at", None),
            timestamp_t2=getattr(getattr(target_scan, "acquisition", None), "acquired_at", None),
        )

        geojson_payload = {
            "type": "FeatureCollection",
            "features": geojson_features,
        }

        result = {
            "status": "success",
            "reference_scan": reference_folder,
            "target_scan": target_folder,
            "threshold_db": threshold_db,
            "arrived_count": report_entity.arrived_count,
            "departed_count": report_entity.departed_count,
            "persistent_structures_count": report_entity.persistent_structures_count,
            "total_change_points": len(change_points),
            "change_map_path": str(output_change_img),
            "change_map_filename": output_change_img.name,
            "change_points": [
                {
                    "x": cp.x,
                    "y": cp.y,
                    "lat": cp.lat,
                    "lon": cp.lon,
                    "change_type": cp.change_type,
                    "magnitude_db": cp.magnitude_db,
                    "confidence": cp.confidence,
                    "narrative": cp.narrative,
                }
                for cp in change_points
            ],
            "geojson": geojson_payload,
        }

        # Persist report JSON in target scan workspace
        report_file = target_img_path.parent / f"change_report_{reference_folder}.json"
        try:
            report_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception:
            pass

        return result
