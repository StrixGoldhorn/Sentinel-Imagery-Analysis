"""Dependency injection container at the outermost application boundary."""

from sentinel_analysis.application.use_cases import (
    AddAreaOfInterest,
    AnalyzeMissionPasses,
    CheckAndScheduleAOIs,
    CreateScan,
    DeleteAreaOfInterest,
    DeleteScan,
    DetectShips,
    GenerateDEM,
    GetScan,
    GetScraperDetail,
    GetScraperLogsUseCase,
    GetSettings,
    GetUpcomingScrapes,
    GetVesselDetails,
    GetVesselPositions,
    IngestAIS,
    IngestPostPassImagery,
    ListAreasOfInterest,
    ListScans,
    ListScrapers,
    PredictAreaOfInterest,
    RenameScan,
    ResetScraperCooldown,
    ResetSettings,
    ScrapeAreaOfInterestAIS,
    ToggleScraper,
    UpdateScraper,
    UpdateScraperConfig,
    UpdateSettings,
    UpdateVesselDetails,
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
from sentinel_analysis.infrastructure.persistence.sqlite_post_pass import SQLitePostPassIngestionRepository
from sentinel_analysis.infrastructure.persistence.sqlite_settings import SQLiteSettingsRepository
from sentinel_analysis.infrastructure.satellite.hybrid_predictor import HybridPassPredictor
from sentinel_analysis.infrastructure.satellite.n2yo import N2YOPassPredictor
from sentinel_analysis.infrastructure.satellite.s1_analyzer import Sentinel1MissionAnalyzer
from sentinel_analysis.infrastructure.scheduler.pass_monitor import BackgroundPassMonitor
from sentinel_analysis.infrastructure.scheduler.pass_scheduler import PassSchedulerWorker
from sentinel_analysis.infrastructure.tasks.queue import ThreadedTaskQueue


class ApplicationContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.scan_repository = FilesystemScanRepository(settings.output_root)
        self.aoi_repository = SQLiteAreaOfInterestRepository(settings.database_path)
        self.ais_repository = SQLiteAISRepository(settings.database_path)
        self.post_pass_repository = SQLitePostPassIngestionRepository(settings.database_path)
        self.settings_repository = SQLiteSettingsRepository(settings.database_path)
        self.tile_cache = FilesystemTileCache(settings.cache_root)
        self.task_queue = ThreadedTaskQueue()

        token_provider = CopernicusTokenProvider(
            settings.copernicus_username,
            settings.copernicus_password,
        )
        self.imagery = CopernicusImageryProvider(token_provider, tile_cache=self.tile_cache)
        self.stitcher = PillowImageStitcher()
        self.n2yo_predictor = N2YOPassPredictor()
        self.mission_analyzer = Sentinel1MissionAnalyzer(self.imagery)
        self.hybrid_predictor = HybridPassPredictor(
            self.n2yo_predictor,
            self.mission_analyzer,
            settings_repo=self.settings_repository,
        )

        self.generate_dem = GenerateDEM(self.imagery, self.stitcher)
        self.create_scan = CreateScan(
            self.imagery,
            self.stitcher,
            self.scan_repository,
            NominatimLocationResolver(),
        )
        self.detect_ships = DetectShips(ClassicalShipDetector(settings_repo=self.settings_repository))
        self.get_scan = GetScan(self.scan_repository)
        self.list_scans = ListScans(self.scan_repository)
        self.rename_scan = RenameScan(self.scan_repository)
        self.delete_scan = DeleteScan(self.scan_repository)
        self.list_aois = ListAreasOfInterest(self.aoi_repository)
        self.add_aoi = AddAreaOfInterest(self.aoi_repository)
        self.delete_aoi = DeleteAreaOfInterest(self.aoi_repository)
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
        self.ais_plugin_registry = DynamicAISPluginRegistry()
        self.ingest_ais = IngestAIS(self.ais_plugin_registry, self.ais_repository)
        self.list_scrapers = ListScrapers(self.ais_plugin_registry, self.ais_repository)
        self.get_scraper_detail = GetScraperDetail(self.ais_plugin_registry, self.ais_repository)
        self.toggle_scraper = ToggleScraper(self.ais_plugin_registry, self.ais_repository)
        self.update_scraper = UpdateScraper(self.ais_plugin_registry, self.ais_repository)
        self.update_scraper_config = UpdateScraperConfig(self.ais_plugin_registry, self.ais_repository)
        self.reset_scraper_cooldown = ResetScraperCooldown(self.ais_plugin_registry, self.ais_repository)
        self.get_scraper_logs_use_case = GetScraperLogsUseCase(self.ais_repository)
        self.get_vessels = GetVesselPositions(self.ais_repository)
        self.get_vessel_details = GetVesselDetails(self.ais_repository)
        self.update_vessel_details = UpdateVesselDetails(self.ais_repository)
        self.scrape_aoi_ais = ScrapeAreaOfInterestAIS(self.aoi_repository, self.ingest_ais)
        self.ingest_post_pass = IngestPostPassImagery(
            self.post_pass_repository,
            self.aoi_repository,
            self.imagery,
            self.create_scan,
            self.detect_ships,
        )
        self.pass_monitor = BackgroundPassMonitor(
            self.ingest_ais,
            self.post_pass_repository,
        )
        self.schedule_aois = CheckAndScheduleAOIs(
            self.aoi_repository,
            self.hybrid_predictor,
            self.create_scan,
            self.ingest_ais,
            self.post_pass_repository,
            self.ingest_post_pass,
            settings_repo=self.settings_repository,
            pass_monitor=self.pass_monitor,
        )
        self.get_upcoming_scrapes = GetUpcomingScrapes(
            self.aoi_repository,
            self.hybrid_predictor,
            settings_repo=self.settings_repository,
        )
        scheduler_aoi_interval = 30.0
        scheduler_sar_interval = 3600.0
        if hasattr(self, "settings_repository") and self.settings_repository is not None:
            saved_aoi_interval = self.settings_repository.get("aoi_check_interval_seconds")
            if saved_aoi_interval is not None:
                try:
                    scheduler_aoi_interval = float(saved_aoi_interval)
                except (ValueError, TypeError):
                    pass
            saved_sar_interval = self.settings_repository.get("sar_scan_interval_seconds")
            if saved_sar_interval is None:
                saved_sar_interval = self.settings_repository.get("poll_interval_seconds")
            if saved_sar_interval is not None:
                try:
                    scheduler_sar_interval = float(saved_sar_interval)
                except (ValueError, TypeError):
                    pass

        self.pass_scheduler = PassSchedulerWorker(
            self.schedule_aois,
            settings.n2yo_api_key or "default_key",
            poll_interval_seconds=scheduler_sar_interval,
            post_pass_repo=self.post_pass_repository,
            settings_repo=self.settings_repository,
            pass_monitor=self.pass_monitor,
            ingest_post_pass=self.ingest_post_pass,
            aoi_check_interval_seconds=scheduler_aoi_interval,
            sar_scan_interval_seconds=scheduler_sar_interval,
        )

        self.get_settings = GetSettings(self.settings_repository)
        self.update_settings = UpdateSettings(self.settings_repository)
        self.reset_settings = ResetSettings(self.settings_repository)



