"""Computer-vision adapters."""

from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.detection.detection_saver import save_detection_results

__all__ = ["ClassicalShipDetector", "save_detection_results"]
