"""Application-owned ports for optical cross-validation and multi-spectral analysis."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import BoundingBox


@dataclass(frozen=True)
class OpticalScene:
    """Metadata for an acquired optical scene (e.g. Sentinel-2)."""

    scene_id: str
    acquired_at: datetime
    cloud_cover: float
    platform: str
    bbox: tuple[float, float, float, float]
    time_delta_hours: float
    product_id: Optional[str] = None
    assets: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpticalValidationResult:
    """Result of optical cross-validation for a specific SAR detection."""

    detection_index: int
    status: str  # CONFIRMED_VESSEL, LAND_FALSE_ALARM, CLOUD_OBSCURED, INCONCLUSIVE, NO_CONCURRENT_OPTICAL
    optical_confirmed: bool
    time_delta_hours: Optional[float] = None
    cloud_cover: Optional[float] = None
    target_ndwi: Optional[float] = None
    water_ndwi: Optional[float] = None
    contrast_ndwi: Optional[float] = None
    optical_confidence: float = 0.0
    scene_id: Optional[str] = None
    details: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "detection_index": self.detection_index,
            "status": self.status,
            "optical_confirmed": self.optical_confirmed,
            "time_delta_hours": self.time_delta_hours,
            "cloud_cover": self.cloud_cover,
            "target_ndwi": self.target_ndwi,
            "water_ndwi": self.water_ndwi,
            "contrast_ndwi": self.contrast_ndwi,
            "optical_confidence": self.optical_confidence,
            "scene_id": self.scene_id,
            "details": self.details,
        }


@runtime_checkable
class OpticalCrossValidator(Protocol):
    """Protocol for querying concurrent optical imagery and performing target cross-validation."""

    def search_concurrent_optical(
        self,
        bbox: BoundingBox,
        target_datetime: datetime,
        time_window_hours: float = 48.0,
        max_cloud_cover: float = 50.0,
        limit: int = 10,
    ) -> list[OpticalScene]:
        ...

    def validate_detection_optical(
        self,
        detection_idx: int,
        det: dict[str, Any],
        scene: Optional[OpticalScene] = None,
        green_chip: Optional[Any] = None,
        nir_chip: Optional[Any] = None,
        rgb_chip: Optional[Any] = None,
    ) -> OpticalValidationResult:
        ...
