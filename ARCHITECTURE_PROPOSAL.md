# Architecture Proposal: Plugin-Based AIS Data Scraper & Ingestion Pipeline

## Objective
To implement a flexible, plugin-based data ingestion pipeline for the Sentinel Imagery Analysis project. Specifically, this system will target sites and APIs that provide **Automatic Identification System (AIS) data**. By gathering real-world vessel telemetry, the project can cross-reference physical ship broadcasts with the ships detected in Synthetic Aperture Radar (SAR) imagery. 

## Background & Motivation
While the project currently relies on processing SAR imagery to detect ships, verifying these detections against actual vessel transponder data (AIS) adds immense analytical value (e.g., identifying "dark" vessels that appear on radar but don't broadcast AIS). A modular, plugin-based architecture—inspired by systems like SeaSentry—allows developers to easily integrate multiple AIS data sources (e.g., MarineTraffic, VesselFinder, custom NMEA UDP streams, or public maritime APIs) without hardcoding source-specific logic into the main application.

## Proposed Architecture

We will introduce a new `ingestion` module with the following components:

### 1. Abstract Base Plugin (`ingestion/base_plugin.py`)
An Abstract Base Class (ABC) named `BaseAISScraperPlugin` will enforce a strict interface for all future scrapers. 
Required methods will include:
*   `__init__(self, config)`: Initialize with required credentials and settings (API keys, session configs).
*   `authenticate(self)`: Handle provider-specific authentication or session setup.
*   `fetch_data(self, bbox, time_range)`: Retrieve raw AIS data for a specific Area of Interest (AOI) and time window.
*   `parse_data(self, raw_data)`: Normalize the provider's specific data format into standard Python dictionaries or dataclasses representing both **Vessel Metadata** and **Telemetry (Locations)**.

### 2. Plugin Manager (`ingestion/plugin_manager.py`)
A dynamic plugin loader that scans the `ingestion/plugins/` directory, discovers classes inheriting from `BaseAISScraperPlugin`, and registers them. This allows adding a new AIS source simply by dropping a new Python file into the folder.

### 3. Ingestion Orchestrator (`ingestion/pipeline.py`)
The main entry point for data ingestion. It will:
1. Accept parameters (Target Bounding Box, Time Range).
2. Iterate through active plugins via the `PluginManager` or target a specific one.
3. Orchestrate execution: `authenticate` -> `fetch_data` -> `parse_data`.
4. **Persist the normalized data** into the local SQLite database, carefully separating static vessel data from dynamic location data.

## Database Schema (SQLite)

We will use **SQLite** as the primary storage mechanism. The schema separates static vessel information from dynamic telemetry broadcasts.

### Table: `vessels`
Stores static or semi-static metadata about the ship itself. 

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique vessel record ID |
| `imo` | TEXT | NOT NULL | International Maritime Organization number |
| `mmsi` | TEXT | NOT NULL | Maritime Mobile Service Identity |
| `vessel_name` | TEXT | | Name of the ship |
| `vessel_type` | TEXT | | Type of vessel (e.g., Cargo, Tanker) |
| `callsign` | TEXT | | Radio callsign |
| `created_at` | DATETIME | DEFAULT CURRENT_TIMESTAMP | When the vessel was first recorded |

*Constraint:* `UNIQUE (imo, mmsi)` - Ensures we do not duplicate vessels.

### Table: `vessel_locations`
Stores the dynamic telemetry and location data (the actual AIS broadcasts).

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique record ID |
| `vessel_id` | INTEGER | NOT NULL | Foreign key reference to vessel |
| `latitude` | REAL | NOT NULL | Y coordinate |
| `longitude` | REAL | NOT NULL | X coordinate |
| `speed` | REAL | | Speed over ground in knots |
| `heading` | REAL | | True heading in degrees |
| `timestamp` | DATETIME | NOT NULL | UTC time of the AIS broadcast |
| `source_plugin` | TEXT | NOT NULL | Name of the plugin that scraped this data |

*Constraint:* `FOREIGN KEY (vessel_id) REFERENCES vessels (id)`

### Table: `scraper_logs`
Tracks the health, execution history, and errors of the scraper plugins.

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique log ID |
| `plugin_name` | TEXT | NOT NULL | Name of the executing plugin |
| `status` | TEXT | NOT NULL | 'SUCCESS', 'FAILED', or 'PARTIAL' |
| `records_inserted` | INTEGER | DEFAULT 0 | Number of location records saved |
| `timestamp` | DATETIME | DEFAULT CURRENT_TIMESTAMP | Time of the ingestion run |
| `error_message` | TEXT | | Stack trace or error details if failed |

## Implementation Steps

1.  **Database Migration:** Create the `vessels`, `vessel_locations`, and `scraper_logs` tables in the SQLite database using a centralized setup script. Ensure Foreign Key pragmas are enabled in SQLite.
2.  **Directory Setup:** Create `ingestion/` and `ingestion/plugins/` directories with `__init__.py` files.
3.  **Core Framework:** Write `base_plugin.py` and `plugin_manager.py`.
4.  **Pipeline Orchestrator:** Implement `pipeline.py` to bridge the scrapers to the SQLite database. It must handle inserting/updating `vessels` first to satisfy foreign key constraints before inserting into `vessel_locations`, and then write a completion/failure record to `scraper_logs`.
5.  **Example Plugin:** Create a mock or initial implementation (e.g., `ingestion/plugins/public_ais_api.py`) to demonstrate the flow.
6.  **Integration:** Create a CLI command or API endpoint in `app.py` to trigger the AIS scraper pipeline for a given bounding box.

## Verification & Testing
*   **Unit Tests:** Verify that the `PluginManager` correctly loads plugins and that `base_plugin.py` enforces the required interface.
*   **Database Tests:** Ensure the pipeline correctly handles the split insertion (upserting into `vessels`, inserting into `vessel_locations` using the returned `vessel_id`), respects the unique constraints, and generates accurate `scraper_logs`.
*   **End-to-End Test:** Run a test pipeline execution to fetch data, parse it, and query the SQLite database to confirm successful ingestion across all three tables.