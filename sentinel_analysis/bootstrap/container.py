"""Dependency injection container at the outermost application boundary."""

from sentinel_analysis.application.use_cases import (
    AddAreaOfInterest,
    CreateScan,
    DetectShips,
    GetScan,
    IngestAIS,
    ListAreasOfInterest,
    ListScans,
    PredictAreaOfInterest,
    RenameScan,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.geocoding import NominatimLocationResolver
from sentinel_analysis.infrastructure.imagery.copernicus import CopernicusImageryProvider, CopernicusTokenProvider
from sentinel_analysis.infrastructure.imagery.stitching import PillowImageStitcher
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.infrastructure.satellite.n2yo import N2YOPassPredictor


class ApplicationContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.scan_repository = FilesystemScanRepository(settings.output_root)
        self.aoi_repository = SQLiteAreaOfInterestRepository(settings.database_path)
        self.ais_repository = SQLiteAISRepository(settings.database_path)

        token_provider = CopernicusTokenProvider(
            settings.copernicus_username,
            settings.copernicus_password,
        )
        imagery = CopernicusImageryProvider(token_provider)
        predictor = N2YOPassPredictor()

        self.create_scan = CreateScan(
            imagery,
            PillowImageStitcher(),
            self.scan_repository,
            NominatimLocationResolver(),
        )
        self.detect_ships = DetectShips(ClassicalShipDetector())
        self.get_scan = GetScan(self.scan_repository)
        self.list_scans = ListScans(self.scan_repository)
        self.rename_scan = RenameScan(self.scan_repository)
        self.list_aois = ListAreasOfInterest(self.aoi_repository)
        self.add_aoi = AddAreaOfInterest(self.aoi_repository)
        self.predict_aoi = PredictAreaOfInterest(self.aoi_repository, predictor)
        self.ingest_ais = IngestAIS(DynamicAISPluginRegistry(), self.ais_repository)
