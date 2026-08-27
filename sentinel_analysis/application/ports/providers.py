"""Compatibility imports for the responsibility-specific provider ports.

New application code should import from the focused port module. This facade
keeps existing use cases stable until their dedicated refactoring step.
"""

from sentinel_analysis.application.ports.ais import AISPlugin, AISPluginRegistry, AISTimeRange
from sentinel_analysis.application.ports.detection import DetectionResult, ShipDetector
from sentinel_analysis.application.ports.geocoding import LocationResolver
from sentinel_analysis.application.ports.imagery import ImageStitcher, ImageryProvider, TileImage
from sentinel_analysis.application.ports.satellite import PassPrediction, PassPredictor

__all__ = [
    "AISPlugin",
    "AISPluginRegistry",
    "AISTimeRange",
    "DetectionResult",
    "ImageStitcher",
    "ImageryProvider",
    "LocationResolver",
    "PassPrediction",
    "PassPredictor",
    "ShipDetector",
    "TileImage",
]
