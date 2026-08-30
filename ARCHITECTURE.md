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
     -> N2YOPassPredictor
     -> SQLiteAreaOfInterestRepository

Automatic Pass Scheduler
  -> CheckAndScheduleAOIs
     -> N2YOPassPredictor
     -> CreateScan (Triggered automatically on upcoming pass)
```

## Database Migrations

SQLite database schema evolution is handled by `MigrationRunner` applying versioned migrations from `sentinel_analysis/infrastructure/persistence/migrations/`:
- `001_initial_schema.py`: Core tables for AOIs and AIS telemetry.
- `002_add_indexes.py`: Spatial and temporal indexes for query performance.
- `003_add_auto_capture_to_aoi.py`: Adds `auto_capture_enabled` flag for scheduled scan capture.
- `004_add_polygon_coords_to_aoi.py`: Stores arbitrary polygon geometry for complex maritime zones.


`app.py` is the web entry point. The CLI is exposed through `python -m sentinel_analysis`; implementation code belongs under `sentinel_analysis/`.
