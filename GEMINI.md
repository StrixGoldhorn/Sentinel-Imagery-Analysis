# Project Overview

**Sentinel Imagery Analysis** is a Python-based web application and toolset designed to automate the detection of ships in Synthetic Aperture Radar (SAR) imagery. It leverages classical computer vision techniques to count ships and draw bounding boxes around them. The project integrates with the Copernicus Data Space Ecosystem to fetch SAR imagery and the N2YO API to predict future satellite passes (specifically Sentinel-1A).

**Key Technologies:**
- **Backend**: Python (Flask framework)
- **Computer Vision**: Classical CV techniques (used in `basic_classical_cv.py`)
- **APIs**: Copernicus Data Space Ecosystem (SAR imagery), Nominatim (Reverse Geocoding), N2YO (Satellite pass prediction)
- **Data Storage**: SQLite (`data.db` for Areas of Interest) and local filesystem for processed images.
- **Frontend**: HTML/CSS/JS (Templates in `templates/`, assets in `static/`)

# Architecture & Key Files

- `app.py`: The core Flask application server. It provides endpoints for scanning areas, managing Areas of Interest (AOI), and reviewing processed scans in a gallery.
- `basic_classical_cv.py`: Implements the classical computer vision logic for ship detection. Supports optional DEM (Digital Elevation Model) grayscale masking to prevent false positives on landmasses.
- `predict_scans.py`: Uses the N2YO API to predict upcoming Sentinel-1 satellite passes over a designated bounding box.
- `copernicus_get_image.py` / `get_images_area.py`: Scripts responsible for authenticating and downloading SAR imagery from Copernicus.
- `data.db`: SQLite database (auto-generated on startup) that stores tracked AOI configurations.
- `.env`: Expected configuration file containing API credentials (`COP_USERNAME`, `COP_PASSWORD`, `N2YO_API_KEY`). You can use `.exampleenv` as a template.

# Building and Running

**Prerequisites:**
1. Ensure your Python virtual environment (e.g., `.venv`) is active and dependencies (Flask, requests, Pillow, python-dotenv, etc.) are installed. You must only run this in the virtual environment.
2. Create a `.env` file by copying `.exampleenv` and populating it with your Copernicus and N2YO API credentials.

**Running the Web Application:**
```bash
python app.py
```
*The Flask application will start in debug mode on `http://127.0.0.1:5000`.*

**Running CLI Tools Standalone:**

To detect ships in a specific image via CLI:
```bash
python basic_classical_cv.py <image_path> [--dem <dem_image_path>] [--output <output_path>]
```

To predict the next satellite scans for a bounding box via CLI:
```bash
python predict_scans.py --bbox <MIN_LON> <MIN_LAT> <MAX_LON> <MAX_LAT>
```

# Development Conventions

- **Environment Management:** Uses `.env` for secrets and credentials. Do not commit `.env` or `data.db`.
- **Data and Outputs:** Scanned images and metadata are dynamically generated and stored in `static/output/`. SQLite is used for lightweight persistent storage of areas of interest.
- **AI Tooling Notice:** Parts of the boilerplate and CLI structure were generated using LLMs (Qwen3.7-Plus), with specific algorithm implementations crafted or structured separately.
- **Paths:** Relies heavily on Python's `pathlib` for cross-platform file path management.