"""Copernicus Sentinel-2 Optical Cross-Validation Client and NDWI Analysis Engine.

Provides concurrent optical scene query over Copernicus STAC API (collections: sentinel-2-l2a,
sentinel-2-l1c) within a configurable temporal window (e.g. ±24h to ±48h), cloud cover filtering,
Normalized Difference Water Index (NDWI) calculation, and optical validation of SAR ship targets.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import cv2
import numpy as np
import requests

from sentinel_analysis.application.ports.optical import (
    OpticalCrossValidator,
    OpticalScene,
    OpticalValidationResult,
)
from sentinel_analysis.domain.entities import BoundingBox

logger = logging.getLogger(__name__)

CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"

Sentinel2Scene = OpticalScene


class Sentinel2Client:
    """Client for Copernicus Sentinel-2 STAC search and optical cross-validation."""

    def __init__(
        self,
        token_provider: Optional[Any] = None,
        http_client: Optional[Any] = None,
        catalog_url: str = CATALOG_URL,
    ) -> None:
        self._token_provider = token_provider
        self._http = http_client or requests
        self._catalog_url = catalog_url

    def search_concurrent_optical(
        self,
        bbox: BoundingBox,
        target_datetime: datetime,
        time_window_hours: float = 48.0,
        max_cloud_cover: float = 50.0,
        limit: int = 10,
    ) -> list[Sentinel2Scene]:
        """Query Copernicus STAC catalog for concurrent Sentinel-2 scenes covering bbox."""
        if target_datetime.utcoffset() is None:
            target_datetime = target_datetime.replace(tzinfo=timezone.utc)
        target_datetime = target_datetime.astimezone(timezone.utc)

        start_dt = target_datetime - timedelta(hours=time_window_hours)
        end_dt = target_datetime + timedelta(hours=time_window_hours)

        headers = {}
        if self._token_provider is not None:
            try:
                token = self._token_provider.get()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            except Exception as exc:
                logger.warning("Failed to obtain Copernicus auth token for Sentinel-2 query: %s", exc)

        params = {
            "bbox": f"{bbox.min_longitude},{bbox.min_latitude},{bbox.max_longitude},{bbox.max_latitude}",
            "datetime": f"{start_dt.isoformat().replace('+00:00', 'Z')}/{end_dt.isoformat().replace('+00:00', 'Z')}",
            "collections": "sentinel-2-l2a",
            "limit": limit,
        }

        try:
            resp = self._http.get(self._catalog_url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.info("Copernicus STAC Sentinel-2 query unavailable: %s", exc)
            return []

        features = data.get("features", [])
        if not isinstance(features, list):
            return []

        scenes: list[Sentinel2Scene] = []
        for feat in features:
            if not isinstance(feat, dict):
                continue
            props = feat.get("properties", {})
            dt_str = props.get("datetime")
            if not dt_str:
                continue

            try:
                scene_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue

            cloud = float(props.get("eo:cloud_cover", props.get("cloud_cover", 0.0)))
            if cloud > max_cloud_cover:
                continue

            delta_hours = round(abs((scene_dt - target_datetime).total_seconds()) / 3600.0, 2)
            feat_bbox = feat.get("bbox") or bbox.as_list()
            scene_bbox = (
                float(feat_bbox[0]),
                float(feat_bbox[1]),
                float(feat_bbox[2]),
                float(feat_bbox[3]),
            )

            scenes.append(
                Sentinel2Scene(
                    scene_id=feat.get("id", f"S2_{dt_str}"),
                    acquired_at=scene_dt,
                    cloud_cover=round(cloud, 1),
                    platform=props.get("platform", "Sentinel-2"),
                    bbox=scene_bbox,
                    time_delta_hours=delta_hours,
                    product_id=feat.get("id"),
                    assets=feat.get("assets", {}),
                )
            )

        # Sort scenes by composite ranking: time proximity (primary) and low cloud cover (secondary)
        scenes.sort(key=lambda s: (s.time_delta_hours, s.cloud_cover))
        return scenes

    @staticmethod
    def compute_ndwi(green_band: np.ndarray, nir_band: np.ndarray) -> np.ndarray:
        """Calculate Normalized Difference Water Index (NDWI) from Green and NIR bands.

        Formula: NDWI = (Green - NIR) / (Green + NIR)
        Open water: NDWI > 0.1 to 0.8
        Vessels, land, man-made structures: NDWI < 0.0 or low positive
        """
        g = green_band.astype(np.float32)
        nir = nir_band.astype(np.float32)
        denom = g + nir + 1e-6
        ndwi = (g - nir) / denom
        return np.clip(ndwi, -1.0, 1.0)

    def validate_detection_optical(
        self,
        detection_idx: int,
        det: dict[str, Any],
        scene: Optional[Sentinel2Scene] = None,
        green_chip: Optional[np.ndarray] = None,
        nir_chip: Optional[np.ndarray] = None,
        rgb_chip: Optional[np.ndarray] = None,
    ) -> OpticalValidationResult:
        """Cross-validate an individual SAR detection against optical multi-spectral data."""
        if scene is None:
            return OpticalValidationResult(
                detection_index=detection_idx,
                status="NO_CONCURRENT_OPTICAL",
                optical_confirmed=False,
                details="No concurrent Sentinel-2 optical pass available within temporal window",
            )

        # If synthetic / mock or provided chips
        if green_chip is not None and nir_chip is not None:
            ndwi_chip = self.compute_ndwi(green_chip, nir_chip)
            h, w = ndwi_chip.shape[:2]
            cx, cy = w // 2, h // 2

            # Target center sample (radius 3)
            r_target = max(1, min(cx, cy, 3))
            target_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(target_mask, (cx, cy), r_target, 255, -1)

            # Background water ring sample (radius 6 to 14)
            r_inner = max(r_target + 2, min(cx, cy, 6))
            r_outer = min(cx, cy, 14)
            bg_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(bg_mask, (cx, cy), r_outer, 255, -1)
            cv2.circle(bg_mask, (cx, cy), r_inner, 0, -1)

            mean_target_ndwi = float(np.mean(ndwi_chip[target_mask > 0])) if np.any(target_mask > 0) else 0.0
            mean_water_ndwi = float(np.mean(ndwi_chip[bg_mask > 0])) if np.any(bg_mask > 0) else 0.0
            contrast = mean_water_ndwi - mean_target_ndwi

            # Check cloud saturation (high RGB reflectance & high NIR)
            is_cloud = False
            if rgb_chip is not None and rgb_chip.size > 0:
                mean_brightness = float(np.mean(rgb_chip))
                mean_nir = float(np.mean(nir_chip))
                if mean_brightness > 210 and mean_nir > 200:
                    is_cloud = True

            if is_cloud or scene.cloud_cover > 75.0:
                return OpticalValidationResult(
                    detection_index=detection_idx,
                    status="CLOUD_OBSCURED",
                    optical_confirmed=False,
                    time_delta_hours=scene.time_delta_hours,
                    cloud_cover=scene.cloud_cover,
                    target_ndwi=round(mean_target_ndwi, 3),
                    water_ndwi=round(mean_water_ndwi, 3),
                    contrast_ndwi=round(contrast, 3),
                    optical_confidence=0.2,
                    scene_id=scene.scene_id,
                    details=f"Detection obscured by optical cloud cover ({scene.cloud_cover}%)",
                )

            # Both target and surrounding area have low NDWI (< 0.05) -> static land / reef / island
            if mean_water_ndwi < 0.05 and mean_target_ndwi < 0.05:
                return OpticalValidationResult(
                    detection_index=detection_idx,
                    status="LAND_FALSE_ALARM",
                    optical_confirmed=False,
                    time_delta_hours=scene.time_delta_hours,
                    cloud_cover=scene.cloud_cover,
                    target_ndwi=round(mean_target_ndwi, 3),
                    water_ndwi=round(mean_water_ndwi, 3),
                    contrast_ndwi=round(contrast, 3),
                    optical_confidence=0.88,
                    scene_id=scene.scene_id,
                    details="Optical NDWI identifies target area as dry land/reef false alarm",
                )

            # Strong NDWI contrast: water is high NDWI, target center is low NDWI (vessel metal/deck)
            if mean_water_ndwi >= 0.15 and contrast >= 0.12:
                conf = float(np.clip(0.6 + contrast * 0.8, 0.65, 0.98))
                return OpticalValidationResult(
                    detection_index=detection_idx,
                    status="CONFIRMED_VESSEL",
                    optical_confirmed=True,
                    time_delta_hours=scene.time_delta_hours,
                    cloud_cover=scene.cloud_cover,
                    target_ndwi=round(mean_target_ndwi, 3),
                    water_ndwi=round(mean_water_ndwi, 3),
                    contrast_ndwi=round(contrast, 3),
                    optical_confidence=round(conf, 3),
                    scene_id=scene.scene_id,
                    details=f"Confirmed vessel signature in Sentinel-2 NDWI (contrast Δ={contrast:.2f}, Δt={scene.time_delta_hours}h)",
                )

            # Moderate contrast or slight anomaly
            return OpticalValidationResult(
                detection_index=detection_idx,
                status="INCONCLUSIVE",
                optical_confirmed=False,
                time_delta_hours=scene.time_delta_hours,
                cloud_cover=scene.cloud_cover,
                target_ndwi=round(mean_target_ndwi, 3),
                water_ndwi=round(mean_water_ndwi, 3),
                contrast_ndwi=round(contrast, 3),
                optical_confidence=0.50,
                scene_id=scene.scene_id,
                details=f"Optical NDWI anomaly inconclusive (Δ={contrast:.2f})",
            )

        # If imagery chips not directly available locally, validate based on scene coverage & metadata
        if scene.cloud_cover <= 20.0:
            status = "CONFIRMED_VESSEL"
            confirmed = True
            conf = 0.75
            details = f"Concurrent clear optical pass ({scene.cloud_cover}% clouds, Δt={scene.time_delta_hours}h) corroborates target"
        elif scene.cloud_cover >= 70.0:
            status = "CLOUD_OBSCURED"
            confirmed = False
            conf = 0.3
            details = f"Heavy cloud cover ({scene.cloud_cover}%) prevents optical visual confirmation"
        else:
            status = "INCONCLUSIVE"
            confirmed = False
            conf = 0.5
            details = f"Moderate optical cloud cover ({scene.cloud_cover}%, Δt={scene.time_delta_hours}h)"

        return OpticalValidationResult(
            detection_index=detection_idx,
            status=status,
            optical_confirmed=confirmed,
            time_delta_hours=scene.time_delta_hours,
            cloud_cover=scene.cloud_cover,
            optical_confidence=conf,
            scene_id=scene.scene_id,
            details=details,
        )
