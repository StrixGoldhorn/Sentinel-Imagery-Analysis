"""Ship-detection use case."""

from pathlib import Path

from sentinel_analysis.application.ports.detection import DetectionResult, ShipDetector


class DetectShips:
    def __init__(self, detector: ShipDetector) -> None:
        self._detector = detector

    def execute(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
    ) -> DetectionResult:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
            raise ValueError("Detection threshold must be an integer between 0 and 255")
        detections, width, height = self._detector.detect(image_path, dem_path, threshold)
        if width <= 0 or height <= 0:
            raise ValueError("Detector returned invalid image dimensions")
        return DetectionResult(list(detections), width, height)
