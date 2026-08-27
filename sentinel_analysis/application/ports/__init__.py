"""Application-owned boundaries for providers and persistence."""

from sentinel_analysis.application.ports.ais import AISPlugin, AISPluginRegistry, AISTimeRange
from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.detection import DetectionResult, ShipDetector
from sentinel_analysis.application.ports.geocoding import LocationResolver
from sentinel_analysis.application.ports.imagery import ImageStitcher, ImageryProvider, TileImage
from sentinel_analysis.application.ports.satellite import PassPrediction, PassPredictor
from sentinel_analysis.application.ports.scan_repository import ScanRepository

__all__ = [
    "AISPlugin",
    "AISPluginRegistry",
    "AISRepository",
    "AISTimeRange",
    "AreaOfInterestRepository",
    "DetectionResult",
    "ImageStitcher",
    "ImageryProvider",
    "LocationResolver",
    "PassPrediction",
    "PassPredictor",
    "ScanRepository",
    "ShipDetector",
    "TileImage",
]
