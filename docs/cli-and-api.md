---
layout: default
title: CLI & REST API Reference | Sentinel Imagery Analysis
---

# CLI & REST API Reference

The Sentinel Imagery Analysis platform provides both a robust Command-Line Interface (CLI) for headless server scripting and a complete REST API for integrating into larger enterprise or defense Command and Control (C2) pipelines.

---

## 💻 1. Command-Line Interface (CLI)

The CLI suite is invoked using `python -m sentinel_analysis <command>` from the project root.

### Vessel Detection (`detect`)
Detect vessels in a local SAR image using classical CFAR or dual-pol models:

```bash
python -m sentinel_analysis detect input_sar.png \
    --dem optional_dem.png \
    --output detections.jpg \
    --threshold 12.5 \
    --polarization VV+VH
```

### Download Imagery (`download`)
Search, download, and stitch Sentinel-1 imagery for a bounding box:

```bash
python -m sentinel_analysis download \
    --bbox 103.6 1.1 104.2 1.5 \
    --output-dir output \
    --start-date 2026-10-01 \
    --end-date 2026-10-09
```

### Satellite Pass Prediction (`predict`)
Compute next acquisition windows for an Area of Interest based on orbital mechanics:

```bash
python -m sentinel_analysis predict \
    --bbox 103.6 1.1 104.2 1.5 \
    --days 14
```

### Ingest AIS Telemetry (`ingest`)
Query configured AIS providers for vessel positions matching a geographic bounding box:

```bash
python -m sentinel_analysis ingest \
    --bbox 103.6 1.1 104.2 1.5 \
    --lookback-hours 6
```

### Interactive Tile Annotation (`annotate`)
Launch the interactive ground-truth bounding box annotator for ML training data generation:

```bash
python -m sentinel_analysis annotate path/to/tiles
```

---

## 🌐 2. REST API Reference

The web service runs by default on port `5050` and provides JSON and binary endpoints.

### Scans & Detection Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/tasks/scan` | `POST` | Asynchronously enqueue an acquisition scan for an AOI. |
| `/scan` | `POST` | Synchronously trigger immediate Sentinel-1 image acquisition. |
| `/api/run_cv/<folder_name>` | `POST` | Execute CV detection pipeline, wake kinematics, and AIS correlation. |
| `/api/scan/<folder_name>/crop` | `GET` | Retrieve high-res cropped radar chip for a detection index. |
| `/api/scan/<folder_name>/export/kmz` | `GET` | Download georeferenced Google Earth `.kmz` package. |
| `/api/scan/<folder_name>/export/cot` | `GET` | Stream MIL-STD-2525 Cursor-on-Target XML event feed. |
| `/api/scan/<folder_name>/briefing` | `GET` | Download publication-grade PDF Intelligence Briefing. |
| `/api/scan/<folder_name>/route_correlation_image`| `GET` | Download standalone high-res nautical route corridor chart. |

---

### Request & Response Examples

#### 1. Trigger Computer Vision & Intelligence Analysis
```bash
curl -X POST http://127.0.0.1:5050/api/run_cv/S1A_IW_GRDH_20261009T051522 \
     -H "Content-Type: application/json"
```

**Response (`200 OK`):**
```json
{
  "status": "success",
  "scan_id": "S1A_IW_GRDH_20261009T051522",
  "detections_count": 42,
  "dark_vessels_count": 14,
  "solas_violations_count": 8,
  "sts_rendezvous_count": 2,
  "spoofed_vessels_count": 2,
  "briefing_pdf_url": "/api/scan/S1A_IW_GRDH_20261009T051522/briefing",
  "kmz_export_url": "/api/scan/S1A_IW_GRDH_20261009T051522/export/kmz",
  "cot_export_url": "/api/scan/S1A_IW_GRDH_20261009T051522/export/cot"
}
```

#### 2. Stream Cursor-on-Target (CoT) XML for ATAK
```bash
curl http://127.0.0.1:5050/api/scan/S1A_IW_GRDH_20261009T051522/export/cot
```

**Response (`200 OK`, `application/xml`):**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<events>
  <event version="2.0" uid="SAR-DET-004" type="a-h-G-E-V" time="2026-10-09T05:15:22Z" ...>
    <point lat="1.2845" lon="103.8512" hae="0" ce="15" le="10"/>
    <detail>
      <contact callsign="DARK-VESSEL-004"/>
      <track speed="7.1" course="042.5"/>
    </detail>
  </event>
</events>
```

#### 3. Test Webhook Alert Dispatching
```bash
curl -X POST http://127.0.0.1:5050/api/alerts/test \
     -H "Content-Type: application/json" \
     -d '{"webhook_type": "slack"}'
```

**Response (`200 OK`):**
```json
{
  "status": "dispatched",
  "adapter": "slack",
  "timestamp": "2026-10-09T05:28:00Z"
}
```

---

## 🎯 Next Steps

- Return to the [**User Guide Overview**](index.html).
- Review operational workflows in the [**C2 Tactical Web Interface Guide**](c2-web-interface.html).
