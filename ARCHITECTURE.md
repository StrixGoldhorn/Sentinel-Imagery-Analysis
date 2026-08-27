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
POST /scan
  -> CreateScan
     -> CopernicusImageryProvider
     -> PillowImageStitcher
     -> NominatimLocationResolver
     -> FilesystemScanRepository

POST /api/run_cv/<scan>
  -> DetectShips
     -> ClassicalShipDetector

POST /api/ingest_ais
  -> IngestAIS
     -> DynamicAISPluginRegistry
     -> SQLiteAISRepository

POST /api/aoi/<id>/predict
  -> PredictAreaOfInterest
     -> N2YOPassPredictor
     -> SQLiteAreaOfInterestRepository
```

`app.py` is the web entry point. The CLI is exposed through `python -m sentinel_analysis`; implementation code belongs under `sentinel_analysis/`.
