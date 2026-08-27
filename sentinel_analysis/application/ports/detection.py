"""Application-owned contract for ship detection."""

from pathlib import Path
from typing import NamedTuple, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import ShipDetection


class DetectionResult(NamedTuple):
    """Detected objects and the dimensions of the analyzed source image."""

    detections: list[ShipDetection]
    image_width: int
    image_height: int


@runtime_checkable
class ShipDetector(Protocol):
    """Detect ships and report detections with source-image dimensions."""

    def detect(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
    ) -> DetectionResult:
        ...
