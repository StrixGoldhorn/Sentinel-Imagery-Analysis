"""Ship-detection use case."""

from pathlib import Path
from typing import Any

from sentinel_analysis.application.ports.detection import DetectionResult, ShipDetector


class DetectShips:
    def __init__(self, detector: ShipDetector) -> None:
        self._detector = detector

    def execute(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
        coastal_buffer: int | None = None,
        enable_wake_detection: bool | None = None,
        vh_path: Path | None = None,
        **kwargs: Any,
    ) -> DetectionResult:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
            raise ValueError("Detection threshold must be an integer between 0 and 255")
        
        detect_kwargs: dict[str, Any] = dict(kwargs)
        if coastal_buffer is not None:
            if isinstance(coastal_buffer, bool) or not isinstance(coastal_buffer, int) or coastal_buffer < 0:
                raise ValueError("Coastal buffer must be a non-negative integer")
            detect_kwargs["coastal_buffer"] = coastal_buffer
        if enable_wake_detection is not None:
            detect_kwargs["enable_wake_detection"] = enable_wake_detection
        if vh_path is not None:
            detect_kwargs["vh_path"] = vh_path

        try:
            detections, width, height = self._detector.detect(
                image_path, dem_path, threshold, **detect_kwargs
            )
        except TypeError:
            try:
                if "coastal_buffer" in detect_kwargs:
                    detections, width, height = self._detector.detect(
                        image_path, dem_path, threshold, coastal_buffer=detect_kwargs["coastal_buffer"]
                    )
                else:
                    detections, width, height = self._detector.detect(image_path, dem_path, threshold)
            except TypeError:
                detections, width, height = self._detector.detect(image_path, dem_path, threshold)

        if width <= 0 or height <= 0:
            raise ValueError("Detector returned invalid image dimensions")
        return DetectionResult(list(detections), width, height)
