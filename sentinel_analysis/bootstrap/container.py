"""Dependency injection container at the outermost application boundary."""

from sentinel_analysis.application.use_cases import (
    AddAreaOfInterest,
    AnalyzeMissionPasses,
    CheckAndScheduleAOIs,
    CreateScan,
    DeleteScan,
    DetectShips,
    GetScan,
    GetVesselPositions,
    IngestAIS,
    ListAreasOfInterest,
    ListScans,
    PredictAreaOfInterest,
    RenameScan,
    ScrapeAreaOfInterestAIS,
)

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.infrastructure.ais.plugin_registry import DynamicAISPluginRegistry
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.geocoding import NominatimLocationResolver
from sentinel_analysis.infrastructure.imagery.cache import FilesystemTileCache
from sentinel_analysis.infrastructure.imagery.copernicus import CopernicusImageryProvider, CopernicusTokenProvider
from sentinel_analysis.infrastructure.imagery.stitching import PillowImageStitcher
from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository
from sentinel_analysis.infrastructure.satellite.hybrid_predictor import HybridPassPredictor
from sentinel_analysis.infrastructure.satellite.n2yo import N2YOPassPredictor
from sentinel_analysis.infrastructure.satellite.s1_analyzer import Sentinel1MissionAnalyzer
from sentinel_analysis.infrastructure.scheduler.pass_scheduler import PassSchedulerWorker
from sentinel_analysis.infrastructure.tasks.queue import ThreadedTaskQueue


class ApplicationContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.scan_repository = FilesystemScanRepository(settings.output_root)
        self.aoi_repository = SQLiteAreaOfInterestRepository(settings.database_path)
        self.ais_repository = SQLiteAISRepository(settings.database_path)
        self.tile_cache = FilesystemTileCache(settings.cache_root)
        self.task_queue = ThreadedTaskQueue()

        token_provider = CopernicusTokenProvider(
            settings.copernicus_username,
            settings.copernicus_password,
        )
        imagery = CopernicusImageryProvider(token_provider, tile_cache=self.tile_cache)
        self.n2yo_predictor = N2YOPassPredictor()
        self.mission_analyzer = Sentinel1MissionAnalyzer(imagery)
        self.hybrid_predictor = HybridPassPredictor(self.n2yo_predictor, self.mission_analyzer)

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
        self.delete_scan = DeleteScan(self.scan_repository)
        self.list_aois = ListAreasOfInterest(self.aoi_repository)
        self.add_aoi = AddAreaOfInterest(self.aoi_repository)
        self.predict_aoi = PredictAreaOfInterest(
            self.aoi_repository,
            self.hybrid_predictor,
            self.mission_analyzer,
            self.n2yo_predictor,
        )

        self.analyze_mission_passes = AnalyzeMissionPasses(
            self.aoi_repository,
            self.mission_analyzer,
        )
        self.ingest_ais = IngestAIS(DynamicAISPluginRegistry(), self.ais_repository)
        self.get_vessels = GetVesselPositions(self.ais_repository)
        self.scrape_aoi_ais = ScrapeAreaOfInterestAIS(self.aoi_repository, self.ingest_ais)
        self.schedule_aois = CheckAndScheduleAOIs(
            self.aoi_repository,
            self.hybrid_predictor,
            self.create_scan,
            self.ingest_ais,
        )
        self.pass_scheduler = PassSchedulerWorker(
            self.schedule_aois,
            settings.n2yo_api_key or "default_key",
            poll_interval_seconds=60.0,
        )
