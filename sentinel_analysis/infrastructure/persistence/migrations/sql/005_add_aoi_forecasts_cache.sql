-- 005_add_aoi_forecasts_cache.sql: Cache table for satellite flypast forecasts
CREATE TABLE IF NOT EXISTS aoi_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aoi_id INTEGER NOT NULL UNIQUE,
    n2yo_predictions_json TEXT,
    historical_predictions_json TEXT,
    combined_predictions_json TEXT,
    mission_analysis_json TEXT,
    next_scan TEXT,
    fetched_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    FOREIGN KEY (aoi_id) REFERENCES aoi (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_aoi_forecasts_aoi_id ON aoi_forecasts(aoi_id);
CREATE INDEX IF NOT EXISTS idx_aoi_forecasts_expires_at ON aoi_forecasts(expires_at);
