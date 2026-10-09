# Sentinel Imagery Analysis

<p align="center">
  <strong>Automated Maritime Domain Awareness, SAR Dark Vessel Interdiction & Multi-Sensor Intelligence Platform</strong>
</p>

<p align="center">
  <a href="https://github.com/username/Sentinel-Imagery-Analysis/actions/workflows/ci.yml"><img src="https://img.shields.io/badge/CI-Passing-brightgreen.svg?style=flat-square" alt="CI Status"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg?style=flat-square" alt="Python Versions"></a>
  <a href="ARCHITECTURE.md"><img src="https://img.shields.io/badge/Architecture-Clean%20Architecture-teal.svg?style=flat-square" alt="Architecture"></a>
  <a href="https://username.github.io/Sentinel-Imagery-Analysis/"><img src="https://img.shields.io/badge/Documentation-GitHub%20Pages-blueviolet.svg?style=flat-square" alt="Docs"></a>
  <a href="#license"><img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License"></a>
</p>

---

## 📖 Executive Overview

**Sentinel Imagery Analysis** is an enterprise-grade maritime intelligence platform engineered to detect, classify, and track non-cooperative vessels ("dark ships") across global ocean waters. By fusing **European Space Agency (ESA) Copernicus Sentinel-1 Synthetic Aperture Radar (SAR)** imagery, **Sentinel-2 optical** passes, **Automatic Identification System (AIS)** telemetry, hydrodynamic wake kinematics, and sovereign geospatial geofences, the platform delivers autonomous, all-weather maritime domain awareness (MDA).

Whether uncovering illicit sanction-evading Ship-to-Ship (STS) transshipments, tracking AIS spoofing and ghost shipping, or patrolling Marine Protected Areas (MPAs) for illegal fishing (IUU), Sentinel Imagery Analysis transforms complex satellite observations into real-time operational intelligence.

---

## 🗺️ System Architecture

The application strictly adheres to **Clean Architecture** principles, maintaining decoupled layers where domain business rules are isolated from databases, web frameworks, and external APIs:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             INTERFACES LAYER                                │
│   Flask Web Application (C2 Canvas)   •   REST API   •   CLI Toolset        │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                             APPLICATION LAYER                               │
│  DetectShips  •  DetectTransshipment  •  CrossValidateOptical               │
│  DetectSARChanges  •  ExportGeospatial (KMZ/CoT)  •  GenerateBriefing (PDF) │
│  IngestAIS  •  DispatchAlerts  •  PredictAOI  •  PostAcquisitionPipeline    │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                            INFRASTRUCTURE LAYER                             │
│  Copernicus Data Space (SAR/Opt)   •   Radon Wake Kinematics & CFAR CV      │
│  Dual-Pol (VV+VH) Clutter Engine   •   Nautical Chart Engine (OpenSeaMap)   │
│  SQLite Spatiotemporal Repositories•   Webhook Dispatchers (Slack/Teams)    │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                                DOMAIN LAYER                                 │
│  VesselDetection  •  AISContact  •  Scan  •  AreaOfInterest  •  Metrology   │
└─────────────────────────────────────────────────────────────────────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) and [SYSTEM_ARCHITECTURE_REPORT.md](SYSTEM_ARCHITECTURE_REPORT.md) for architectural boundary definitions.

---

## ⚡ Core Capabilities

| Capability | Technical Approach | Operational Value |
|---|---|---|
| **All-Weather SAR Detection** | Sentinel-1 C-band GRD (IW mode) with CFAR adaptive thresholding and SRTM DEM land masking. | Uninterrupted 24/7 day/night surveillance through clouds, rain, and storms. |
| **Dual-Pol (VV+VH) Clutter Rejection** | Normalized cross-ratio $\sigma^\circ_{VH} / \sigma^\circ_{VV}$ suppression. | Suppresses sea clutter up to 18 dB in rough sea states (Beaufort 5+) while preserving depolarized steel ship hull returns. |
| **Dark Vessel Interdiction** | Spatiotemporal kinematic gating between radar detections and broadcast AIS positions. | Flags non-reporting vessels violating IMO SOLAS Chapter V, Reg 19 mandatory carriage requirements. |
| **Hydrodynamic Wake Kinematics** | Radon and Hough transform spectral analysis of Kelvin wake arms ($19.5^\circ$) and turbulent centerlines. | Measures true vessel speed-through-water ($V_{\text{water}}$) and resolves $180^\circ$ heading ambiguity. |
| **AIS Spoofing Detection** | Discrepancy checks comparing broadcast AIS SOG/COG against radar wake physics. | Unmasks vessels broadcasting false coordinates, forged headings, or claiming anchored while underway. |
| **STS Transshipment Detection** | Pairwise proximity clustering ($< 500\text{ m}$) and drift velocity matching ($< 2\text{ kn}$). | Uncovers clandestine dark fleet oil and cargo transfers in offshore waters. |
| **EEZ & MPA Geofencing** | Ray-casting point-in-polygon checks against sovereign maritime economic zones and reserves. | Automated alerts for Illegal, Unreported, and Unregulated (IUU) fishing and unauthorized sovereign incursions. |
| **Sentinel-2 Optical Cross-Validation** | Coincident optical pass search, NDWI water masking, and true-color RGB chip generation. | Provides visual verification of vessel superstructure, deck cargo, and vessel color. |
| **Repeat-Pass SAR Coherence Change** | Multi-temporal log-ratio amplitude differencing across orbital revisit cycles. | Categorizes port and anchorage traffic: new arrivals, departures, and long-term anchored contacts. |
| **MIL-STD-2525 Cursor-on-Target (CoT)** | Real-time XML streaming compliant with DoD and NATO CoT schema. | Direct tactical situational awareness streaming into ATAK, WinTAK, and FalconView. |
| **Google Earth KMZ Packages** | Packaged archive with calibrated SAR rasters, 3D vectors, and inspection placemarks. | Seamless 3D globe briefing and offline operational sharing. |
| **Executive PDF Dossier Briefings** | High-resolution PDF with OpenSeaMap nautical chart overlays, dark vessel dossiers, and route corridors. | Publication-grade operational briefs ready for boarding crews and commanding officers. |

---

## 🚀 Quickstart & Installation

### Prerequisites
- **Python 3.11** or **3.12**
- Free account on the [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/)

### Setup

```bash
# 1. Clone repository
git clone https://github.com/username/Sentinel-Imagery-Analysis.git
cd Sentinel-Imagery-Analysis

# 2. Create virtual environment
python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Configuration (`.env`)

Copy `.env.example` to `.env` and fill in your credentials:

```ini
# Copernicus Data Space Ecosystem Credentials
COP_USERNAME=your_copernicus_username
COP_PASSWORD=your_copernicus_password

# Tactical Alerting Webhook (Optional)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
TEAMS_WEBHOOK_URL=https://your-org.webhook.office.com/...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Storage & Server Settings
DATABASE_PATH=data.db
OUTPUT_ROOT=output
PORT=5050
```

---

## 🖥️ Tactical Web C2 Application

Start the web application:

```bash
python app.py
```
Open your browser to: **`http://127.0.0.1:5050`**

### C2 Interface Highlights
1. **Tactical Quick-Filter Bar**:
   - `[All Contacts]` • `[🚨 Dark Vessels]` • `[⚠️ SOLAS Suspect]` • `[🟣 AIS Spoofed]` • `[🟡 STS Risk]` • `[🛰️ Optical Match]`
   - Instantly halos and spotlights priority targets while dimming background traffic.
2. **Contact Telemetry Dossier Drawer**:
   - Click any contact to slide open detailed telemetry: high-resolution SAR radar chip preview, physical length/beam, IMO vessel class, range rings to nearest AIS traffic, wake velocity, and optical cross-validation status.
3. **One-Click Exports**:
   - Download Google Earth `.kmz` bundles, stream ATAK `.cot` XML, or generate multi-page PDF briefing dossiers directly from the UI.
4. **Scan Gallery (`/gallery`)**:
   - Browse historical scans, review detection statistics, and view standalone nautical route corridor charts.

---

## 💻 Command-Line Interface (CLI)

The CLI supports headless batch processing and server automation:

```bash
# Detect vessels in an existing SAR image
python -m sentinel_analysis detect input_sar.png --dem dem.png --output detections.jpg

# Download & stitch recent Sentinel-1 imagery for a bounding box
python -m sentinel_analysis download --bbox 103.6 1.1 104.2 1.5 --output-dir output

# Predict next satellite overpasses for an Area of Interest
python -m sentinel_analysis predict --bbox 103.6 1.1 104.2 1.5 --days 14

# Ingest AIS telemetry matching a bounding box
python -m sentinel_analysis ingest --bbox 103.6 1.1 104.2 1.5 --lookback-hours 6

# Interactive ground-truth bounding box annotator
python -m sentinel_analysis annotate path/to/tiles
```

---

## 🌐 Tactical Interoperability & REST API

### Key Endpoints

| Endpoint | Method | Output | Description |
|---|---|---|---|
| `/api/scan/<folder>/export/kmz` | `GET` | `.kmz` | Complete georeferenced Google Earth archive. |
| `/api/scan/<folder>/export/cot` | `GET` | XML | MIL-STD-2525 Cursor-on-Target feed for ATAK/WinTAK. |
| `/api/scan/<folder>/briefing` | `GET` | PDF | Multi-page executive intelligence dossier. |
| `/api/scan/<folder>/route_correlation_image` | `GET` | PNG | Standalone nautical chart route corridor plot. |
| `/api/scan/<folder>/crop?idx=0` | `GET` | PNG | High-res cropped radar chip for target index. |
| `/api/run_cv/<folder>` | `POST` | JSON | Triggers full CV detection, wake physics & correlation. |
| `/api/tasks/scan` | `POST` | JSON | Asynchronously initiates an imagery acquisition task. |

---

## 📚 Complete User Guide on GitHub Pages

A comprehensive, multi-section operational manual is published with **GitHub Pages**:

- 🚀 [**Getting Started & Installation**](https://username.github.io/Sentinel-Imagery-Analysis/getting-started.html)
- 🗺️ [**Tactical C2 Web Interface Guide**](https://username.github.io/Sentinel-Imagery-Analysis/c2-web-interface.html)
- 🛰️ [**Maritime Intelligence & Anomaly Pipeline**](https://username.github.io/Sentinel-Imagery-Analysis/maritime-intelligence.html)
- ⚡ [**Sensor Processing, Radar Physics & CV**](https://username.github.io/Sentinel-Imagery-Analysis/sensor-physics-cv.html)
- 📑 [**Reporting & Interoperability Exports (KMZ/CoT)**](https://username.github.io/Sentinel-Imagery-Analysis/reporting-and-exports.html)
- 💻 [**CLI & REST API Reference**](https://username.github.io/Sentinel-Imagery-Analysis/cli-and-api.html)

### Deploying the User Guide to GitHub Pages

1. Navigate to your repository on GitHub.
2. Go to **Settings** > **Pages**.
3. Under **Build and deployment**:
   - **Option A (Recommended)**: Set **Source** to **GitHub Actions**. The included `.github/workflows/pages.yml` workflow will automatically build and publish the Jekyll site upon push.
   - **Option B**: Set **Source** to **Deploy from a branch**, select branch `main`, and folder `/docs`.
4. Your documentation will be live at `https://<username>.github.io/<repo-name>/`.

---

## 🧪 Verification & Test Suite

The project includes a comprehensive unit and integration test suite with 100% test pass rate across all domains and ports:

```bash
python -m unittest discover tests -v
```

```text
Ran 538 tests in 84.317s
OK (failures=0, errors=0)
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
