-- 006_add_post_pass_ingestion.sql: Add post_pass_ingestions table to track autonomous post-pass SAR scan ingestion
CREATE TABLE IF NOT EXISTS post_pass_ingestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aoi_id INTEGER NOT NULL REFERENCES aoi(id) ON DELETE CASCADE,
    pass_time TEXT NOT NULL,
    satellite TEXT NOT NULL DEFAULT 'Sentinel-1',
    orbit_direction TEXT,
    status TEXT NOT NULL DEFAULT 'POLLING_CATALOG',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_polled_at TEXT,
    next_poll_at TEXT,
    scan_folder TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(aoi_id, pass_time)
);

CREATE INDEX IF NOT EXISTS idx_post_pass_status_next_poll ON post_pass_ingestions(status, next_poll_at);
CREATE INDEX IF NOT EXISTS idx_post_pass_aoi_pass_time ON post_pass_ingestions(aoi_id, pass_time);
