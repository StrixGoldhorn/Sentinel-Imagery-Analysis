# Architecture

The application follows Clean Architecture. Dependencies point inward:

```text
interfaces -> bootstrap -> infrastructure -> application -> domain
```

`domain` contains immutable business entities and expected application errors. It has no framework, database, HTTP, or computer-vision dependencies.

`application` contains provider/repository ports and use cases. Use cases coordinate behavior but do not know which web framework, API provider, image library, or database is used.

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

Top-level Python files are compatibility entry points. New implementation code belongs under `sentinel_analysis/`.

