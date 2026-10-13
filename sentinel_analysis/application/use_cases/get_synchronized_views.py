"""Use case for retrieving coordinated multi-modality split views."""

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.domain.entities import BoundingBox, Scan
from sentinel_analysis.domain.synchronized_views import (
    LayerModality,
    LayerPane,
    SynchronizedSplitView,
    ViewportInfo,
)

logger = logging.getLogger(__name__)


class GetSynchronizedViews:
    """Orchestrates synchronized multi-modality views across SAR, Optical, Detection Mask, and AIS."""

    def __init__(
        self,
        scan_repository: Any,
        ais_repository: Optional[Any] = None,
        sentinel2_client: Optional[Any] = None,
    ) -> None:
        self._scan_repo = scan_repository
        self._ais_repo = ais_repository
        self._sentinel2_client = sentinel2_client

    def execute(self, scan_id: str) -> dict[str, Any]:
        """Generate synchronized view specifications for the given scan ID."""
        scan = self._scan_repo.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan '{scan_id}' not found")

        # 1. Viewport geometry
        bbox = scan.bbox
        center_lat = (bbox.min_latitude + bbox.max_latitude) / 2.0
        center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0
        span = max(bbox.max_latitude - bbox.min_latitude, bbox.max_longitude - bbox.min_longitude)

        initial_zoom = 12
        if span < 0.10:
            initial_zoom = 14
        elif span < 0.25:
            initial_zoom = 13
        elif span < 0.60:
            initial_zoom = 12
        elif span < 1.20:
            initial_zoom = 11
        else:
            initial_zoom = 10

        viewport = ViewportInfo(
            center_lat=round(center_lat, 6),
            center_lon=round(center_lon, 6),
            min_lat=round(bbox.min_latitude, 6),
            min_lon=round(bbox.min_longitude, 6),
            max_lat=round(bbox.max_latitude, 6),
            max_lon=round(bbox.max_longitude, 6),
            initial_zoom=initial_zoom,
        )

        detections = self._load_detections(scan)

        # 2. Build Panes
        sar_pane = self._build_sar_pane(scan)
        optical_pane = self._build_optical_pane(scan, detections)
        mask_pane = self._build_mask_pane(scan, detections)
        ais_pane = self._build_ais_pane(scan, detections)

        panes = {
            LayerModality.SAR.value: sar_pane,
            LayerModality.OPTICAL.value: optical_pane,
            LayerModality.MASK.value: mask_pane,
            LayerModality.AIS.value: ais_pane,
        }

        split_view = SynchronizedSplitView(
            scan_id=scan.folder_name,
            viewport=viewport,
            panes=panes,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        return split_view.to_dict()

    # -------------------------------------------------------------------------
    # Pane Builders
    # -------------------------------------------------------------------------
    def _build_sar_pane(self, scan: Scan) -> LayerPane:
        pols = list(scan.acquisition.polarizations)
        pol_label = "/".join(pols) if pols else "SAR"
        title = f"Sentinel-1 SAR Amplitude ({pol_label})"

        return LayerPane(
            modality=LayerModality.SAR.value,
            title=title,
            available=True,
            image_url=f"/media/scans/{scan.folder_name}/stitched.png",
            status_message="Primary SAR radar backscatter calibrated layer",
            metadata={
                "satellite": scan.acquisition.satellite,
                "product_type": scan.acquisition.product_type,
                "polarizations": pols,
                "orbit_direction": scan.acquisition.orbit_direction,
                "acquired_at": scan.acquisition.acquired_at.isoformat(),
                "folder_name": scan.folder_name,
            },
        )

    def _build_optical_pane(self, scan: Scan, detections: list[dict[str, Any]]) -> LayerPane:
        scan_dir = Path(scan.image_path).parent
        optical_path: Optional[Path] = None
        for candidate_name in ("optical_image.png", "sentinel2.png", "optical.png"):
            p = scan_dir / candidate_name
            if p.is_file():
                optical_path = p
                break

        # Check detection optical validation metadata
        opt_status = None
        cloud_cover = None
        time_delta_h = None
        scene_id = None
        confirmed_count = 0

        for det in detections:
            if det.get("optical_status"):
                opt_status = det.get("optical_status")
            if det.get("optical_cloud_cover") is not None:
                cloud_cover = det.get("optical_cloud_cover")
            if det.get("optical_time_delta_hours") is not None:
                time_delta_h = det.get("optical_time_delta_hours")
            if det.get("optical_scene_id"):
                scene_id = det.get("optical_scene_id")
            if det.get("optical_confirmed"):
                confirmed_count += 1

        available = optical_path is not None
        image_url = f"/api/scans/{scan.folder_name}/optical-layer.png" if available else None

        if available:
            status = f"Concurrent Sentinel-2 optical imagery loaded"
            if cloud_cover is not None:
                status += f" ({cloud_cover:.1f}% cloud cover)"
        elif self._sentinel2_client is not None:
            try:
                scenes = self._sentinel2_client.search_concurrent_optical(
                    bbox=scan.bbox,
                    target_datetime=scan.acquisition.acquired_at,
                    time_window_hours=48.0,
                    limit=3,
                )
                if scenes:
                    best = scenes[0]
                    scene_id = best.scene_id
                    cloud_cover = best.cloud_cover
                    time_delta_h = best.time_delta_hours
                    status = (
                        f"Concurrent Sentinel-2 scene cataloged ({scene_id}): "
                        f"{cloud_cover:.1f}% clouds, {time_delta_h:+.1f}h delta"
                    )
                else:
                    status = "No concurrent Sentinel-2 optical scene found within +/- 48h"
            except Exception as exc:
                logger.debug("Error querying Sentinel-2 optical catalog: %s", exc)
                status = "Optical catalog search unavailable"
        else:
            status = "Optical imagery not yet acquired for this scan"

        return LayerPane(
            modality=LayerModality.OPTICAL.value,
            title="Sentinel-2 MSI Optical (RGB)",
            available=available,
            image_url=image_url,
            status_message=status,
            metadata={
                "satellite": "Sentinel-2 MSI",
                "scene_id": scene_id,
                "cloud_cover_pct": cloud_cover,
                "time_delta_hours": time_delta_h,
                "confirmed_targets_count": confirmed_count,
                "has_optical_file": available,
            },
        )

    def _build_mask_pane(self, scan: Scan, detections: list[dict[str, Any]]) -> LayerPane:
        features = []
        dark_count = 0
        ais_corr_count = 0
        confidences = []

        for idx, det in enumerate(detections):
            lat = det.get("latitude")
            lon = det.get("longitude")
            is_dark = det.get("is_dark", True)
            if is_dark:
                dark_count += 1
            else:
                ais_corr_count += 1

            conf = det.get("confidence")
            if conf is not None:
                try:
                    confidences.append(float(conf))
                except (ValueError, TypeError):
                    pass

            geo_bbox = det.get("geo_bbox")
            feature_geom = None

            if geo_bbox and all(k in geo_bbox for k in ("min_lon", "min_lat", "max_lon", "max_lat")):
                min_lo = geo_bbox["min_lon"]
                min_la = geo_bbox["min_lat"]
                max_lo = geo_bbox["max_lon"]
                max_la = geo_bbox["max_lat"]
                feature_geom = {
                    "type": "Polygon",
                    "coordinates": [[
                        [min_lo, min_la],
                        [max_lo, min_la],
                        [max_lo, max_la],
                        [min_lo, max_la],
                        [min_lo, min_la],
                    ]],
                }
            elif lon is not None and lat is not None:
                feature_geom = {
                    "type": "Point",
                    "coordinates": [lon, lat],
                }

            if feature_geom is not None:
                features.append({
                    "type": "Feature",
                    "id": idx,
                    "geometry": feature_geom,
                    "properties": {
                        "detection_index": idx,
                        "confidence": det.get("confidence"),
                        "length": det.get("length"),
                        "beam": det.get("beam"),
                        "angle": det.get("angle"),
                        "vessel_class": det.get("vessel_class") or "vessel",
                        "is_dark": is_dark,
                        "optical_confirmed": det.get("optical_confirmed", False),
                        "mmsi": det.get("mmsi") or det.get("correlated_mmsi"),
                    },
                })

        geojson = {
            "type": "FeatureCollection",
            "features": features,
        }

        avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else None

        return LayerPane(
            modality=LayerModality.MASK.value,
            title="Ship Detection Mask & Target Vectors",
            available=True,
            image_url=f"/api/scans/{scan.folder_name}/detection-mask.png",
            vector_geojson=geojson,
            status_message=f"{len(detections)} targets detected ({dark_count} dark, {ais_corr_count} AIS correlated)",
            metadata={
                "total_detections": len(detections),
                "dark_targets": dark_count,
                "correlated_targets": ais_corr_count,
                "average_confidence": avg_conf,
            },
        )

    def _build_ais_pane(self, scan: Scan, detections: list[dict[str, Any]]) -> LayerPane:
        # Collect correlated MMSIs from detections
        correlated_mmsis = set()
        for det in detections:
            m = det.get("mmsi") or det.get("correlated_mmsi")
            if m:
                correlated_mmsis.add(str(m))

        features = []
        seen_mmsis = set()

        # Query AIS repository if available
        if self._ais_repo is not None:
            time_window = (
                scan.acquisition.acquired_at - timedelta(hours=2),
                scan.acquisition.acquired_at + timedelta(hours=2),
            )
            try:
                positions = self._ais_repo.get_vessel_positions(
                    bbox=scan.bbox,
                    time_range=time_window,
                    limit=1000,
                    latest_only=True,
                )
                for pos in positions:
                    lat = pos.get("latitude")
                    lon = pos.get("longitude")
                    mmsi = str(pos.get("mmsi") or "")
                    if lat is not None and lon is not None:
                        seen_mmsis.add(mmsi)
                        is_corr = mmsi in correlated_mmsis
                        features.append({
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [lon, lat],
                            },
                            "properties": {
                                "mmsi": mmsi,
                                "vessel_name": pos.get("vessel_name") or f"MMSI {mmsi}",
                                "speed": pos.get("speed"),
                                "heading": pos.get("heading"),
                                "vessel_type": pos.get("vessel_type"),
                                "is_correlated": is_corr,
                                "source": "ais_repository",
                            },
                        })
            except Exception as exc:
                logger.debug("Failed querying AIS repository for split view: %s", exc)

        # Supplement with correlated AIS targets from detection file if missing
        for det in detections:
            mmsi = det.get("mmsi") or det.get("correlated_mmsi")
            if mmsi and str(mmsi) not in seen_mmsis:
                lat = det.get("latitude")
                lon = det.get("longitude")
                if lat is not None and lon is not None:
                    seen_mmsis.add(str(mmsi))
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [lon, lat],
                        },
                        "properties": {
                            "mmsi": str(mmsi),
                            "vessel_name": det.get("vessel_name") or f"Vessel {mmsi}",
                            "speed": det.get("ais_speed") or det.get("speed"),
                            "heading": det.get("heading") or det.get("angle"),
                            "vessel_type": det.get("vessel_type"),
                            "is_correlated": True,
                            "source": "detection_metadata",
                        },
                    })

        geojson = {
            "type": "FeatureCollection",
            "features": features,
        }

        return LayerPane(
            modality=LayerModality.AIS.value,
            title="AIS Kinematic Ground Truth",
            available=True,
            image_url=None,
            vector_geojson=geojson,
            status_message=f"{len(features)} AIS contacts within scan spatio-temporal window",
            metadata={
                "contact_count": len(features),
                "correlated_count": sum(1 for f in features if f["properties"].get("is_correlated")),
                "time_window_hours": 4.0,
            },
        )

    # -------------------------------------------------------------------------
    # Helper
    # -------------------------------------------------------------------------
    def _load_detections(self, scan: Scan) -> list[dict[str, Any]]:
        scan_dir = Path(scan.image_path).parent
        for fname in ("detection_results.json", "detections.json", "ships.json", "results.json"):
            p = scan_dir / fname
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        candidates = (
                            data.get("detections")
                            or data.get("candidates")
                            or data.get("ships")
                            or data.get("contacts")
                        )
                        if isinstance(candidates, list):
                            return candidates
                except Exception as exc:
                    logger.debug("Failed parsing detection file %s: %s", p, exc)
        return []
