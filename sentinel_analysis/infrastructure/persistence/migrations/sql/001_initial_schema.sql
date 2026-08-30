-- 001_initial_schema.sql: Core tables for AOI, Vessels, Locations, and Scraper Logs
CREATE TABLE IF NOT EXISTS aoi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    bbox TEXT NOT NULL,
    next_scan TEXT,
    last_checked TEXT
);

CREATE TABLE IF NOT EXISTS vessels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imo TEXT NOT NULL,
    mmsi TEXT NOT NULL,
    vessel_name TEXT,
    vessel_type TEXT,
    callsign TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (imo, mmsi)
);

CREATE TABLE IF NOT EXISTS vessel_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vessel_id INTEGER NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    speed REAL,
    heading REAL,
    timestamp DATETIME NOT NULL,
    source_plugin TEXT NOT NULL,
    FOREIGN KEY (vessel_id) REFERENCES vessels (id)
);

CREATE INDEX IF NOT EXISTS idx_vessel_locations_vessel ON vessel_locations(vessel_id);
CREATE INDEX IF NOT EXISTS idx_vessel_locations_timestamp ON vessel_locations(timestamp);

CREATE TABLE IF NOT EXISTS scraper_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_name TEXT NOT NULL,
    status TEXT NOT NULL,
    records_inserted INTEGER DEFAULT 0,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    error_message TEXT
);
