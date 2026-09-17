"""Generate Comprehensive System Architecture & Technical Documentation PDF.

This script builds a publication-grade PDF report documenting the Sentinel Imagery
Analysis platform, incorporating project purpose, detailed features, software
architecture, operational workflows, and vector diagrams.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WORKSPACE_DIR = Path(__file__).resolve().parent
OUTPUT_PDF_ROOT = WORKSPACE_DIR / "Sentinel_Imagery_Analysis_Architecture_and_Features.pdf"
OUTPUT_PDF_DIR = WORKSPACE_DIR / "output" / "pdf" / "Sentinel_Imagery_Analysis_Architecture_and_Features.pdf"

# Load demo detection image if available
DEMO_IMG_PATH = WORKSPACE_DIR / "demo" / "demo_detection.jpg"
demo_img_b64 = ""
if DEMO_IMG_PATH.exists():
    with open(DEMO_IMG_PATH, "rb") as f:
        demo_img_b64 = base64.b64encode(f.read()).decode("utf-8")

# SVG DIAGRAMS DEFINITIONS

SVG_CLEAN_ARCHITECTURE = """
<svg viewBox="0 0 800 440" xmlns="http://www.w3.org/2000/svg" style="width: 100%; max-width: 760px; height: auto; margin: 0 auto; display: block;">
  <defs>
    <filter id="shadow" x="-3%" y="-3%" width="106%" height="106%">
      <feDropShadow dx="1" dy="2" stdDeviation="2" flood-opacity="0.1"/>
    </filter>
    <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#0284c7" />
    </marker>
    <linearGradient id="gradInterfaces" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f8fafc"/>
      <stop offset="100%" stop-color="#f1f5f9"/>
    </linearGradient>
    <linearGradient id="gradInfra" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f0f9ff"/>
      <stop offset="100%" stop-color="#e0f2fe"/>
    </linearGradient>
    <linearGradient id="gradApp" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#eff6ff"/>
      <stop offset="100%" stop-color="#dbeafe"/>
    </linearGradient>
    <linearGradient id="gradDomain" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0284c7"/>
      <stop offset="100%" stop-color="#0369a1"/>
    </linearGradient>
  </defs>

  <!-- Outer Ring: Interfaces -->
  <rect x="15" y="15" width="770" height="380" rx="14" fill="url(#gradInterfaces)" stroke="#94a3b8" stroke-width="1.8" filter="url(#shadow)"/>
  <text x="35" y="42" font-family="'Segoe UI', Roboto, sans-serif" font-size="12.5" font-weight="bold" fill="#334155">1. DRIVERS &amp; INTERFACES LAYER (Flask Web Blueprints, Click CLI Commands, Desktop GUI)</text>

  <!-- Infrastructure Layer Box -->
  <rect x="35" y="58" width="730" height="315" rx="12" fill="url(#gradInfra)" stroke="#38bdf8" stroke-width="1.6"/>
  <text x="55" y="82" font-family="'Segoe UI', Roboto, sans-serif" font-size="12" font-weight="bold" fill="#0369a1">2. INFRASTRUCTURE ADAPTERS (Copernicus API, SQLite, OpenCV, Pillow, N2YO, AIS Plugins, APScheduler)</text>

  <!-- Application Layer Box -->
  <rect x="65" y="98" width="670" height="255" rx="10" fill="url(#gradApp)" stroke="#60a5fa" stroke-width="1.6"/>
  <text x="85" y="122" font-family="'Segoe UI', Roboto, sans-serif" font-size="11.5" font-weight="bold" fill="#1e40af">3. APPLICATION LAYER (Use Cases: CreateScan, DetectShips, CorrelateAIS; Protocols: ImageryProvider, AISRepo)</text>

  <!-- Domain Layer Box -->
  <rect x="110" y="138" width="580" height="195" rx="8" fill="url(#gradDomain)" stroke="#0c4a6e" stroke-width="1.8"/>
  <text x="400" y="166" font-family="'Segoe UI', Roboto, sans-serif" font-size="13.5" font-weight="bold" fill="#ffffff" text-anchor="middle">4. DOMAIN CORE (Pure Business Entities &amp; Invariants)</text>

  <!-- Domain Entities Inside -->
  <g transform="translate(130, 182)">
    <rect x="0" y="0" width="160" height="36" rx="5" fill="#ffffff" stroke="#bae6fd"/>
    <text x="80" y="23" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">BoundingBox</text>

    <rect x="190" y="0" width="160" height="36" rx="5" fill="#ffffff" stroke="#bae6fd"/>
    <text x="270" y="23" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">Acquisition</text>

    <rect x="380" y="0" width="160" height="36" rx="5" fill="#ffffff" stroke="#bae6fd"/>
    <text x="460" y="23" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">Scan &amp; ImageTile</text>

    <rect x="0" y="48" width="160" height="36" rx="5" fill="#ffffff" stroke="#bae6fd"/>
    <text x="80" y="71" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">ShipDetection</text>

    <rect x="190" y="48" width="160" height="36" rx="5" fill="#ffffff" stroke="#bae6fd"/>
    <text x="270" y="71" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">Vessel &amp; Position</text>

    <rect x="380" y="48" width="160" height="36" rx="5" fill="#ffffff" stroke="#bae6fd"/>
    <text x="460" y="71" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">PostPassJob</text>

    <text x="270" y="118" font-family="'Segoe UI', sans-serif" font-size="10.5" font-weight="500" fill="#f0f9ff" text-anchor="middle">Zero-Dependency Core &bull; Frozen Immutable Dataclasses &bull; Strict Geographic Range Checking</text>
  </g>

  <!-- Inward Arrows -->
  <path d="M 35 220 L 100 220" stroke="#0284c7" stroke-width="2.5" stroke-dasharray="4,3" marker-end="url(#arrow)"/>
  <path d="M 765 220 L 700 220" stroke="#0284c7" stroke-width="2.5" stroke-dasharray="4,3" marker-end="url(#arrow)"/>

  <!-- Footer Banner -->
  <rect x="100" y="405" width="600" height="26" rx="13" fill="#e0f2fe" stroke="#38bdf8" stroke-width="1"/>
  <text x="400" y="422" font-family="'Segoe UI', Roboto, sans-serif" font-size="10.5" font-weight="bold" fill="#0369a1" text-anchor="middle">DEPENDENCY INVERSION PRINCIPLE: All source code dependencies point strictly inward toward Domain entities</text>
</svg>
"""

SVG_SYSTEM_TOPOLOGY = """
<svg viewBox="0 0 820 440" xmlns="http://www.w3.org/2000/svg" style="width: 100%; max-width: 780px; height: auto; margin: 0 auto; display: block;">
  <defs>
    <filter id="cardShadow" x="-3%" y="-3%" width="106%" height="106%">
      <feDropShadow dx="1" dy="2" stdDeviation="2" flood-opacity="0.1"/>
    </filter>
    <marker id="flowArrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#0ea5e9" />
    </marker>
  </defs>

  <!-- Clients Column -->
  <g transform="translate(15, 25)">
    <rect x="0" y="0" width="145" height="395" rx="10" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5"/>
    <text x="72" y="28" font-family="'Segoe UI', sans-serif" font-size="13" font-weight="bold" fill="#0f172a" text-anchor="middle">CLIENT INTERFACES</text>
    
    <rect x="12" y="48" width="121" height="68" rx="6" fill="#ffffff" stroke="#94a3b8" filter="url(#cardShadow)"/>
    <text x="72" y="75" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#1e293b" text-anchor="middle">Flask Web UI</text>
    <text x="72" y="93" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#64748b" text-anchor="middle">Leaflet Map &amp; Scans</text>

    <rect x="12" y="130" width="121" height="68" rx="6" fill="#ffffff" stroke="#94a3b8" filter="url(#cardShadow)"/>
    <text x="72" y="157" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#1e293b" text-anchor="middle">Click CLI</text>
    <text x="72" y="175" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#64748b" text-anchor="middle">detect, download...</text>

    <rect x="12" y="212" width="121" height="68" rx="6" fill="#ffffff" stroke="#94a3b8" filter="url(#cardShadow)"/>
    <text x="72" y="239" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#1e293b" text-anchor="middle">Desktop GUI</text>
    <text x="72" y="257" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#64748b" text-anchor="middle">Tile Annotator (Tk)</text>

    <rect x="12" y="295" width="121" height="52" rx="6" fill="#ffffff" stroke="#94a3b8" filter="url(#cardShadow)"/>
    <text x="72" y="320" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="600" fill="#1e293b" text-anchor="middle">CRS Inspector</text>
    <text x="72" y="334" font-family="'Segoe UI', sans-serif" font-size="9" fill="#64748b" text-anchor="middle">Projection Diagnostics</text>
  </g>

  <!-- Monolith Host Core -->
  <g transform="translate(180, 15)">
    <rect x="0" y="0" width="410" height="415" rx="12" fill="#f0fdf4" stroke="#86efac" stroke-width="1.8"/>
    <text x="205" y="26" font-family="'Segoe UI', sans-serif" font-size="14" font-weight="bold" fill="#15803d" text-anchor="middle">APPLICATION RUNTIME (Modular Monolith)</text>

    <!-- Web Layer -->
    <rect x="15" y="42" width="380" height="52" rx="6" fill="#ffffff" stroke="#bbf7d0" filter="url(#cardShadow)"/>
    <text x="195" y="65" font-family="'Segoe UI', sans-serif" font-size="12" font-weight="bold" fill="#0f172a" text-anchor="middle">Flask Web Server &amp; RESTful JSON APIs</text>
    <text x="195" y="82" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#64748b" text-anchor="middle">Blueprints: Scans, AOIs, AIS, Schedules, Tasks, Settings</text>

    <!-- ApplicationContainer -->
    <rect x="15" y="104" width="380" height="44" rx="6" fill="#e0f2fe" stroke="#38bdf8"/>
    <text x="195" y="125" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0369a1" text-anchor="middle">ApplicationContainer (Composition Root)</text>
    <text x="195" y="139" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#0284c7" text-anchor="middle">Injects Infrastructure Adapters into Protocol Ports</text>

    <!-- Use Cases & Engines -->
    <rect x="15" y="158" width="380" height="152" rx="6" fill="#ffffff" stroke="#cbd5e1" filter="url(#cardShadow)"/>
    <text x="195" y="178" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">Application Orchestration Use Cases</text>
    <g transform="translate(25, 190)">
      <rect x="0" y="0" width="175" height="32" rx="4" fill="#f1f5f9"/>
      <text x="87" y="20" font-family="'Segoe UI', sans-serif" font-size="10" font-weight="bold" fill="#334155" text-anchor="middle">CreateScan (Stitching)</text>

      <rect x="185" y="0" width="175" height="32" rx="4" fill="#f1f5f9"/>
      <text x="272" y="20" font-family="'Segoe UI', sans-serif" font-size="10" font-weight="bold" fill="#334155" text-anchor="middle">DetectShips (CV Metrology)</text>

      <rect x="0" y="38" width="175" height="32" rx="4" fill="#f1f5f9"/>
      <text x="87" y="58" font-family="'Segoe UI', sans-serif" font-size="10" font-weight="bold" fill="#334155" text-anchor="middle">Hybrid Pass Prediction</text>

      <rect x="185" y="38" width="175" height="32" rx="4" fill="#f1f5f9"/>
      <text x="272" y="58" font-family="'Segoe UI', sans-serif" font-size="10" font-weight="bold" fill="#334155" text-anchor="middle">Correlate AIS &amp; SAR</text>

      <rect x="0" y="76" width="175" height="32" rx="4" fill="#f1f5f9"/>
      <text x="87" y="96" font-family="'Segoe UI', sans-serif" font-size="10" font-weight="bold" fill="#334155" text-anchor="middle">IngestPostPassImagery</text>

      <rect x="185" y="76" width="175" height="32" rx="4" fill="#f1f5f9"/>
      <text x="272" y="96" font-family="'Segoe UI', sans-serif" font-size="10" font-weight="bold" fill="#334155" text-anchor="middle">ShutdownCoordinator</text>
    </g>

    <!-- Background Workers -->
    <rect x="15" y="320" width="380" height="82" rx="6" fill="#f8fafc" stroke="#cbd5e1"/>
    <text x="195" y="338" font-family="'Segoe UI', sans-serif" font-size="10.5" font-weight="bold" fill="#475569" text-anchor="middle">Concurrent Execution &amp; Scheduling</text>
    <g transform="translate(25, 348)">
      <rect x="0" y="0" width="115" height="42" rx="4" fill="#ffffff" stroke="#94a3b8"/>
      <text x="57" y="18" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="bold" fill="#1e293b" text-anchor="middle">APScheduler</text>
      <text x="57" y="32" font-family="'Segoe UI', sans-serif" font-size="8.5" fill="#64748b" text-anchor="middle">30s AOI / 1hr cron</text>

      <rect x="123" y="0" width="115" height="42" rx="4" fill="#ffffff" stroke="#94a3b8"/>
      <text x="180" y="18" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="bold" fill="#1e293b" text-anchor="middle">Pass Monitors</text>
      <text x="180" y="32" font-family="'Segoe UI', sans-serif" font-size="8.5" fill="#64748b" text-anchor="middle">&plusmn;5m flypast threads</text>

      <rect x="245" y="0" width="115" height="42" rx="4" fill="#ffffff" stroke="#94a3b8"/>
      <text x="302" y="18" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="bold" fill="#1e293b" text-anchor="middle">ThreadPool</text>
      <text x="302" y="32" font-family="'Segoe UI', sans-serif" font-size="8.5" fill="#64748b" text-anchor="middle">4 async task workers</text>
    </g>
  </g>

  <!-- External Infrastructure / Persistence Column -->
  <g transform="translate(610, 20)">
    <!-- External APIs -->
    <rect x="0" y="0" width="195" height="195" rx="8" fill="#eff6ff" stroke="#93c5fd" stroke-width="1.5"/>
    <text x="97" y="22" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#1e40af" text-anchor="middle">EXTERNAL ADAPTERS</text>
    
    <rect x="12" y="35" width="171" height="32" rx="4" fill="#ffffff" stroke="#bfdbfe"/>
    <text x="97" y="55" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="600" fill="#1e293b" text-anchor="middle">Copernicus STAC &amp; Process API</text>

    <rect x="12" y="74" width="171" height="32" rx="4" fill="#ffffff" stroke="#bfdbfe"/>
    <text x="97" y="94" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="600" fill="#1e293b" text-anchor="middle">N2YO SGP4 Tracking API</text>

    <rect x="12" y="113" width="171" height="32" rx="4" fill="#ffffff" stroke="#bfdbfe"/>
    <text x="97" y="133" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="600" fill="#1e293b" text-anchor="middle">AIS Plugins (Playwright / UDP)</text>

    <rect x="12" y="152" width="171" height="30" rx="4" fill="#ffffff" stroke="#bfdbfe"/>
    <text x="97" y="172" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="600" fill="#1e293b" text-anchor="middle">Nominatim Reverse Geocoder</text>

    <!-- Local Persistence -->
    <rect x="0" y="210" width="195" height="195" rx="8" fill="#fdf4ff" stroke="#f0abfc" stroke-width="1.5"/>
    <text x="97" y="232" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#86198f" text-anchor="middle">LOCAL PERSISTENCE</text>

    <rect x="12" y="246" width="171" height="42" rx="4" fill="#ffffff" stroke="#f5d0fe"/>
    <text x="97" y="265" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="bold" fill="#1e293b" text-anchor="middle">SQLite Database (data.db)</text>
    <text x="97" y="279" font-family="'Segoe UI', sans-serif" font-size="8.5" fill="#64748b" text-anchor="middle">AOIs, Vessels, Jobs, Settings</text>

    <rect x="12" y="296" width="171" height="44" rx="4" fill="#ffffff" stroke="#f5d0fe"/>
    <text x="97" y="315" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="bold" fill="#1e293b" text-anchor="middle">Filesystem Workspaces</text>
    <text x="97" y="329" font-family="'Segoe UI', sans-serif" font-size="8.5" fill="#64748b" text-anchor="middle">Atomic SAR images &amp; metadata</text>

    <rect x="12" y="348" width="171" height="42" rx="4" fill="#ffffff" stroke="#f5d0fe"/>
    <text x="97" y="367" font-family="'Segoe UI', sans-serif" font-size="9.5" font-weight="bold" fill="#1e293b" text-anchor="middle">Tile Cache (.cache/)</text>
    <text x="97" y="380" font-family="'Segoe UI', sans-serif" font-size="8.5" fill="#64748b" text-anchor="middle">SHA-256 Hashed PNG Tiles</text>
  </g>

  <!-- Connectors -->
  <path d="M 160 140 L 180 140" stroke="#0ea5e9" stroke-width="2" marker-end="url(#flowArrow)"/>
  <path d="M 590 120 L 610 120" stroke="#0ea5e9" stroke-width="2" marker-end="url(#flowArrow)"/>
  <path d="M 590 310 L 610 310" stroke="#0ea5e9" stroke-width="2" marker-end="url(#flowArrow)"/>
</svg>
"""

SVG_MISSION_WORKFLOW = """
<svg viewBox="0 0 820 400" xmlns="http://www.w3.org/2000/svg" style="width: 100%; max-width: 780px; height: auto; margin: 0 auto; display: block;">
  <defs>
    <marker id="wfArrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#0284c7" />
    </marker>
  </defs>

  <rect x="10" y="10" width="800" height="380" rx="10" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1.8"/>
  <text x="410" y="36" font-family="'Segoe UI', sans-serif" font-size="15" font-weight="bold" fill="#0f172a" text-anchor="middle">END-TO-END MARITIME SATELLITE INTELLIGENCE WORKFLOW</text>

  <!-- Step 1: Define AOI -->
  <g transform="translate(30, 58)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#ffffff" stroke="#0284c7" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#0284c7"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">1</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a">Define AOI</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Set BoundingBox</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Geographic validation</text>
  </g>

  <!-- Step 2: Hybrid Prediction -->
  <g transform="translate(230, 58)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#ffffff" stroke="#0284c7" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#0284c7"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">2</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a">Pass Prediction</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">SGP4 + 12-day repeat</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Corroborated passes</text>
  </g>

  <!-- Step 3: Pass Scheduling -->
  <g transform="translate(430, 58)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#ffffff" stroke="#0284c7" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#0284c7"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">3</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a">Pass Scheduler</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">APScheduler arms</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">PassMonitor thread</text>
  </g>

  <!-- Step 4: AIS Scraping -->
  <g transform="translate(630, 58)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#ffffff" stroke="#0284c7" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#0284c7"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">4</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a">Flypast AIS</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">&plusmn;5 min overpass window</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">High-freq AIS capture</text>
  </g>

  <!-- Connecting Arrows Row 1 -->
  <path d="M 190 100 L 230 100" stroke="#0284c7" stroke-width="2" marker-end="url(#wfArrow)"/>
  <path d="M 390 100 L 430 100" stroke="#0284c7" stroke-width="2" marker-end="url(#wfArrow)"/>
  <path d="M 590 100 L 630 100" stroke="#0284c7" stroke-width="2" marker-end="url(#wfArrow)"/>
  <path d="M 710 142 L 710 195" stroke="#0284c7" stroke-width="2" marker-end="url(#wfArrow)"/>

  <!-- Step 8: Correlation (Bottom Left) -->
  <g transform="translate(30, 195)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#fef2f2" stroke="#ef4444" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#ef4444"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">8</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#991b1b">Dark Vessel Alert</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#b91c1c">3-tier correlation</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#b91c1c">Uncorrelated flags</text>
  </g>

  <!-- Step 7: CV Detection -->
  <g transform="translate(230, 195)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#ffffff" stroke="#0284c7" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#0284c7"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">7</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a">Vessel Detection</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Lee/Frost speckle filter</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">OBB metrology (L, B, &theta;)</text>
  </g>

  <!-- Step 6: SAR Download & Stitch -->
  <g transform="translate(430, 195)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#ffffff" stroke="#0284c7" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#0284c7"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">6</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a">SAR Stitching</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Multi-tile Process API</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Atomic Pillow seam stitch</text>
  </g>

  <!-- Step 5: Post-Pass Poller -->
  <g transform="translate(630, 195)">
    <rect x="0" y="0" width="160" height="84" rx="6" fill="#ffffff" stroke="#0284c7" stroke-width="1.8"/>
    <circle cx="24" cy="22" r="12" fill="#0284c7"/>
    <text x="24" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#ffffff" text-anchor="middle">5</text>
    <text x="45" y="26" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a">Post-Pass Poll</text>
    <text x="12" y="50" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Poll CDSE STAC</text>
    <text x="12" y="66" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">Backoff: 2, 3, 5, 10 min</text>
  </g>

  <!-- Connecting Arrows Row 2 (Right to Left) -->
  <path d="M 630 237 L 590 237" stroke="#0284c7" stroke-width="2" marker-end="url(#wfArrow)"/>
  <path d="M 430 237 L 390 237" stroke="#0284c7" stroke-width="2" marker-end="url(#wfArrow)"/>
  <path d="M 230 237 L 190 237" stroke="#0284c7" stroke-width="2" marker-end="url(#wfArrow)"/>

  <!-- Legend Bottom -->
  <g transform="translate(30, 305)">
    <rect x="0" y="0" width="760" height="65" rx="6" fill="#f1f5f9" stroke="#cbd5e1"/>
    <text x="20" y="24" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#334155">TACTICAL MARITIME ADVANTAGE</text>
    <text x="20" y="42" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">SAR penetrates cloud cover, fog, and darkness. AIS data collected within &plusmn;5 minutes of the exact satellite overpass</text>
    <text x="20" y="56" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#475569">enables unambiguous identification of cooperative ships and instantaneous classification of non-transmitting "dark vessels".</text>
  </g>
</svg>
"""

SVG_CV_PIPELINE = """
<svg viewBox="0 0 820 370" xmlns="http://www.w3.org/2000/svg" style="width: 100%; max-width: 780px; height: auto; margin: 0 auto; display: block;">
  <defs>
    <marker id="cvArrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#0d9488" />
    </marker>
  </defs>

  <rect x="10" y="10" width="800" height="350" rx="10" fill="#f0fdfa" stroke="#99f6e4" stroke-width="1.8"/>
  <text x="410" y="34" font-family="'Segoe UI', sans-serif" font-size="15" font-weight="bold" fill="#115e59" text-anchor="middle">CLASSICAL COMPUTER VISION &amp; VESSEL METROLOGY PIPELINE</text>

  <!-- Step 1: Input -->
  <g transform="translate(30, 55)">
    <rect x="0" y="0" width="160" height="110" rx="6" fill="#ffffff" stroke="#14b8a6" stroke-width="1.5"/>
    <text x="80" y="24" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a" text-anchor="middle">1. Raw SAR Scene</text>
    <text x="14" y="46" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Level-1 GRD IW Mode</text>
    <text x="14" y="64" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Dual Pol: VV + VH</text>
    <text x="14" y="82" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; 10m Pixel Spacing</text>
    <text x="14" y="100" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; dB / Linear Scaling</text>
  </g>

  <!-- Step 2: Denoise & Mask -->
  <g transform="translate(230, 55)">
    <rect x="0" y="0" width="160" height="110" rx="6" fill="#ffffff" stroke="#14b8a6" stroke-width="1.5"/>
    <text x="80" y="24" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a" text-anchor="middle">2. Filter &amp; Mask</text>
    <text x="14" y="46" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Speckle Filter:</text>
    <text x="22" y="60" font-family="'Segoe UI', sans-serif" font-size="8.8" font-weight="600" fill="#0d9488">Lee or Frost Filter</text>
    <text x="14" y="78" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; DEM Land Masking:</text>
    <text x="22" y="92" font-family="'Segoe UI', sans-serif" font-size="8.8" font-weight="600" fill="#0d9488">Copernicus 30m DEM</text>
  </g>

  <!-- Step 3: Threshold -->
  <g transform="translate(430, 55)">
    <rect x="0" y="0" width="160" height="110" rx="6" fill="#ffffff" stroke="#14b8a6" stroke-width="1.5"/>
    <text x="80" y="24" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a" text-anchor="middle">3. Thresholding</text>
    <text x="14" y="46" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Adaptive Thresholding</text>
    <text x="14" y="64" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; CFAR Peak Detection</text>
    <text x="14" y="82" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Bright Target Isolation</text>
    <text x="14" y="100" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Sea Clutter Cutoff</text>
  </g>

  <!-- Step 4: Morphology -->
  <g transform="translate(630, 55)">
    <rect x="0" y="0" width="160" height="110" rx="6" fill="#ffffff" stroke="#14b8a6" stroke-width="1.5"/>
    <text x="80" y="24" font-family="'Segoe UI', sans-serif" font-size="11" font-weight="bold" fill="#0f172a" text-anchor="middle">4. Morphology</text>
    <text x="14" y="46" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Dilation of returns</text>
    <text x="14" y="64" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Connect radar peaks</text>
    <text x="14" y="82" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Area Bounds Check</text>
    <text x="14" y="100" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#475569">&bull; Contour Extraction</text>
  </g>

  <!-- Connectors Row 1 -->
  <path d="M 190 110 L 230 110" stroke="#0d9488" stroke-width="2" marker-end="url(#cvArrow)"/>
  <path d="M 390 110 L 430 110" stroke="#0d9488" stroke-width="2" marker-end="url(#cvArrow)"/>
  <path d="M 590 110 L 630 110" stroke="#0d9488" stroke-width="2" marker-end="url(#cvArrow)"/>
  <path d="M 710 165 L 710 195" stroke="#0d9488" stroke-width="2" marker-end="url(#cvArrow)"/>

  <!-- Step 5: OBB Metrology -->
  <g transform="translate(130, 195)">
    <rect x="0" y="0" width="265" height="140" rx="6" fill="#ffffff" stroke="#0d9488" stroke-width="1.8"/>
    <text x="132" y="24" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">5. Oriented Bounding Box (OBB)</text>
    <text x="18" y="48" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">cv2.minAreaRect():</tspan> Rotated bounding box</text>
    <text x="18" y="68" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Length Metrology:</tspan> Major box dimension (m)</text>
    <text x="18" y="88" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Beam Metrology:</tspan> Minor box dimension (m)</text>
    <text x="18" y="108" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Heading / Aspect:</tspan> Orientation angle (&theta;)</text>
    <text x="18" y="128" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Area Bounds Filter:</tspan> Rejects speckle noise</text>
  </g>

  <!-- Step 6: Chip Extraction -->
  <g transform="translate(425, 195)">
    <rect x="0" y="0" width="265" height="140" rx="6" fill="#ffffff" stroke="#0d9488" stroke-width="1.8"/>
    <text x="132" y="24" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a" text-anchor="middle">6. Vessel Chip Extraction &amp; Stats</text>
    <text x="18" y="48" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Sub-scene Crop:</tspan> Target thumbnail chip</text>
    <text x="18" y="68" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Intensity Profile:</tspan> Backscatter distribution</text>
    <text x="18" y="88" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Signal-to-Clutter:</tspan> Peak vs mean sea ratio</text>
    <text x="18" y="108" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">WGS84 Polygon:</tspan> 4 georeferenced corners</text>
    <text x="18" y="128" font-family="'Segoe UI', sans-serif" font-size="9.5" fill="#334155">&bull; <tspan font-weight="bold">Heuristic Confidence:</tspan> Brightness-derived</text>
  </g>

  <!-- Connector from 4 to 6 -->
  <path d="M 630 265 L 590 265" stroke="#0d9488" stroke-width="2" marker-end="url(#cvArrow)"/>
  <!-- Connector from 6 to 5 -->
  <path d="M 425 265 L 395 265" stroke="#0d9488" stroke-width="2" marker-end="url(#cvArrow)"/>
</svg>
"""

SVG_STATE_MACHINE = """
<svg viewBox="0 0 800 310" xmlns="http://www.w3.org/2000/svg" style="width: 100%; max-width: 760px; height: auto; margin: 0 auto; display: block;">
  <defs>
    <marker id="smArrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1 L 8 5 L 0 9 z" fill="#6366f1" />
    </marker>
  </defs>

  <rect x="10" y="10" width="780" height="290" rx="10" fill="#eef2ff" stroke="#c7d2fe" stroke-width="1.8"/>
  <text x="400" y="34" font-family="'Segoe UI', sans-serif" font-size="15" font-weight="bold" fill="#3730a3" text-anchor="middle">POST-PASS SAR INGESTION STATE MACHINE</text>

  <!-- PENDING_PASS -->
  <g transform="translate(45, 60)">
    <rect x="0" y="0" width="160" height="66" rx="6" fill="#ffffff" stroke="#6366f1" stroke-width="1.8"/>
    <text x="80" y="28" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#1e1b4b" text-anchor="middle">PENDING_PASS</text>
    <text x="80" y="48" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#4f46e5" text-anchor="middle">Wait for satellite flypast</text>
  </g>

  <!-- POLLING_CATALOG -->
  <g transform="translate(285, 60)">
    <rect x="0" y="0" width="170" height="66" rx="6" fill="#ffffff" stroke="#6366f1" stroke-width="1.8"/>
    <text x="85" y="28" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#1e1b4b" text-anchor="middle">POLLING_CATALOG</text>
    <text x="85" y="48" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#4f46e5" text-anchor="middle">Backoff: 2, 3, 5, 10 min</text>
  </g>

  <!-- INGESTING -->
  <g transform="translate(545, 60)">
    <rect x="0" y="0" width="160" height="66" rx="6" fill="#ffffff" stroke="#6366f1" stroke-width="1.8"/>
    <text x="80" y="28" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#1e1b4b" text-anchor="middle">INGESTING</text>
    <text x="80" y="48" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#4f46e5" text-anchor="middle">Download, stitch &amp; CV</text>
  </g>

  <!-- Terminal States Bottom Row -->
  <!-- TIMED_OUT -->
  <g transform="translate(160, 190)">
    <rect x="0" y="0" width="150" height="60" rx="6" fill="#fffbeb" stroke="#f59e0b" stroke-width="1.8"/>
    <text x="75" y="26" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#92400e" text-anchor="middle">TIMED_OUT</text>
    <text x="75" y="45" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#b45309" text-anchor="middle">>24h or missed pass</text>
  </g>

  <!-- COMPLETED -->
  <g transform="translate(370, 190)">
    <rect x="0" y="0" width="150" height="60" rx="6" fill="#f0fdf4" stroke="#10b981" stroke-width="1.8"/>
    <text x="75" y="26" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#065f46" text-anchor="middle">COMPLETED</text>
    <text x="75" y="45" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#047857" text-anchor="middle">Scan persisted cleanly</text>
  </g>

  <!-- FAILED -->
  <g transform="translate(570, 190)">
    <rect x="0" y="0" width="150" height="60" rx="6" fill="#fef2f2" stroke="#ef4444" stroke-width="1.8"/>
    <text x="75" y="26" font-family="'Segoe UI', sans-serif" font-size="11.5" font-weight="bold" fill="#991b1b" text-anchor="middle">FAILED</text>
    <text x="75" y="45" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#b91c1c" text-anchor="middle">Network / API Error</text>
  </g>

  <!-- Transitions -->
  <path d="M 205 93 L 285 93" stroke="#6366f1" stroke-width="2" marker-end="url(#smArrow)"/>
  <text x="245" y="87" font-family="'Segoe UI', sans-serif" font-size="8.8" fill="#4338ca" text-anchor="middle">Flypast + 15m</text>

  <path d="M 455 93 L 545 93" stroke="#6366f1" stroke-width="2" marker-end="url(#smArrow)"/>
  <text x="500" y="87" font-family="'Segoe UI', sans-serif" font-size="8.8" fill="#4338ca" text-anchor="middle">Acq Matched</text>

  <!-- From Polling to Timed Out -->
  <path d="M 330 126 L 255 190" stroke="#f59e0b" stroke-width="2" marker-end="url(#smArrow)"/>
  <!-- From Ingesting to Completed -->
  <path d="M 590 126 L 475 190" stroke="#10b981" stroke-width="2" marker-end="url(#smArrow)"/>
  <!-- From Ingesting to Failed -->
  <path d="M 645 126 L 645 190" stroke="#ef4444" stroke-width="2" marker-end="url(#smArrow)"/>
</svg>
"""

SVG_CORRELATION_SCHEME = """
<svg viewBox="0 0 800 330" xmlns="http://www.w3.org/2000/svg" style="width: 100%; max-width: 760px; height: auto; margin: 0 auto; display: block;">
  <rect x="10" y="10" width="780" height="310" rx="10" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.8"/>
  <text x="400" y="34" font-family="'Segoe UI', sans-serif" font-size="15" font-weight="bold" fill="#0f172a" text-anchor="middle">SAR-TO-AIS 3-TIER SPATIAL CORRELATION SCHEMA</text>

  <!-- Tier 1: Inside Box -->
  <g transform="translate(35, 55)">
    <rect x="0" y="0" width="220" height="245" rx="8" fill="#f0fdf4" stroke="#22c55e" stroke-width="1.8"/>
    <text x="110" y="26" font-family="'Segoe UI', sans-serif" font-size="13" font-weight="bold" fill="#15803d" text-anchor="middle">TIER 1: inside_box</text>
    <rect x="20" y="42" width="180" height="75" rx="5" fill="#ffffff" stroke="#86efac"/>
    <!-- Box with AIS target inside -->
    <rect x="40" y="55" width="140" height="48" rx="4" fill="#dcfce7" stroke="#16a34a" stroke-width="1.5" stroke-dasharray="3,3"/>
    <circle cx="110" cy="79" r="6" fill="#15803d"/>
    <text x="110" y="83" font-family="'Segoe UI', sans-serif" font-size="8" font-weight="bold" fill="#ffffff" text-anchor="middle">&#10003;</text>
    
    <text x="110" y="142" font-family="'Segoe UI', sans-serif" font-size="10.5" font-weight="bold" fill="#15803d" text-anchor="middle">Distance = 0.0 meters</text>
    <text x="15" y="166" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">&bull; AIS ping strictly inside SAR</text>
    <text x="15" y="180" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">  detection bounding box.</text>
    <text x="15" y="202" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">&bull; Highest confidence identity.</text>
    <text x="15" y="224" font-family="'Segoe UI', sans-serif" font-size="9.2" font-weight="bold" fill="#166534">&bull; Status: Cooperative Vessel</text>
  </g>

  <!-- Tier 2: Outside Box -->
  <g transform="translate(290, 55)">
    <rect x="0" y="0" width="220" height="245" rx="8" fill="#fffbeb" stroke="#f59e0b" stroke-width="1.8"/>
    <text x="110" y="26" font-family="'Segoe UI', sans-serif" font-size="13" font-weight="bold" fill="#b45309" text-anchor="middle">TIER 2: outside_box</text>
    <rect x="20" y="42" width="180" height="75" rx="5" fill="#ffffff" stroke="#fde68a"/>
    <!-- Box with AIS target nearby -->
    <rect x="35" y="55" width="85" height="48" rx="4" fill="#fef3c7" stroke="#d97706" stroke-width="1.5"/>
    <circle cx="165" cy="79" r="6" fill="#d97706"/>
    <!-- Buffer distance line -->
    <line x1="120" y1="79" x2="159" y2="79" stroke="#b45309" stroke-width="1.5" stroke-dasharray="2,2"/>
    
    <text x="110" y="142" font-family="'Segoe UI', sans-serif" font-size="10.5" font-weight="bold" fill="#b45309" text-anchor="middle">0 &lt; Dist &le; Tolerance</text>
    <text x="15" y="166" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">&bull; Nearest AIS ping within</text>
    <text x="15" y="180" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">  buffer (500m &ndash; 2000m).</text>
    <text x="15" y="202" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">&bull; Kinematic / drift offset.</text>
    <text x="15" y="224" font-family="'Segoe UI', sans-serif" font-size="9.2" font-weight="bold" fill="#92400e">&bull; Status: Plausible Match</text>
  </g>

  <!-- Tier 3: Uncorrelated -->
  <g transform="translate(545, 55)">
    <rect x="0" y="0" width="220" height="245" rx="8" fill="#fef2f2" stroke="#ef4444" stroke-width="1.8"/>
    <text x="110" y="26" font-family="'Segoe UI', sans-serif" font-size="13" font-weight="bold" fill="#b91c1c" text-anchor="middle">TIER 3: uncorrelated</text>
    <rect x="20" y="42" width="180" height="75" rx="5" fill="#ffffff" stroke="#fecaca"/>
    <!-- Box with NO AIS nearby -->
    <rect x="65" y="55" width="90" height="48" rx="4" fill="#fee2e2" stroke="#dc2626" stroke-width="1.5"/>
    <text x="110" y="86" font-family="'Segoe UI', sans-serif" font-size="16" font-weight="bold" fill="#dc2626" text-anchor="middle">?</text>
    
    <text x="110" y="142" font-family="'Segoe UI', sans-serif" font-size="10.5" font-weight="bold" fill="#b91c1c" text-anchor="middle">Dist &gt; Tolerance (No AIS)</text>
    <text x="15" y="166" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">&bull; Strong radar reflection with</text>
    <text x="15" y="180" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">  ZERO broadcast AIS.</text>
    <text x="15" y="202" font-family="'Segoe UI', sans-serif" font-size="9.2" fill="#334155">&bull; Potential IUU / dark target.</text>
    <text x="15" y="224" font-family="'Segoe UI', sans-serif" font-size="9.2" font-weight="bold" fill="#991b1b">&bull; Status: Dark Vessel Alert!</text>
  </g>
</svg>
"""

def generate_html_document() -> str:
    demo_image_html = ""
    if demo_img_b64:
        demo_image_html = f"""
        <div class="figure-container">
            <img src="data:image/jpeg;base64,{demo_img_b64}" style="max-height: 175px; width: auto; border-radius: 6px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); border: 1px solid #cbd5e1;" />
            <div class="figure-caption"><strong>Figure 7:</strong> Classical Computer Vision Ship Detection with Oriented Bounding Box (OBB) overlays and estimated vessel heading vectors.</div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Sentinel Imagery Analysis — Technical Architecture & Specifications</title>
<style>
    @page {{
        size: A4;
        margin: 14mm 15mm 16mm 15mm;
    }}
    
    * {{
        box-sizing: border-box;
    }}

    body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        color: #1e293b;
        background-color: #ffffff;
        line-height: 1.42;
        font-size: 9.0pt;
        margin: 0;
        padding: 0;
    }}

    /* Cover Page */
    .cover-page {{
        page-break-after: always;
        break-after: page;
        min-height: 92vh;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        padding: 20px 10px 10px 10px;
    }}

    .cover-header {{
        border-top: 6px solid #0284c7;
        padding-top: 24px;
    }}

    .badge-bar {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-bottom: 20px;
    }}

    .badge {{
        display: inline-block;
        font-size: 7.5pt;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        padding: 4px 10px;
        border-radius: 4px;
        background: #f1f5f9;
        color: #334155;
        border: 1px solid #cbd5e1;
    }}

    .badge-primary {{
        background: #e0f2fe;
        color: #0369a1;
        border-color: #bae6fd;
    }}

    .badge-accent {{
        background: #ccfbf1;
        color: #0f766e;
        border-color: #99f6e4;
    }}

    .cover-title {{
        font-size: 26pt;
        font-weight: 800;
        color: #0f172a;
        line-height: 1.15;
        margin: 0 0 10px 0;
        letter-spacing: -0.02em;
    }}

    .cover-subtitle {{
        font-size: 12.5pt;
        font-weight: 400;
        color: #475569;
        line-height: 1.4;
        margin: 0 0 28px 0;
    }}

    .metadata-card {{
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px 20px;
        margin-bottom: 22px;
    }}

    .metadata-grid {{
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 12px 20px;
    }}

    .metadata-item {{
        display: flex;
        flex-direction: column;
    }}

    .metadata-label {{
        font-size: 7.2pt;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #64748b;
        font-weight: 600;
        margin-bottom: 2px;
    }}

    .metadata-value {{
        font-size: 9.2pt;
        color: #0f172a;
        font-weight: 600;
    }}

    .abstract-box {{
        background: #ffffff;
        border-left: 4px solid #0284c7;
        padding: 14px 18px;
        border-radius: 0 8px 8px 0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        border-top: 1px solid #f1f5f9;
        border-right: 1px solid #f1f5f9;
        border-bottom: 1px solid #f1f5f9;
    }}

    .abstract-title {{
        font-size: 9.5pt;
        font-weight: 700;
        color: #0369a1;
        margin-bottom: 6px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}

    .abstract-text {{
        font-size: 8.8pt;
        color: #334155;
        line-height: 1.5;
        margin: 0;
    }}

    .toc-card {{
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 14px 18px;
        margin-top: 18px;
    }}

    .toc-title {{
        font-size: 8.5pt;
        font-weight: 700;
        color: #0f172a;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 8px;
    }}

    .toc-grid {{
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 6px 16px;
        font-size: 8.2pt;
        color: #334155;
    }}

    .cover-footer {{
        border-top: 1px solid #e2e8f0;
        padding-top: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 7.8pt;
        color: #64748b;
    }}

    /* Typography & Hierarchy */
    h1 {{
        font-size: 16pt;
        font-weight: 800;
        color: #0f172a;
        margin-top: 0;
        margin-bottom: 12px;
        padding-bottom: 6px;
        border-bottom: 2px solid #0284c7;
        letter-spacing: -0.01em;
        break-before: page;
        page-break-before: always;
        break-after: avoid;
        page-break-after: avoid;
    }}

    h1.first-section {{
        break-before: auto;
        page-break-before: auto;
    }}

    h2 {{
        font-size: 11.5pt;
        font-weight: 700;
        color: #1e293b;
        margin-top: 8px;
        margin-bottom: 3px;
        border-left: 3.5px solid #0ea5e9;
        padding-left: 8px;
        break-after: avoid;
        page-break-after: avoid;
    }}

    h3 {{
        font-size: 9.8pt;
        font-weight: 700;
        color: #334155;
        margin-top: 8px;
        margin-bottom: 3px;
        break-after: avoid;
        page-break-after: avoid;
    }}

    p {{
        margin-top: 0;
        margin-bottom: 5px;
        text-align: justify;
    }}

    ul, ol {{
        margin-top: 0;
        margin-bottom: 6px;
        padding-left: 18px;
    }}

    li {{
        margin-bottom: 2px;
    }}

    /* Tables */
    table {{
        width: 100%;
        border-collapse: collapse;
        margin: 5px 0 7px 0;
        font-size: 8.1pt;
        break-inside: avoid;
        page-break-inside: avoid;
    }}

    th {{
        background-color: #0f172a;
        color: #ffffff;
        font-weight: 600;
        text-align: left;
        padding: 4px 8px;
        border: 1px solid #0f172a;
    }}

    td {{
        padding: 2.8px 8px;
        border: 1px solid #cbd5e1;
        vertical-align: top;
    }}

    tr:nth-child(even) {{
        background-color: #f8fafc;
    }}

    /* Code & Callouts */
    pre, code {{
        font-family: "JetBrains Mono", Consolas, Menlo, Monaco, monospace;
        font-size: 8pt;
    }}

    code.inline {{
        background: #f1f5f9;
        color: #0369a1;
        padding: 1.5px 4.5px;
        border-radius: 3px;
        border: 1px solid #e2e8f0;
    }}

    pre {{
        background: #0f172a;
        color: #f8fafc;
        padding: 10px 14px;
        border-radius: 5px;
        overflow-x: auto;
        line-height: 1.4;
        margin: 10px 0 14px 0;
        break-inside: avoid;
        page-break-inside: avoid;
    }}

    .callout {{
        border-radius: 6px;
        padding: 8px 12px;
        margin: 8px 0;
        font-size: 8.7pt;
        break-inside: avoid;
        page-break-inside: avoid;
    }}

    .callout-info {{
        background: #f0f9ff;
        border-left: 3.5px solid #0284c7;
        color: #0c4a6e;
    }}

    .callout-warning {{
        background: #fffbeb;
        border-left: 3.5px solid #f59e0b;
        color: #78350f;
    }}

    .callout-success {{
        background: #f0fdf4;
        border-left: 3.5px solid #10b981;
        color: #064e3b;
    }}

    /* Figure & Diagram Containers */
    .figure-container {{
        text-align: center;
        margin: 14px 0 16px 0;
        break-inside: avoid;
        page-break-inside: avoid;
    }}

    .figure-caption {{
        font-size: 8pt;
        color: #64748b;
        margin-top: 6px;
        font-style: italic;
    }}
</style>
</head>
<body>

<!-- COVER PAGE -->
<div class="cover-page">
    <div class="cover-header">
        <div class="badge-bar">
            <span class="badge badge-primary">Technical Architecture</span>
            <span class="badge badge-accent">Maritime Domain Awareness</span>
            <span class="badge">Sentinel-1 C-Band SAR</span>
            <span class="badge">AIS Telemetry</span>
            <span class="badge">Clean Architecture</span>
        </div>

        <h1 class="cover-title" style="page-break-before: auto; break-before: auto; border-bottom: none;">Sentinel Imagery Analysis</h1>
        <div class="cover-subtitle">Synthetic Aperture Radar (SAR) Vessel Detection, Pass Prediction, and AIS Telemetry Intelligence Platform</div>

        <div class="metadata-card">
            <div class="metadata-grid">
                <div class="metadata-item">
                    <span class="metadata-label">System Type</span>
                    <span class="metadata-value">Modular Monolith Analytical Workstation (Web &amp; CLI)</span>
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">Earth Observation Constellation</span>
                    <span class="metadata-value">ESA Copernicus Sentinel-1 (S1A, S1C, S1D)</span>
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">Core Algorithms</span>
                    <span class="metadata-value">Deterministic CV (CFAR, Lee/Frost, OBB Metrology), SGP4</span>
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">Persistence &amp; Cache</span>
                    <span class="metadata-value">SQLite (Versioned Migrations) &bull; Filesystem Scan Repositories</span>
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">Software Architecture</span>
                    <span class="metadata-value">Inward Clean Architecture (Domain &rarr; Application &rarr; Infrastructure)</span>
                </div>
                <div class="metadata-item">
                    <span class="metadata-label">Date &amp; Release</span>
                    <span class="metadata-value">September 2026 &bull; Production v1.2</span>
                </div>
            </div>
        </div>

        <div class="abstract-box">
            <div class="abstract-title">Executive Abstract</div>
            <p class="abstract-text">
                The <strong>Sentinel Imagery Analysis</strong> platform provides an end-to-end operational intelligence capability designed to monitor maritime Areas of Interest (AOIs). By synthesizing cloud-penetrating C-band Synthetic Aperture Radar (SAR) from the European Space Agency’s Sentinel-1 constellation with real-time Automatic Identification System (AIS) vessel telemetry, the system autonomously predicts satellite flypasts, schedules high-cadence AIS capture, ingests and seamlessly stitches multi-tile SAR scenes, executes deterministic computer vision vessel metrology, and cross-correlates radar contacts against broadcast vessel positions. This document delivers the complete specification of the platform’s mission purpose, functional capabilities, software architecture, data schemas, and operational trade-offs.
            </p>
        </div>

        <div class="toc-card">
            <div class="toc-title">Document Structure &amp; Roadmap</div>
            <div class="toc-grid">
                <div><strong>1. Mission Purpose:</strong> Operational MDA &amp; Dark Vessels</div>
                <div><strong>5. Data Architecture:</strong> SQLite &amp; Workspaces</div>
                <div><strong>2. Core Features:</strong> SAR Engine, CV, AIS Plugins</div>
                <div><strong>6. Quality &amp; Security:</strong> Attributes &amp; Trust Model</div>
                <div><strong>3. Architecture:</strong> Clean Layers, Topology, Ports</div>
                <div><strong>7. Roadmap:</strong> 3-Phase Evolution Strategy</div>
                <div><strong>4. Operational Workflows:</strong> Figures 1 &ndash; 7 &amp; Pipelines</div>
                <div><strong>8. Appendices:</strong> REST APIs, CLI &amp; Verification</div>
            </div>
        </div>
    </div>

    <div class="cover-footer">
        <span>CONFIDENTIAL &bull; MARITIME DOMAIN AWARENESS SPECIFICATION</span>
        <span>AUTONOMOUS EARTH OBSERVATION SYSTEMS GROUP</span>
    </div>
</div>

<!-- SECTION 1: MISSION PURPOSE -->
<h1 class="first-section">1. Mission Background &amp; Project Purpose</h1>

<h2>1.1 The Operational Challenge: Maritime Domain Awareness &amp; Dark Vessels</h2>
<p>
    Maritime Domain Awareness (MDA) is fundamental to the security, ecological integrity, and economic governance of sovereign waters and Exclusive Economic Zones (EEZs). Across vast ocean expanses, authorities face critical threats including:
</p>
<ul>
    <li><strong>Illegal, Unreported, and Unregulated (IUU) Fishing:</strong> Depleting regulated fisheries and violating marine sanctuaries.</li>
    <li><strong>Sanctions Evasion &amp; Illicit Ship-to-Ship (STS) Transfers:</strong> Tankers and cargo ships transferring contraband or crude oil while masking their operational activity.</li>
    <li><strong>Piracy, Smuggling, and Maritime Sovereignty Incursions:</strong> Hostile or illicit incursions across contested geopolitical choke points (e.g., Strait of Malacca, Bab-el-Mandeb, Suez Canal, Baltic Sea).</li>
</ul>
<p>
    Under International Maritime Organization (IMO) regulations, all commercial vessels of 300 gross tonnage or larger on international voyages are mandated to operate an <strong>Automatic Identification System (AIS)</strong> transponder. However, non-compliant vessels routinely switch off their transponders or spoof GPS coordinates—becoming <em>"Dark Vessels"</em> that vanish entirely from conventional coastal and satellite AIS tracking monitors.
</p>

<h2>1.2 Why Spaceborne Synthetic Aperture Radar (SAR)?</h2>
<p>
    Traditional optical Earth Observation satellites (such as Sentinel-2 or Landsat) are fundamentally hindered by atmospheric phenomena: cloud cover, marine fog, smoke, and nighttime darkness obscure the ocean surface up to 70% of the time over critical maritime corridors.
</p>
<p>
    <strong>Sentinel-1 C-Band Synthetic Aperture Radar (5.405 GHz)</strong> overcomes these limitations completely:
</p>
<ul>
    <li><strong>All-Weather, Day-and-Night Capability:</strong> Active microwave radar pulses penetrate heavy cloud cover, precipitation, and operate seamlessly in total darkness.</li>
    <li><strong>High Surface Contrast:</strong> The ambient sea surface acts as a diffuse scatterer, reflecting radar energy away from the sensor and appearing dark in radar imagery. Conversely, metallic ship hulls, superstructures, and right-angled bulkheads act as <em>corner reflectors</em>, bouncing radar energy directly back to the antenna as intensely bright pixels.</li>
    <li><strong>Dual Polarization (VV + VH):</strong> Vertical-Vertical (VV) polarization provides crisp ocean surface boundary contrast, while Vertical-Horizontal (VH) cross-polarization effectively suppresses wind-induced ocean clutter while preserving depolarized vessel returns.</li>
</ul>

<h2>1.3 Core System Purpose &amp; Operational Objectives</h2>
<p>
    The <strong>Sentinel Imagery Analysis</strong> platform was engineered to bridge the gap between spaceborne Earth Observation and maritime situational awareness. It replaces manual, fragmented workflows with an integrated, automated workstation that:
</p>
<ol>
    <li>Tracks user-defined geographic <strong>Areas of Interest (AOIs)</strong> with strict spatial boundary validation.</li>
    <li>Combines real-time orbital mechanics (SGP4) with historical Copernicus repeat-cycle cadence to accurately <strong>predict future imaging satellite passes</strong>.</li>
    <li>Orchestrates background <strong>AIS scraping monitors</strong> during the critical &plusmn;5-minute flypast window to establish cooperative ground truth.</li>
    <li>Automates <strong>post-pass catalogue polling and multi-tile SAR image downloads</strong> from the Copernicus Data Space Ecosystem (CDSE).</li>
    <li>Applies <strong>Digital Elevation Model (DEM) land masking</strong> and <strong>deterministic classical computer vision</strong> to extract candidate vessels and oriented physical dimensions (length, beam, heading).</li>
    <li>Executes a <strong>3-tier geospatial correlation engine</strong> to classify radar detections against AIS telemetry, isolating and alerting on non-transmitting <strong>Dark Vessels</strong>.</li>
</ol>

<div class="callout callout-info">
    <strong>Architectural Tenet: Local-First Analytical Workstation</strong><br>
    The platform is built as a self-contained, modular monolith designed for analyst workstations, operational field centers, and edge facilities. It operates with zero mandatory cloud dependencies outside external data APIs, featuring an inward Clean Architecture that guarantees modularity, testability, and deterministic reproducibility.
</div>

<!-- SECTION 2: SYSTEM FEATURES -->
<h1>2. Core Capabilities &amp; Feature Overview</h1>

<h2>2.1 Sentinel-1 SAR Acquisition Engine</h2>
<p>
    The SAR ingestion pipeline communicates directly with the Copernicus Data Space Ecosystem (CDSE) through STAC 1.0.0 catalog queries and the Sentinel Hub Process API:
</p>
<ul>
    <li><strong>STAC Catalog Search:</strong> Queries Level-1 Ground Range Detected (GRD) products acquired in Interferometric Wide (IW) swath mode across Sentinel-1A, Sentinel-1C, and Sentinel-1D spacecraft.</li>
    <li><strong>Dynamic Evalscript Generation:</strong> Constructs server-side JavaScript scripts to request radiometric terrain-corrected backscatter in decibels (dB) or linear scaling across VV and VH polarizations.</li>
    <li><strong>Adaptive Tiling &amp; Hashed Cache:</strong> Large bounding boxes exceeding API limits are automatically decomposed into optimal geographic zones (<code class="inline">split_into_zones()</code>). Retrieved tiles are validated, cached via SHA-256 hash keys in a local filesystem cache (<code class="inline">.cache/</code>), and assembled seam-free into monolithic scenes using Pillow.</li>
    <li><strong>Atomic Workspace Assembly:</strong> Scene assembly and metadata generation are executed in staging paths before undergoing atomic filesystem replacement, preventing corrupted or partial scans on process interruption.</li>
</ul>

<h2>2.2 Digital Elevation Model (DEM) Land &amp; Coastline Masking</h2>
<p>
    A major failure mode in maritime radar computer vision is false alarm generation along coastlines, islands, harbor walls, and coastal terrain. The platform automatically acquires Copernicus 30m Digital Elevation Models (DEM) matching the identical scan bounding box. Contours above sea level are processed to generate binary land masks, suppressing non-maritime high-backscatter returns before vessel extraction.
</p>

<h2>2.3 Classical Computer Vision &amp; Vessel Metrology</h2>
<p>
    Unlike uncalibrated deep-learning models prone to hallucination and distribution drift, the system utilizes a deterministic classical computer vision pipeline:
</p>
<ul>
    <li><strong>Speckle Reduction Filtering:</strong> Implements adaptive Lee and Frost filtering to suppress multiplicative radar speckle noise while preserving sharp ship-to-water boundary gradients.</li>
    <li><strong>Adaptive &amp; CFAR Thresholding:</strong> Dynamically calculates local background sea clutter statistics to isolate anomalously bright specular returns.</li>
    <li><strong>Morphological Refinement:</strong> Applies structured morphological dilation and closing operations to connect fragmented ship returns (e.g., bows, sterns, and bridge superstructures).</li>
    <li><strong>Oriented Bounding Box (OBB) Metrology:</strong> Computes the minimum area rotated bounding box (<code class="inline">cv2.minAreaRect()</code>) for each candidate contour. Derived metrology includes:
        <ul>
            <li><strong>Length (meters):</strong> Major bounding box axis scaled by ground sampling distance (10m/pixel).</li>
            <li><strong>Beam (meters):</strong> Minor bounding box axis.</li>
            <li><strong>Heading / Aspect Angle (&theta;):</strong> Angular orientation relative to true north.</li>
        </ul>
    </li>
    <li><strong>Target Chip Extraction &amp; Histograms:</strong> Generates cropped high-resolution image chips of each vessel with associated radar backscatter intensity distribution histograms.</li>
</ul>

{demo_image_html}

<h2 style="break-before: page; page-break-before: always;">2.4 Hybrid Orbital Pass Prediction</h2>
<p>
    Satellite pass prediction combines two complementary analytical models:
</p>
<ol>
    <li><strong>SGP4 Real-Time Analytical Propagation:</strong> Integrates with N2YO API to calculate real-time satellite elevation, azimuth, and overpass windows using Two-Line Element (TLE) ephemeris data.</li>
    <li><strong>Historical Repeat-Cycle Cadence Analysis:</strong> Sentinel-1 operates on an exact 12-day repeat orbit (175 orbits per cycle). The engine mines historical acquisition footprints in the Copernicus STAC catalog to extrapolate precise sub-satellite imaging tracks and relative orbit numbers.</li>
</ol>
<p>
    Predictions corroborated across both models receive high confidence scores. Passes predicted only by orbital radio tracking are tagged for AIS capture but excluded from automatic SAR ingestion, as Sentinel-1 radar instruments are not active over all ocean sectors on every pass.
</p>

<h2>2.5 Pluggable Multi-Source AIS Telemetry Ingestion</h2>
<p>
    The system employs an open plugin registry (<code class="inline">DynamicAISPluginRegistry</code>) supporting concurrent telemetry sources:
</p>
<table>
    <thead>
        <tr>
            <th style="width: 20%;">Plugin Name</th>
            <th style="width: 25%;">Transport / Technology</th>
            <th style="width: 30%;">Operational Role</th>
            <th style="width: 25%;">Fault Tolerance</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td><strong>AIS Friends</strong></td>
            <td>HTTP REST / JSON API</td>
            <td>Global terrestrial &amp; satellite AIS aggregator</td>
            <td>Pacing limits, exponential retry</td>
        </tr>
        <tr>
            <td><strong>VesselFinder</strong></td>
            <td>Playwright Headless Browser</td>
            <td>Live geographic coastal scraping</td>
            <td>Stealth plugin, 15m&rarr;4h cooldown</td>
        </tr>
        <tr>
            <td><strong>APRS.fi</strong></td>
            <td>Playwright / HTTP Web</td>
            <td>Amateur maritime AIS gateway</td>
            <td>Session reuse, rate-limit backoff</td>
        </tr>
        <tr>
            <td><strong>UDP NMEA Listener</strong></td>
            <td>UDP Socket / <code class="inline">pyais</code> Decoder</td>
            <td>Local hardware receiver (RTL-SDR / dAISy)</td>
            <td>Non-blocking daemon, buffer pooling</td>
        </tr>
        <tr>
            <td><strong>Mock / Replay</strong></td>
            <td>In-memory Generator</td>
            <td>Deterministic offline validation &amp; test suite</td>
            <td>Isolated mock namespace</td>
        </tr>
    </tbody>
</table>

<h2>2.6 Autonomous Mission Scheduling &amp; Post-Pass Poller</h2>
<p>
    Autonomous background monitoring is powered by APScheduler and dedicated daemon threads:
</p>
<ul>
    <li><strong>Flypast Pass Monitor:</strong> Identifies upcoming satellite passes over enabled AOIs. Exactly &plusmn;5 minutes around the flypast peak, a high-frequency AIS scraping sequence is triggered (sampling once every 60 seconds) to ensure temporal synchronicity with the radar acquisition.</li>
    <li><strong>Post-Pass Ingestion State Machine:</strong> Following the flypast, an asynchronous job polls the Copernicus STAC catalog using a progressive backoff sequence (2, 3, 5, 10 minutes) until the Level-1 GRD product is processed and published by ESA. Matching imagery within &plusmn;1 hour is ingested, stitched, and processed through computer vision automatically.</li>
</ul>

<h2>2.7 SAR-to-AIS 3-Tier Spatial Correlation &amp; Dark Vessel Detection</h2>
<p>
    When a scan is processed, detected ship targets are projected into geographic WGS84 coordinates and cross-referenced against all vessel positions recorded in SQLite within a matching time window:
</p>
<ul>
    <li><strong style="color: #16a34a;">Tier 1: inside_box (Distance = 0.0m):</strong> An AIS beacon position falls strictly inside the oriented bounding box of the radar return. Confirmed identity with maximum confidence.</li>
    <li><strong style="color: #d97706;">Tier 2: outside_box (0m &lt; Distance &le; Tolerance):</strong> Nearest AIS ping is outside the box but within a configurable tolerance buffer (default 500m &ndash; 2000m), accounting for GPS antenna placement, vessel drift, and radar azimuth shift. Plausible kinematic correlation.</li>
    <li><strong style="color: #dc2626;">Tier 3: uncorrelated (Distance &gt; Tolerance):</strong> A confirmed high-backscatter radar detection with <strong>zero associated AIS pings</strong>. Flagged immediately as a <strong>Potential Dark Vessel</strong>.</li>
</ul>

<h2>2.8 Multi-Modal Delivery Interfaces</h2>
<ul>
    <li><strong>Flask Web Dashboard:</strong> Interactive Leaflet-powered GIS mapping interface featuring satellite swath footprints, oriented ship bounding boxes, vessel trails, real-time AIS telemetry overlays, and time scrubbing controls.</li>
    <li><strong>CLI Automation Suite:</strong> Direct shell access via <code class="inline">python -m sentinel_analysis</code> providing dedicated verbs: <code class="inline">detect</code>, <code class="inline">download</code>, <code class="inline">predict</code>, <code class="inline">ingest</code>, and <code class="inline">annotate</code>.</li>
    <li><strong>Desktop Tile Annotator:</strong> Standalone GUI tool built with Tkinter and OpenCV for human analysts to review, label, and export training tiles for machine learning validation.</li>
    <li><strong>CRS Inspector Utility:</strong> Built-in geospatial diagnostic tool (<code class="inline">utils/crs_inspector.py</code>) analyzing raw bounding boxes, detecting EPSG projections, validating coordinate ranges, and generating conversion scripts.</li>
</ul>

<!-- SECTION 3: SOFTWARE ARCHITECTURE -->
<h1>3. Software Architecture &amp; Design Principles</h1>

<h2>3.1 Clean Architecture &amp; Inward Dependency Model</h2>
<p>
    The codebase strictly enforces <strong>Clean Architecture</strong> (Uncle Bob / Ports and Adapters pattern). Code is structured into five distinct packages, where inner layers have zero knowledge of outer layers:
</p>

<div class="figure-container">
    {SVG_CLEAN_ARCHITECTURE}
    <div class="figure-caption"><strong>Figure 1:</strong> Clean Architecture layer boundary model. Dependency vectors strictly point inward toward domain entities.</div>
</div>

<p>
    Automated architectural compliance tests in <code class="inline">tests/test_architecture.py</code> verify AST import trees during continuous integration, ensuring that neither <code class="inline">domain</code> nor <code class="inline">application</code> modules import external libraries (Flask, SQLite, OpenCV, Pillow, Requests) or infrastructure implementations.
</p>

<h2>3.2 Layer Breakdown &amp; Port Specifications</h2>
<table>
    <thead>
        <tr>
            <th style="width: 18%;">Layer</th>
            <th style="width: 25%;">Package Path</th>
            <th style="width: 32%;">Core Responsibilities</th>
            <th style="width: 25%;">Allowed Dependencies</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td><strong>Domain</strong></td>
            <td><code class="inline">sentinel_analysis.domain</code></td>
            <td>Immutable business entities (<code class="inline">BoundingBox</code>, <code class="inline">Scan</code>, <code class="inline">ShipDetection</code>, <code class="inline">Vessel</code>), invariant validation, pure math</td>
            <td>Python standard library only (no external packages)</td>
        </tr>
        <tr>
            <td><strong>Application</strong></td>
            <td><code class="inline">sentinel_analysis.application</code></td>
            <td>Use cases (<code class="inline">CreateScan</code>, <code class="inline">DetectShips</code>, <code class="inline">CorrelateAIS</code>), structural Protocol ports, lifecycle shutdown</td>
            <td><code class="inline">domain</code>, Python stdlib (<code class="inline">typing.Protocol</code>)</td>
        </tr>
        <tr>
            <td><strong>Infrastructure</strong></td>
            <td><code class="inline">sentinel_analysis.infrastructure</code></td>
            <td>Concrete port adapters: Copernicus STAC client, SQLite repos, OpenCV detector, Pillow stitcher, APScheduler</td>
            <td><code class="inline">application</code>, <code class="inline">domain</code>, SQLite, OpenCV, Requests, Playwright</td>
        </tr>
        <tr>
            <td><strong>Interfaces</strong></td>
            <td><code class="inline">sentinel_analysis.interfaces</code></td>
            <td>Delivery controllers: Flask web routes/blueprints, Click CLI commands, Desktop annotation GUI</td>
            <td><code class="inline">application</code>, <code class="inline">domain</code>, Flask, Click, Tkinter</td>
        </tr>
        <tr>
            <td><strong>Bootstrap</strong></td>
            <td><code class="inline">sentinel_analysis.bootstrap</code></td>
            <td>Composition root (<code class="inline">ApplicationContainer</code>), environment variable parsing, adapter injection</td>
            <td>All packages (wires infrastructure to application ports)</td>
        </tr>
    </tbody>
</table>

<h2>3.3 System Topology &amp; Component Interaction</h2>
<p>
    The runtime architecture is configured as a high-efficiency modular monolith. The diagram below illustrates the interactions between user interfaces, the dependency container, background execution threads, external services, and persistent storage:
</p>

<div class="figure-container">
    {SVG_SYSTEM_TOPOLOGY}
    <div class="figure-caption"><strong>Figure 2:</strong> High-level system topology and component interaction flow.</div>
</div>

<h2>3.4 Persistence &amp; Data Architecture</h2>
<p>
    The platform employs a dual-tier persistence strategy separating structured relational data from heavy raster imagery:
</p>
<ol>
    <li><strong>Relational SQLite Store (<code class="inline">data.db</code>):</strong>
        <ul>
            <li>Managed by an automated <code class="inline">MigrationRunner</code> applying versioned SQL scripts (<code class="inline">001_initial_schema.sql</code> through <code class="inline">007_...</code>).</li>
            <li>Maintains foreign key enforcement, short-lived connections, transactional commits, and indexed spatial queries across <code class="inline">areas_of_interest</code>, <code class="inline">vessels</code>, <code class="inline">vessel_positions</code>, <code class="inline">pass_forecasts</code>, and <code class="inline">post_pass_ingestion_jobs</code>.</li>
        </ul>
    </li>
    <li><strong>Atomic Filesystem Scan Workspaces (<code class="inline">static/output/&lt;scan_name&gt;/</code>):</strong>
        <ul>
            <li>Monolithic stitched scenes, DEM masks, and vessel crops reside in per-scan folders alongside an atomically written <code class="inline">metadata.json</code> file.</li>
            <li>Eliminates database bloat from multi-megabyte GeoTIFF/PNG files while ensuring instantaneous static HTTP asset delivery to web clients.</li>
        </ul>
    </li>
    <li><strong>Hashed Tile Cache (<code class="inline">.cache/</code>):</strong>
        <ul>
            <li>Stores raw 512x512 SAR and DEM tiles indexed by SHA-256 hashes of acquisition parameters, preventing redundant billable calls to Copernicus APIs.</li>
        </ul>
    </li>
</ol>

<h2>3.5 Concurrency Model &amp; Graceful Shutdown</h2>
<p>
    Background asynchronous tasks (such as manual scan downloads) are scheduled onto an internal <code class="inline">ThreadPoolExecutor</code> worker pool. Time-based operations run through an in-process <code class="inline">APScheduler</code> instance. A cooperative shutdown coordinator (<code class="inline">ShutdownCoordinator</code>) traps SIGINT/SIGTERM signals, halts new task submissions, allows running downloads a grace period, terminates daemon pass monitors, closes UDP listeners, and releases SQLite locks safely before process termination.
</p>

<!-- SECTION 4: DIAGRAMS & WORKFLOWS -->
<h1>4. Operational Workflows &amp; Technical Diagrams</h1>

<h2>4.1 End-to-End Mission Workflow</h2>
<p>
    The end-to-end mission lifecycle transitions autonomously from initial AOI definition to correlated maritime intelligence:
</p>

<div class="figure-container">
    {SVG_MISSION_WORKFLOW}
    <div class="figure-caption"><strong>Figure 3:</strong> Eight-stage end-to-end automated mission and flypast intelligence workflow.</div>
</div>

<h2>4.2 Computer Vision Detection Pipeline</h2>
<p>
    The ship detection engine executes in six discrete stages, ensuring mathematical explainability and reproducible vessel metrology:
</p>

<div class="figure-container">
    {SVG_CV_PIPELINE}
    <div class="figure-caption"><strong>Figure 4:</strong> Computer vision SAR processing, DEM land masking, and OBB metrology pipeline.</div>
</div>

<h2 style="break-before: page; page-break-before: always;">4.3 Post-Pass Ingestion State Machine</h2>
<p>
    The post-pass ingestion lifecycle manages transient network states and satellite data latency with automatic exponential backoff:
</p>

<div class="figure-container">
    {SVG_STATE_MACHINE}
    <div class="figure-caption"><strong>Figure 5:</strong> State machine governing automated post-pass catalogue polling and scene ingestion.</div>
</div>

<h2>4.4 SAR-to-AIS Spatial Correlation Scheme</h2>
<p>
    Detections are georeferenced and categorized into three operational alert levels based on Great-Circle distance calculations:
</p>

<div class="figure-container">
    {SVG_CORRELATION_SCHEME}
    <div class="figure-caption"><strong>Figure 6:</strong> 3-Tier spatial correlation model classifying cooperative vessels vs. potential dark vessels.</div>
</div>

<!-- SECTION 5: DATA ARCHITECTURE -->
<h1>5. Data Schema &amp; Storage Specifications</h1>

<h2>5.1 SQLite Relational Schema</h2>
<p>
    The relational database maintains system state and historical vessel tracks. Key entity tables include:
</p>

<table>
    <thead>
        <tr>
            <th style="width: 22%;">Table Name</th>
            <th style="width: 33%;">Key Columns</th>
            <th style="width: 45%;">Functional Purpose</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td><code class="inline">areas_of_interest</code></td>
            <td><code class="inline">id, name, min_lon, min_lat, max_lon, max_lat, auto_capture, created_at</code></td>
            <td>Geographic bounding box definitions and automated pass capture toggles.</td>
        </tr>
        <tr>
            <td><code class="inline">vessels</code></td>
            <td><code class="inline">mmsi, name, callsign, imo, vessel_type, length, width, first_seen, last_seen</code></td>
            <td>Unique vessel registry indexed by Maritime Mobile Service Identity (MMSI).</td>
        </tr>
        <tr>
            <td><code class="inline">vessel_positions</code></td>
            <td><code class="inline">id, mmsi, timestamp, lat, lon, sog, cog, heading, source, raw_data</code></td>
            <td>Timestamped kinematic trajectory records with speed-over-ground and course.</td>
        </tr>
        <tr>
            <td><code class="inline">scrapers</code></td>
            <td><code class="inline">id, name, enabled, interval_seconds, last_run, next_run, cooldown_until</code></td>
            <td>AIS scraper plugin state, execution pacing, and failure cooldown tracking.</td>
        </tr>
        <tr>
            <td><code class="inline">scraper_logs</code></td>
            <td><code class="inline">id, scraper_id, timestamp, status, records_fetched, duration_ms, error_message</code></td>
            <td>Auditable telemetry ingestion logs and error diagnostics.</td>
        </tr>
        <tr>
            <td><code class="inline">pass_forecasts</code></td>
            <td><code class="inline">id, aoi_id, satellite, pass_start, pass_end, max_elevation, pass_type, confidence</code></td>
            <td>Cached hybrid orbital and historical repeat-cycle overpass predictions.</td>
        </tr>
        <tr>
            <td><code class="inline">post_pass_ingestion_jobs</code></td>
            <td><code class="inline">id, aoi_id, satellite, pass_time, status, poll_attempts, next_poll_time, scan_name</code></td>
            <td>State machine records tracking catalogue polling and automatic SAR downloads.</td>
        </tr>
        <tr>
            <td><code class="inline">settings</code></td>
            <td><code class="inline">key, value_json, updated_at</code></td>
            <td>Dynamic system configuration (API keys, tolerances, thresholds, intervals).</td>
        </tr>
    </tbody>
</table>

<h2>5.2 Filesystem Workspace Hierarchy</h2>
<p>
    Scans and cached tiles are maintained in a structured directory tree:
</p>
<pre>
Sentinel Imagery Analysis/
&boxur;&boxh;&boxh; .cache/                               # Hashed tile cache
&boxv;   &boxur;&boxh;&boxh; 7a9f...b1.png                     # Cached raw SAR tile
&boxv;   &boxur;&boxh;&boxh; c30d...8e.png                     # Cached raw DEM tile
&boxur;&boxh;&boxh; static/output/                         # Stitched scan repositories
&boxv;   &boxur;&boxh;&boxh; Singapore_2026-09-10/
&boxv;   &boxv;   &boxur;&boxh;&boxh; metadata.json                 # Acquisition details &amp; georeference bounds
&boxv;   &boxv;   &boxur;&boxh;&boxh; images/
&boxv;   &boxv;       &boxur;&boxh;&boxh; Singapore_stitched_sar.png # Monolithic stitched SAR scene
&boxv;   &boxv;       &boxur;&boxh;&boxh; Singapore_stitched_dem.png # Optional aligned DEM mask
&boxv;   &boxur;&boxh;&boxh; Cape_Town_2026-08-31/
&boxv;       &boxur;&boxh;&boxh; metadata.json
&boxv;       &boxur;&boxh;&boxh; images/
&boxur;&boxh;&boxh; data.db                                # SQLite Relational Database
</pre>

<!-- SECTION 6: QUALITY & ROADMAP -->
<h1>6. Quality Attributes, Security &amp; Evolution Roadmap</h1>

<h2>6.1 Quality Attribute Assessment</h2>
<table>
    <thead>
        <tr>
            <th style="width: 22%;">Quality Attribute</th>
            <th style="width: 18%;">Rating</th>
            <th style="width: 60%;">Architectural Rationale</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td><strong>Modularity</strong></td>
            <td>Strong</td>
            <td>Strict Clean Architecture layer isolation; ports and protocols cleanly separate domain logic from external vendors.</td>
        </tr>
        <tr>
            <td><strong>Maintainability</strong></td>
            <td>Moderate &ndash; Strong</td>
            <td>Clear separation of concerns; broad test suite (260+ tests). Backwards-compatibility branches can be simplified.</td>
        </tr>
        <tr>
            <td><strong>Reliability</strong></td>
            <td>Moderate</td>
            <td>Atomic filesystem file swaps, SQLite transactions, token retries, and cooldown circuit breakers. In-memory tasks require restart recovery.</td>
        </tr>
        <tr>
            <td><strong>Security</strong></td>
            <td>Local Trust Model</td>
            <td>Strict path traversal and boundary validation. Loopback-bound deployment; lacks multi-user authentication and CSRF guards.</td>
        </tr>
        <tr>
            <td><strong>Scalability</strong></td>
            <td>Single Host</td>
            <td>Optimized for local workstations. Multi-node horizontal scaling requires externalizing the task queue and database.</td>
        </tr>
    </tbody>
</table>

<h2>6.2 Trust Boundary &amp; Operational Security Model</h2>
<p>
    The application is explicitly designed for <strong>trusted local analyst workstations</strong> or secure enclave networks. Existing safeguards include input range verification, filename sanitization, parameterized SQL queries, and secret masking in the settings UI.
</p>
<div class="callout callout-warning">
    <strong>Security Recommendation for Network Exposure</strong><br>
    Prior to exposing the web dashboard over a shared or public network, operators must deploy a reverse proxy (e.g., NGINX / Caddy) providing TLS termination, HTTP Basic / OAuth2 authentication, rate limiting, and CSRF protection headers.
</div>

<h2>6.3 Three-Phase Production Evolution Roadmap</h2>
<ul>
    <li><strong>Phase 1: Local Monolith Hardening (Current Priority)</strong>
        <ul>
            <li>Centralize database migrations into an explicit pre-start bootstrap step.</li>
            <li>Decouple background scheduler startup from Flask application factory to guarantee single-scheduler leadership.</li>
            <li>Pin dependencies in <code class="inline">requirements.txt</code> with frozen hash constraints.</li>
        </ul>
    </li>
    <li><strong>Phase 2: Shared Single-Host Production Service</strong>
        <ul>
            <li>Deploy under Gunicorn / Uvicorn WSGI server behind an authenticated TLS reverse proxy.</li>
            <li>Migrate in-memory tasks to persistent SQLite-backed queue leases.</li>
            <li>Enable SQLite Write-Ahead Logging (WAL) mode for high-concurrency read/write operations.</li>
        </ul>
    </li>
    <li><strong>Phase 3: Distributed Cloud Architecture (High Volume)</strong>
        <ul>
            <li>Extract heavy image processing and STAC polling into Celery / Redis distributed workers.</li>
            <li>Migrate metadata store from SQLite to PostgreSQL / PostGIS and move raster scenes to S3-compatible object storage.</li>
        </ul>
    </li>
</ul>

<!-- SECTION 7: TECHNICAL REFERENCES -->
<h1>7. Technical Reference Appendices</h1>

<h2>7.1 Supported Sentinel-1 Spacecraft</h2>
<table>
    <thead>
        <tr>
            <th style="width: 20%;">Spacecraft</th>
            <th style="width: 25%;">NORAD ID</th>
            <th style="width: 25%;">Launch / Status</th>
            <th style="width: 30%;">Radar Payload &amp; Band</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td><strong>Sentinel-1A</strong></td>
            <td>39634</td>
            <td>Operational (Apr 2014)</td>
            <td>C-SAR (5.405 GHz), 12-day repeat orbit</td>
        </tr>
        <tr>
            <td><strong>Sentinel-1C</strong></td>
            <td>49260</td>
            <td>Operational (Dec 2024)</td>
            <td>C-SAR (5.405 GHz), enhanced AIS receiver</td>
        </tr>
        <tr>
            <td><strong>Sentinel-1D</strong></td>
            <td>56214</td>
            <td>Operational (2025/2026)</td>
            <td>C-SAR (5.405 GHz), full constellation replenishment</td>
        </tr>
    </tbody>
</table>

<h2>7.2 REST API Specification (Core Endpoints)</h2>
<table>
    <thead>
        <tr>
            <th style="width: 20%;">Method &amp; Path</th>
            <th style="width: 25%;">Request Payload</th>
            <th style="width: 20%;">Response</th>
            <th style="width: 35%;">Description</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td><code class="inline">POST /api/tasks/scan</code></td>
            <td><code class="inline">{{bbox, start_date, ...}}</code></td>
            <td><code class="inline">202 Accepted</code></td>
            <td>Submits an asynchronous SAR download and stitching job.</td>
        </tr>
        <tr>
            <td><code class="inline">GET /api/tasks/&lt;id&gt;</code></td>
            <td>None</td>
            <td><code class="inline">200 OK</code></td>
            <td>Queries execution progress and result metadata for a task.</td>
        </tr>
        <tr>
            <td><code class="inline">POST /api/run_cv/&lt;scan&gt;</code></td>
            <td><code class="inline">{{threshold, filter}}</code></td>
            <td><code class="inline">200 OK</code></td>
            <td>Executes classical computer vision ship detection on a scan.</td>
        </tr>
        <tr>
            <td><code class="inline">GET /api/scan/&lt;scan&gt;/crop</code></td>
            <td><code class="inline">bbox, detection_id</code></td>
            <td><code class="inline">200 Image/PNG</code></td>
            <td>Extracts a cropped vessel radar chip and intensity profile.</td>
        </tr>
        <tr>
            <td><code class="inline">POST /api/aoi/&lt;id&gt;/predict</code></td>
            <td>None</td>
            <td><code class="inline">200 OK</code></td>
            <td>Generates hybrid SGP4 and historical pass forecasts for an AOI.</td>
        </tr>
        <tr>
            <td><code class="inline">POST /api/ingest_ais</code></td>
            <td><code class="inline">{{bbox, provider}}</code></td>
            <td><code class="inline">200 OK</code></td>
            <td>Manually triggers an immediate AIS telemetry ingestion cycle.</td>
        </tr>
    </tbody>
</table>

<h2>7.3 Command-Line Interface (CLI) Reference</h2>
<pre>
# Detect candidate vessels in a local SAR image using classical CV:
python -m sentinel_analysis detect scene.png --dem optional_dem.png --output detections.jpg

# Download and stitch Sentinel-1 SAR imagery for a bounding box:
python -m sentinel_analysis download --bbox 103.5 1.15 104.2 1.50 --output-dir output

# Compute hybrid orbital pass predictions for a geographic region:
python -m sentinel_analysis predict --bbox 103.5 1.15 104.2 1.50

# Ingest AIS vessel telemetry from registered providers:
python -m sentinel_analysis ingest --bbox 103.5 1.15 104.2 1.50

# Interactively inspect and annotate SAR tiles in desktop GUI:
python -m sentinel_analysis annotate path/to/tiles

# Diagnose CRS and project coordinates:
python utils/crs_inspector.py --bbox 103.5 1.15 104.2 1.50
</pre>

<div class="callout callout-success" style="margin-top: 18px;">
    <strong>Verification &amp; Test Suite Execution</strong><br>
    The complete unit and integration test suite can be executed offline using standard Python testing tooling:<br>
    <code class="inline">python -m unittest discover -v</code>
</div>

</body>
</html>
"""

def generate_pdf():
    print("Generating updated HTML document...")
    html_content = generate_html_document()
    
    html_file = WORKSPACE_DIR / "temp_report.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"Temporary HTML written to: {html_file}")
    print("Launching Playwright Chromium to render PDF...")

    OUTPUT_PDF_DIR.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        
        # Navigate using file:// URI
        page.goto(html_file.as_uri(), wait_until="networkidle")
        
        # Define clean header and footer
        header_template = """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 7pt; color: #94a3b8; width: 100%; display: flex; justify-content: space-between; padding: 0 16mm;">
            <span>SENTINEL IMAGERY ANALYSIS &bull; SYSTEM ARCHITECTURE REPORT</span>
            <span>SEPTEMBER 2026</span>
        </div>
        """
        
        footer_template = """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 7.5pt; color: #64748b; width: 100%; display: flex; justify-content: space-between; padding: 0 16mm; border-top: 1px solid #e2e8f0; padding-top: 4px;">
            <span>CONFIDENTIAL &bull; MARITIME DOMAIN AWARENESS</span>
            <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
        </div>
        """
        
        # Render to workspace root
        page.pdf(
            path=str(OUTPUT_PDF_ROOT),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
            display_header_footer=True,
            header_template=header_template,
            footer_template=footer_template
        )
        print(f"Successfully generated PDF at: {OUTPUT_PDF_ROOT} (Size: {os.path.getsize(OUTPUT_PDF_ROOT)} bytes)")
        
        # Render copy to output/pdf/ directory
        page.pdf(
            path=str(OUTPUT_PDF_DIR),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
            display_header_footer=True,
            header_template=header_template,
            footer_template=footer_template
        )
        print(f"Successfully generated copy at: {OUTPUT_PDF_DIR} (Size: {os.path.getsize(OUTPUT_PDF_DIR)} bytes)")
        
        browser.close()

    # Clean up temp html
    if html_file.exists():
        html_file.unlink()

if __name__ == "__main__":
    generate_pdf()
