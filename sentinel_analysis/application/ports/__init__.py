"""Application-owned boundaries for providers and persistence."""

from sentinel_analysis.application.ports.ais import AISPlugin, AISPluginRegistry, AISTimeRange
from sentinel_analysis.application.ports.ais_repository import AISAnalyticsRepository, AISRepository
from sentinel_analysis.application.ports.annotation import (
    AnnotationEditor,
    AnnotationProgress,
    AnnotationProgressRepository,
    AnnotationTile,
    AnnotationTileSource,
)
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.cache import TileCache
from sentinel_analysis.application.ports.detection import DetectionResult, ShipDetector
from sentinel_analysis.application.ports.briefing import IntelligenceBriefGenerator
from sentinel_analysis.application.ports.geocoding import LocationResolver
from sentinel_analysis.application.ports.imagery import GeoTIFFWriter, ImageStitcher, ImageryProvider, TileImage
from sentinel_analysis.application.ports.optical import (
    OpticalCrossValidator,
    OpticalScene,
    OpticalValidationResult,
)
from sentinel_analysis.application.ports.sar_change_detector import SARChangeDetector
from sentinel_analysis.application.ports.satellite import PassPrediction, PassPredictor
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.application.ports.settings_repository import SettingsRepository
from sentinel_analysis.application.ports.task_queue import TaskQueue

__all__ = [
    "AISPlugin",
    "AISPluginRegistry",
    "AISAnalyticsRepository",
    "AISRepository",
    "AISTimeRange",
    "AnnotationEditor",
    "AnnotationProgress",
    "AnnotationProgressRepository",
    "AnnotationTile",
    "AnnotationTileSource",
    "AreaOfInterestRepository",
    "DetectionResult",
    "GeoTIFFWriter",
    "ImageStitcher",
    "ImageryProvider",
    "IntelligenceBriefGenerator",
    "LocationResolver",
    "OpticalCrossValidator",
    "OpticalScene",
    "OpticalValidationResult",
    "PassPrediction",
    "PassPredictor",
    "PostPassIngestionRepository",
    "SARChangeDetector",
    "ScanRepository",
    "SettingsRepository",
    "ShipDetector",
    "TaskQueue",
    "TileCache",
    "TileImage",
]
