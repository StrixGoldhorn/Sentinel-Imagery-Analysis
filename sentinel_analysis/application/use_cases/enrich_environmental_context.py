"""Use case to enrich satellite scans and contacts with marine environmental and nautical layers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.environmental import MarineContextProviderPort
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.environmental import MarineEnvironmentContext

logger = logging.getLogger(__name__)


class EnrichMarineEnvironmentalContext:
    """Enriches scans and vessel detections with metocean, bathymetric, and navigational context layers."""

    def __init__(
        self,
        marine_provider: MarineContextProviderPort,
        scan_repository: Optional[Any] = None,
    ) -> None:
        self._provider = marine_provider
        self._scan_repository = scan_repository

    def execute(
        self,
        scan_id: Optional[str] = None,
        aoi_bbox: Optional[BoundingBox] = None,
        detections: Optional[list[dict[str, Any]]] = None,
        timestamp: Optional[datetime] = None,
    ) -> dict[str, Any]:
        bbox = aoi_bbox
        scan_time = timestamp
        scan_detections = list(detections or [])

        scan_obj = None
        if scan_id and self._scan_repository is not None:
            try:
                scan_obj = self._scan_repository.get_scan(scan_id)
                if scan_obj is not None:
                    if bbox is None:
                        bbox = scan_obj.bbox
                    if scan_time is None and scan_obj.acquisition:
                        scan_time = scan_obj.acquisition.acquired_at
                    if not scan_detections and hasattr(scan_obj, "detections"):
                        scan_detections = [dict(d) if isinstance(d, dict) else d.__dict__ for d in scan_obj.detections]
            except Exception as exc:
                logger.warning("Could not fetch scan details for %s: %s", scan_id, exc)

        if bbox is None:
            # Fallback global default if neither bbox nor scan provided
            bbox = BoundingBox(min_latitude=1.10, min_longitude=103.50, max_latitude=1.40, max_longitude=104.30)

        ts = scan_time or datetime.now(timezone.utc)
        env_context = self._provider.get_context_for_bbox(bbox, timestamp=ts)
        tagged_detections = self._provider.tag_detections_with_environmental_context(
            scan_detections, bbox=bbox, timestamp=ts
        )

        # Build GeoJSON feature collection of environmental and nautical layers
        features = []

        # 1. Navigational Zones (Shipping Lanes, Anchorages, Port Boundaries)
        for z in env_context.active_zones:
            coords = [[p[1], p[0]] for p in z.polygon]
            # Ensure closed polygon
            if coords and coords[0] != coords[-1]:
                coords.append(coords[0])
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords],
                },
                "properties": {
                    "layer": "NAVIGATIONAL_ZONE",
                    "zone_id": z.zone_id,
                    "zone_type": z.zone_type.value,
                    "name": z.name,
                    "speed_limit_knots": z.speed_limit_knots,
                    "port_unlocode": z.port_unlocode,
                    "description": z.description,
                },
            })

        # 2. Offshore Infrastructure
        for inf in env_context.nearby_infrastructure:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [inf.longitude, inf.latitude],
                },
                "properties": {
                    "layer": "OFFSHORE_INFRASTRUCTURE",
                    "feature_id": inf.feature_id,
                    "feature_type": inf.feature_type.value,
                    "name": inf.name,
                    "radius_meters": inf.radius_meters,
                    "status": inf.status,
                    "description": inf.description,
                },
            })

        geojson_layers = {
            "type": "FeatureCollection",
            "features": features,
        }

        result = {
            "status": "success",
            "context": env_context.to_dict(),
            "tagged_detections_count": len(tagged_detections),
            "tagged_detections": tagged_detections,
            "geojson_layers": geojson_layers,
        }

        # Persist to scan folder if available
        if scan_obj and scan_obj.image_path:
            try:
                scan_dir = Path(scan_obj.image_path).parent
                (scan_dir / "environmental_context.json").write_text(
                    json.dumps(result["context"], indent=2), encoding="utf-8"
                )
                (scan_dir / "environmental_layers.geojson").write_text(
                    json.dumps(geojson_layers, indent=2), encoding="utf-8"
                )
            except Exception as exc:
                logger.warning("Failed persisting environmental layers for %s: %s", scan_id, exc)

        return result
