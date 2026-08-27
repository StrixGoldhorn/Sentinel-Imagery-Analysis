"""Ship-detection use case."""

from pathlib import Path

from sentinel_analysis.application.ports.providers import ShipDetector


class DetectShips:
    def __init__(self, detector: ShipDetector) -> None:
        self._detector = detector

    def execute(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
    ):
        return self._detector.detect(image_path, dem_path, threshold)

