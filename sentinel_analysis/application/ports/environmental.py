"""Application-owned contract for marine environmental context providers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.environmental import MarineEnvironmentContext


@runtime_checkable
class MarineContextProviderPort(Protocol):
    """Provides metocean, bathymetric, and navigational context for maritime analysis."""

    def get_context_for_bbox(
        self,
        bbox: BoundingBox,
        timestamp: Optional[datetime] = None,
    ) -> MarineEnvironmentContext:
        """Retrieve marine environmental context over an AOI bounding box."""
        ...

    def tag_detections_with_environmental_context(
        self,
        detections: list[dict[str, Any]],
        bbox: Optional[BoundingBox] = None,
        timestamp: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """Tag detections with bathymetry, zones, and suppress offshore platform false alarms."""
        ...
