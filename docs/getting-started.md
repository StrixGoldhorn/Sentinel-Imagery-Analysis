---
layout: default
title: Getting Started | Sentinel Imagery Analysis
---

# Getting Started & System Installation

This guide walks you through setting up, configuring, and launching the Sentinel Imagery Analysis platform on your local workstation, dedicated Linux server, or Docker container.

---

## 📋 System Prerequisites

| Component | Minimum Requirement | Recommended Specification |
|---|---|---|
| **Operating System** | Windows 10/11, Ubuntu 22.04+, macOS 12+ | Ubuntu 22.04 LTS (x86_64) |
| **Python** | Python 3.11 or 3.12 | Python 3.12 |
| **CPU** | 4 Cores (x86_64 or Apple Silicon) | 8+ Cores (multithreaded tile processing) |
| **Memory (RAM)** | 8 GB RAM | 16 GB - 32 GB RAM (for large SAR swath mosaics) |
| **Storage** | 10 GB free disk space | 100+ GB SSD (for local tile and cache storage) |
| **Network** | Broadband internet connection | High-speed connection (for Copernicus downloads) |

---

## 🛠️ Step 1: Clone Repository & Set Up Environment

### Windows (PowerShell)
```powershell
# Clone repository
git clone https://github.com/StrixGoldhorn/Sentinel-Imagery-Analysis.git
cd "Sentinel-Imagery-Analysis"

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Linux / macOS (Bash / Zsh)
```bash
# Clone repository
git clone https://github.com/StrixGoldhorn/Sentinel-Imagery-Analysis.git
cd Sentinel-Imagery-Analysis

# Install system libraries for OpenCV (Ubuntu/Debian)
sudo apt-get update && sudo apt-get install -y libgl1 libglib2.0-0

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## ⚙️ Step 2: Configure Environment Variables (`.env`)

Copy the template configuration file:
```bash
cp .env.example .env
```

Open `.env` in your editor and configure the parameters:

```ini
# ==============================================================================
# Copernicus Data Space Ecosystem (CDSE) / Sentinel Hub Credentials
# Register free at: https://dataspace.copernicus.eu/
# ==============================================================================
COP_USERNAME=your_copernicus_username
COP_PASSWORD=your_copernicus_password

# ==============================================================================
# Satellite Orbit & Pass Prediction (Optional)
# Used to corroborate historical pass timings via N2YO API
# ==============================================================================
N2YO_API_KEY=your_optional_n2yo_api_key

# ==============================================================================
# Tactical Alerting Webhook Endpoints (Optional)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
TEAMS_WEBHOOK_URL=https://your-org.webhook.office.com/...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
ALERT_WEBHOOK_URL=https://your-custom-c2-system.example.com/api/v1/maritime-alerts

# ==============================================================================
# Application Storage & Server Settings
# ==============================================================================
DATABASE_PATH=data.db
OUTPUT_ROOT=output
HOST=0.0.0.0
PORT=5050
FLASK_DEBUG=false
```

---

## 🐳 Step 3 (Alternative): Docker Deployment

You can deploy the platform containerized via Docker and Docker Compose:

```bash
# Build and run container in detached mode
docker-compose up --build -d

# Inspect running logs
docker-compose logs -f
```

The web interface will be accessible at `http://localhost:5050`.

---

## 🚀 Step 4: Launching the Web Server

### Development Server
```bash
python app.py
```
*Accessible at: `http://127.0.0.1:5050`*

### Production Server (Gunicorn)
```bash
gunicorn -c gunicorn.conf.py wsgi:app
```

After launch, open `http://127.0.0.1:5050` to confirm that the tactical map and SAR scan controls load:

![Tactical map displayed after a successful local launch](assets/images/tactical-map.png)

---

## 🧪 Step 5: Verifying the Test Suite

Before operational deployment, verify that all system components pass the test suite:

```bash
python -m unittest discover tests -v
```

All 538+ unit and integration tests should pass with `0 failures` and `0 errors`.

---

## 🎯 Next Steps

Now that your instance is running:
- Learn how to navigate the tactical interface in the [**C2 Tactical Web Interface Guide**](c2-web-interface.html).
- Understand how targets are detected and classified in the [**Maritime Intelligence Pipeline Guide**](maritime-intelligence.html).
