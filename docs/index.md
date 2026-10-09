---
layout: default
title: Sentinel Imagery Analysis | User Guide & Operational Manual
---

# Sentinel Imagery Analysis
### Maritime Domain Awareness, SAR Dark Vessel Interdiction & Multi-Sensor Intelligence Platform

Welcome to the **Sentinel Imagery Analysis** User Guide and Technical Documentation. This platform delivers automated, end-to-end maritime intelligence by fusing European Space Agency (ESA) **Copernicus Sentinel-1 Synthetic Aperture Radar (SAR)** imagery, **Sentinel-2 multispectral optical** passes, **Automatic Identification System (AIS)** telemetry, hydrodynamic wake physics, and geospatial compliance boundaries.

![Sentinel Imagery Analysis tactical map](assets/images/tactical-map.png)

*The main workspace places SAR acquisition, monitored areas, contact filters, and temporal AIS controls around a shared maritime map.*

---

## 🧭 Documentation Sitemap & Guide Directory

| Section | Focus Area | Description |
|---|---|---|
| [**Getting Started**](getting-started.html) | Setup & Configuration | System prerequisites, installation, `.env` setup, Copernicus API keys, and deployment. |
| [**C2 Tactical Web Interface**](c2-web-interface.html) | Operator UI & Workflows | Leaflet tactical map, live quick-filter pills, contact dossier drawer, and scan gallery. |
| [**Maritime Intelligence Pipeline**](maritime-intelligence.html) | Operational Heuristics | Dark vessel classification, SOLAS compliance, STS transshipment, EEZ/MPA geofences, and webhooks. |
| [**Sensor Processing & Physics**](sensor-physics-cv.html) | Radar Physics & CV | Dual-pol (VV+VH) clutter suppression, CFAR, Radon wake kinematics, and Sentinel-2 optical validation. |
| [**Reporting & Tactical Exports**](reporting-and-exports.html) | Mission Products | PDF Tactical Briefings, nautical chart overlays, dark ship dossiers, KMZ Google Earth, and ATAK CoT XML. |
| [**CLI & REST API Reference**](cli-and-api.html) | Developer & Automation | Command-line switches, headless processing, REST API endpoints, and JSON schemas. |

---

## ⚡ Core Capabilities at a Glance

```
                         ┌──────────────────────────────────────────────┐
                         │   Copernicus Sentinel-1 SAR & Sentinel-2     │
                         └──────────────────────┬───────────────────────┘
                                                │
                                                ▼
┌─────────────────────────┐      ┌──────────────────────────────┐      ┌─────────────────────────┐
│     AIS Data Feeds      │ ───► │  Sentinel Analysis Engine    │ ◄─── │  Sovereign EEZ & MPAs   │
│ (Terrestrial/Satellite) │      │                              │      │  (Geofence Compliance)  │
└─────────────────────────┘      └──────────────┬───────────────┘      └─────────────────────────┘
                                                │
                 ┌──────────────────────────────┼──────────────────────────────┐
                 ▼                              ▼                              ▼
   ┌──────────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────────┐
   │   Tactical C2 Web Map    │   │  Multi-Page PDF Dossier  │   │ Interoperability Exports │
   │ (Leaflet Drawer, Filters)│   │ (Nautical Charts, Chips) │   │ (KMZ & ATAK CoT Stream)  │
   └──────────────────────────┘   └──────────────────────────┘   └──────────────────────────┘
```

### 1. Synthetic Aperture Radar (SAR) Vessel Detection
- **All-Weather, Day/Night Surveillance**: Ingests Sentinel-1 C-band Synthetic Aperture Radar (GRD products).
- **Dual-Polarization (VV + VH) Suppression**: Leverages cross-ratio $\sigma^\circ_{VH} / \sigma^\circ_{VV}$ suppression to filter rough ocean wave clutter (Beaufort scale 5+) while preserving depolarized ship hull returns.
- **Adaptive Land Masking**: Integrates SRTM Digital Elevation Model (DEM) and coastline masking to eliminate false positives from islands, shoals, and coastal infrastructure.

### 2. AIS Correlation & Dark Vessel Identification
- **Automatic Target Correlation**: Matches detected radar contacts with historical and live broadcast AIS telemetry using nearest-neighbor and kinematic gating.
- **Dark Vessel Interdiction**: Automatically isolates radar contacts operating with deactivated transponders (violating IMO SOLAS Chapter V, Regulation 19).
- **Kinematic Route Corridor Prediction**: Employs Kalman forward/backward extrapolation to trace projected tracks between intermittent AIS pings.

### 3. Hydrodynamic Wake Kinematics & Anti-Spoofing
- **Radon & Hough Transform Wake Analysis**: Detects physical ship wakes (Kelvin wake arms at $19.5^\circ$ cusp angles and turbulent centerline scars).
- **True Velocity Estimation**: Derives actual vessel speed through water ($V_{\text{water}}$ in knots) and resolves $180^\circ$ heading ambiguities.
- **AIS Spoofing Detection**: Triggers alerts when broadcast AIS velocity or heading contradicts hydrodynamic radar measurements (e.g., claiming anchored while wake indicates 12 knots).

### 4. Operational Intelligence & Anomaly Alerts
- **Ship-to-Ship (STS) Transshipment**: Detects paired vessels drifting within $500\text{ m}$ proximity with matched low relative speeds ($< 2\text{ kn}$) outside designated ports.
- **EEZ & Marine Protected Area (MPA) Geofencing**: Flags unauthorized foreign or dark vessels in sovereign fisheries zones (IUU fishing detection).
- **Automated Webhook Alerts**: Real-time push dispatching to Slack, Microsoft Teams, Discord, and custom REST API endpoints.

### 5. Multi-Sensor & Temporal Analytics
- **Sentinel-2 Optical Cross-Validation**: Cross-references SAR targets against cloud-filtered Sentinel-2 multispectral passes, generating true-color RGB chips and NDWI water masking.
- **Multi-Temporal SAR Coherence & Change Detection**: Performs repeat-pass log-ratio change detection across orbital cycles, categorizing arrivals, departures, and long-term anchored contacts.

### 6. Interactive C2 & Interoperability
- **Tactical Web C2**: Interactive Leaflet interface featuring dynamic quick-filter pills (Dark, SOLAS, Spoof, STS, Optical), contact telemetry drawer, and radar chip zoom.
- **Google Earth KMZ**: Packaged `.kmz` bundles with georeferenced radar overlays, vessel polygons, and inspection metadata.
- **ATAK / WinTAK Cursor-on-Target (CoT)**: Real-time MIL-STD-2525 compliant XML stream for direct tactical integration with field situational awareness systems.
- **Executive PDF Briefings**: Multi-page intelligence documents with OpenSeaMap nautical chart overlays, dark ship dossiers, and corridor plots.

![Generated intelligence briefing target dossier](assets/images/briefing-target-dossier.png)

*Generated briefings preserve the source radar signature alongside map context, vessel dimensions, and the operational risk assessment.*

---

## 🚀 Quick Navigation

- Want to run the system in 5 minutes? Head to [**Getting Started**](getting-started.html).
- Operating the tactical map? Read the [**C2 Tactical Web Interface Guide**](c2-web-interface.html).
- Integrating with ATAK or exporting data? Check [**Reporting & Tactical Exports**](reporting-and-exports.html).
- Automating via scripts or headless servers? Browse the [**CLI & REST API Reference**](cli-and-api.html).
