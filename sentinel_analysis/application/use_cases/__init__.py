"""Application use cases."""

from sentinel_analysis.application.use_cases.annotate_tiles import AnnotationSummary, BatchAnnotateTiles
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.application.use_cases.manage_aois import (
    AddAreaOfInterest,
    ListAreasOfInterest,
    PredictAreaOfInterest,
)
from sentinel_analysis.application.use_cases.manage_scans import GetScan, ListScans, RenameScan
from sentinel_analysis.application.use_cases.predict_passes import PredictPasses

__all__ = [
    "AddAreaOfInterest",
    "AnnotationSummary",
    "BatchAnnotateTiles",
    "CreateScan",
    "DetectShips",
    "GetScan",
    "IngestAIS",
    "ListAreasOfInterest",
    "ListScans",
    "PredictAreaOfInterest",
    "PredictPasses",
    "RenameScan",
]
