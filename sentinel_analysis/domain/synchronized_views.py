"""Domain models for synchronized multi-modality split views."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SplitViewMode(str, Enum):
    DUAL = "dual"
    QUAD = "quad"


class LayerModality(str, Enum):
    SAR = "sar"
    OPTICAL = "optical"
    MASK = "mask"
    AIS = "ais"


@dataclass(frozen=True)
class ViewportInfo:
    """Coordinated spatial viewport for synchronized panes."""

    center_lat: float
    center_lon: float
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    initial_zoom: int = 12

    def to_dict(self) -> dict[str, Any]:
        return {
            "center": [self.center_lat, self.center_lon],
            "center_lat": self.center_lat,
            "center_lon": self.center_lon,
            "bounds": [
                [self.min_lat, self.min_lon],
                [self.max_lat, self.max_lon],
            ],
            "bbox": [self.min_lon, self.min_lat, self.max_lon, self.max_lat],
            "initial_zoom": self.initial_zoom,
        }


@dataclass
class LayerPane:
    """A synchronized viewport layer representing one sensor/modality pane."""

    modality: str
    title: str
    available: bool
    image_url: Optional[str] = None
    vector_geojson: Optional[dict[str, Any]] = None
    status_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "modality": self.modality,
            "title": self.title,
            "available": self.available,
            "image_url": self.image_url,
            "vector_geojson": self.vector_geojson,
            "status_message": self.status_message,
            "metadata": self.metadata,
        }


@dataclass
class SynchronizedSplitView:
    """Coordinated multi-modality state for dual and quad split views."""

    scan_id: str
    viewport: ViewportInfo
    panes: dict[str, LayerPane]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "viewport": self.viewport.to_dict(),
            "panes": {k: pane.to_dict() for k, pane in self.panes.items()},
            "modes_supported": [SplitViewMode.DUAL.value, SplitViewMode.QUAD.value],
            "generated_at": self.generated_at,
        }
