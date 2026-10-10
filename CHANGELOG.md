# Changelog

All notable changes to the **Sentinel Imagery Analysis** system will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.0] - 2026-10-10

### Added
- **Analyst Review Workflow**:
  - Interactive analyst review queue supporting `pending`, `accepted`, `rejected`, and `uncertain` dispositions.
  - Interactive bounding box editing with IoU tracking against raw detections.
  - Analyst identification, structured comments, and immutable audit history (`review_history`).
  - Benchmark and retraining dataset generator exporting clean COCO / YOLO / JSON annotations.
  - Web endpoints for review queue, submission, history, and dataset export (`/reviews`, `/api/reviews/...`).
- **Operational Observability Telemetry**:
  - 8-pillar operational telemetry collector tracking:
    1. Queue depth (pending, running, failed tasks and post-pass jobs)
    2. Scan processing latency (P50, P90, P99, max durations)
    3. Provider failures (Copernicus CDSE, ASF, Umbra, AIS sources)
    4. Imagery age (freshness from sensor capture to ingestion)
    5. Scheduler leadership (heartbeat, active leader ID, lease expiry)
    6. Database locks (busy timeouts, journal mode, write contention)
    7. Detection counts (total contacts, dark vessels, high-confidence targets)
    8. Alert delivery metrics (webhook/email notifications, success rate, delivery latency)
  - End-to-end correlation tracking with `correlation_id` across AOIs, satellite passes, scans, tasks, web requests, and alerts.
  - Prometheus `/metrics` exposition and JSON API `/api/observability/metrics`.
  - Live operator dashboard at `/observability` with metric badges and health monitors.
- **Reproducible Builds & Quality Checks**:
  - Dependency pinning with `constraints.txt` and `requirements-dev.txt`.
  - Comprehensive `pyproject.toml` configuring Ruff linter, Mypy static typing, and Bandit security scanner.
  - Automated CI matrix in `.github/workflows/ci.yml` covering Python 3.11, 3.12, 3.13 on Ubuntu and Windows.

### Database Migrations
- **`014_add_analyst_review_workflow.sql`**:
  - Created `reviews` table for storing analyst decisions, reviewed bboxes, reviewer identity, and reason codes.
  - Created `review_history` table for immutable audit logging of all review transitions.
  - Created `benchmark_datasets` table for tracking versioned exports for model retraining.
- **`015_add_operational_observability.sql`**:
  - Added `correlation_id` column to `aoi`, `post_pass_ingestions`, and `background_tasks`.
  - Created `alert_deliveries` table for tracking notification delivery latencies and status.
  - Created `provider_failures` table for monitoring upstream API failures and error diagnostics.

---

## [1.1.0] - 2026-10-09

### Added
- **Contact Uncertainty Calibration**:
  - Spatial uncertainty modeling with bivariate normal CEP (Circular Error Probable) and error ellipses.
  - Dimensional error estimation accounting for radar resolution cell blur and side-lobe spreading.
  - Kinematic association likelihood scoring integrating spatial, speed, heading, and dimensional consistency.
  - Tactical surveillance reason codes (`RADAR_STRONG_REFLECTOR`, `DARK_VESSEL_SUSPECT`, `SOLAS_TRANSPONDER_OFF`, `SPEED_SPOOFING_DETECTED`, `AIS_KINEMATIC_MATCH`, etc.).
- **SAR Preprocessing Provenance Metadata**:
  - End-to-end lineage capture recording sensor orbit, product ID, polarization, processing baseline, calibration method, terrain correction, DEM source, speckle filter, pixel spacing, model version, and cryptographic source checksums (SHA-256).
- **Geospatial Export Accuracy**:
  - Affine transformation mapping from SAR pixel space $(X, Y)$ to WGS84 geographic coordinates $(\text{Lon}, \text{Lat})$ when image georeferencing is missing or uncalibrated.
- **CI Test Harness Stabilization**:
  - Process-isolated temporary directories for concurrent test runs.
  - Windows CI runner integration for multi-OS stability.

### Database Migrations
- **`008_add_expected_imagery_time.sql`**: Added `expected_imagery_time` to `post_pass_ingestions`.
- **`009_add_system_settings.sql`**: Centralized key-value settings table with JSON validation.
- **`010_add_scraper_log_trigger_reason.sql`**: Added `trigger_reason` tracking for scraper auditing.
- **`011_add_background_task_scan_id.sql`**: Added `scan_id` correlation column to `background_tasks`.
- **`012_add_post_pass_job_events.sql`**: Auditable event log for post-pass ingestion state machine transitions.
- **`013_harden_automation_workflows.sql`**: Multi-process worker leasing, `relative_orbit`, `trigger_type`, and `prediction_source` tracking.

---

## [1.0.0] - 2026-09-02

### Added
- Initial baseline release of **Sentinel Imagery Analysis**:
  - Automated Sentinel-1 SAR acquisition via Copernicus CDSE and ASF DAAC.
  - Classical Adaptive CFAR and ONNX deep learning oriented bounding box (OBB) vessel detection.
  - Multi-source AIS correlation engine with kinematic track interpolation and dark vessel detection.
  - Automated orbital pass prediction, pass scheduling, and background ingestion pipeline.
  - Dynamic distributed scheduler leadership election using SQLite leasing.
  - 7-pillar information architecture web interface and desktop annotation workstation.

### Database Migrations
- **`001_initial_schema.sql`**: Initial core schema for `aoi`, `vessels`, `vessel_locations`, and `scraper_logs`.
- **`002_add_aoi_scheduler.sql`**: Added `auto_capture_enabled` toggle to areas of interest.
- **`003_add_tasks_table.sql`**: Created `background_tasks` table for durable job state tracking.
- **`004_add_scraper_config.sql`**: Created `scraper_config` table for AIS scraping plugin configuration.
- **`005_add_aoi_forecasts_cache.sql`**: Created `aoi_forecasts` cache for satellite pass predictions.
- **`005_enhance_scraper_config.sql`**: Added dynamic config JSON, consecutive failures, and cooldown tracking.
- **`006_add_post_pass_ingestion.sql`**: Created `post_pass_ingestions` table for autonomous pass acquisition.
- **`007_add_scraper_tag.sql`**: Added category tagging to AIS scraper configuration.
