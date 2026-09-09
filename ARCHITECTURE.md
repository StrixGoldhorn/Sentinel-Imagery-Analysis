# Architecture

The application follows Clean Architecture. Dependencies point inward:

```text
interfaces ─────┐
infrastructure ──┼──> application -> domain
bootstrap ───────┘
```

`domain` contains immutable business entities and domain validation errors. It has no framework, database, HTTP, or computer-vision dependencies.

`application` contains provider/repository ports, application errors, and use cases. Use cases coordinate behavior but do not know which web framework, API provider, image library, or database is used.

`infrastructure` implements the ports using Copernicus, N2YO, Nominatim, OpenCV, Pillow, SQLite, the filesystem, and AIS provider plugins.

`interfaces` translates HTTP and CLI input into domain values, invokes use cases, and serializes results.

`bootstrap` is the composition root. It reads configuration and injects concrete infrastructure into application use cases.

## Main workflows

```text
POST /api/tasks/scan (Async) & POST /scan (Sync)
  -> CreateScan (Executed via ThreadedTaskQueue or direct)
     -> CopernicusImageryProvider (STAC 1.0.0 Catalog Search + Sentinel Hub Process API)
     -> FilesystemTileCache
     -> PillowImageStitcher (Atomic tile assembly)
     -> NominatimLocationResolver
     -> FilesystemScanRepository

POST /api/run_cv/<scan>
  -> DetectShips
     -> ClassicalShipDetector (Adaptive thresholding, OBB metrology: length, beam, heading)

GET /api/scan/<scan>/crop
  -> Extract cropped vessel radar chip, calculate intensity histogram and stats

POST /api/ingest_ais
  -> IngestAIS
     -> DynamicAISPluginRegistry
     -> SQLiteAISRepository

POST /api/aoi/<id>/predict
  -> PredictAreaOfInterest
     -> Sentinel1MissionAnalyzer (historical acquisition cadence owns pass timing)
     -> N2YOPassPredictor (optional corroboration only)
     -> SQLiteAreaOfInterestRepository

Automatic Pass Scheduler
  -> CheckAndScheduleAOIs
     -> Historical or historically corroborated prediction
     -> BackgroundPassMonitor
     -> TriggerAutomaticAISScrape
        -> IngestAIS
        -> PostPassIngestionJob (idempotently created by the automatic trigger)
     -> IngestPostPassImagery after the fly-past window
        -> exact matched Copernicus acquisition
        -> CreateScan

Manual AOI AIS scrapes and manual imagery scans are independent workflows and do
not create work in the other pipeline.
```

## Database Migrations

SQLite database schema evolution is handled by `MigrationRunner` applying ordered
SQL files from `sentinel_analysis/infrastructure/persistence/migrations/sql/`.
The current schema includes AOIs, AIS telemetry and scraper logs, cached pass
forecasts, durable background tasks, post-pass ingestion jobs, and runtime settings.


`app.py` is the web entry point. The CLI is exposed through `python -m sentinel_analysis`; implementation code belongs under `sentinel_analysis/`.
