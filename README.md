# Sentinel Imagery Analysis

A Flask application and CLI toolset for downloading Sentinel-1 Synthetic Aperture Radar imagery, detecting candidate vessels with classical computer vision, tracking areas of interest, predicting satellite passes, and ingesting AIS telemetry.

## AI Usage Disclaimer

Lots of AI usage. Pretty much everything is clanked, other than the classical CV portion. Software architecture was described to Codex and went through several iterations with human, but ultimately the code was clanked.

Rough flow of data and processing was described to Codex, but left up to the clanker to implement in code.

## Architecture

The implementation uses Clean Architecture. Business entities and use cases are isolated from Flask, SQLite, OpenCV, Pillow, and external APIs. See [ARCHITECTURE.md](ARCHITECTURE.md) for the package boundaries and workflows.

## Setup

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.exampleenv` to `.env` and configure:

```text
COP_USERNAME=<Copernicus username>
COP_PASSWORD=<Copernicus password>
N2YO_API_KEY=<N2YO API key>
```

Optional settings include `DATABASE_PATH`, `OUTPUT_ROOT`, `PORT`, and `FLASK_DEBUG`.

## Run the web application

```powershell
python app.py
```

The default address is `http://127.0.0.1:5050`.

## Command-line tools

Detect vessels in an existing SAR image:

```powershell
python -m sentinel_analysis detect image.png --dem optional_dem.png --output detections.jpg
```

Download and stitch recent Sentinel-1 imagery:

```powershell
python -m sentinel_analysis download --bbox MIN_LON MIN_LAT MAX_LON MAX_LAT --output-dir output
```

Predict Sentinel-1 passes:

```powershell
python -m sentinel_analysis predict --bbox MIN_LON MIN_LAT MAX_LON MAX_LAT
```

Run AIS ingestion:

```powershell
python -m sentinel_analysis ingest --bbox MIN_LON MIN_LAT MAX_LON MAX_LAT
```

Interactively annotate downloaded SAR tiles:

```powershell
python -m sentinel_analysis annotate path\to\tiles
```

## Tests

The test suite is based on the Python standard library and does not contact external APIs:

```powershell
python -m unittest discover -v
```

The classical detector is heuristic. Its output represents candidate vessels, not confirmed vessel identities.
