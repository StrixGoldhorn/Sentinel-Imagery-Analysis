-- Migration 015: Add operational observability metrics, audit tables, and correlation tracking

-- Add correlation_id to core entity tables
ALTER TABLE aoi ADD COLUMN correlation_id TEXT;
ALTER TABLE post_pass_ingestions ADD COLUMN correlation_id TEXT;
ALTER TABLE background_tasks ADD COLUMN correlation_id TEXT;

-- Table to record webhook alert delivery latency and success/failure telemetry
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL,
    webhook_id TEXT NOT NULL,
    service_type TEXT NOT NULL,
    status TEXT NOT NULL, -- 'SUCCESS' or 'FAILED'
    http_status INTEGER,
    latency_ms REAL NOT NULL,
    error_message TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alert_deliveries_status ON alert_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_created ON alert_deliveries(created_at);

-- Table to record external provider failure events (Copernicus, N2YO, AIS)
CREATE TABLE IF NOT EXISTS provider_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL, -- 'copernicus', 'n2yo', 'ais'
    operation TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_failures_provider ON provider_failures(provider);
CREATE INDEX IF NOT EXISTS idx_provider_failures_created ON provider_failures(created_at);
