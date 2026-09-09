-- Persist causal prediction evidence, coordinate multi-process workers, and
-- retain task leases so one process cannot invalidate another's live work.
ALTER TABLE post_pass_ingestions ADD COLUMN relative_orbit INTEGER;
ALTER TABLE post_pass_ingestions ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'MANUAL';
ALTER TABLE post_pass_ingestions ADD COLUMN prediction_source TEXT;
ALTER TABLE post_pass_ingestions ADD COLUMN workflow_id TEXT;
ALTER TABLE post_pass_ingestions ADD COLUMN basis_product_id TEXT;
ALTER TABLE post_pass_ingestions ADD COLUMN basis_acquisition_time TEXT;
ALTER TABLE post_pass_ingestions ADD COLUMN basis_satellite TEXT;
ALTER TABLE post_pass_ingestions ADD COLUMN basis_relative_orbit INTEGER;
ALTER TABLE post_pass_ingestions ADD COLUMN deleted_at TEXT;

ALTER TABLE background_tasks ADD COLUMN owner_id TEXT;
ALTER TABLE background_tasks ADD COLUMN heartbeat_at TEXT;
ALTER TABLE background_tasks ADD COLUMN lease_expires_at TEXT;

CREATE TABLE IF NOT EXISTS scheduler_leases (
    name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automatic_ais_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aoi_id INTEGER NOT NULL REFERENCES aoi(id) ON DELETE CASCADE,
    pass_time TEXT NOT NULL,
    tick_time TEXT NOT NULL,
    status TEXT NOT NULL,
    post_pass_job_id INTEGER REFERENCES post_pass_ingestions(id),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(aoi_id, pass_time, tick_time)
);

CREATE INDEX IF NOT EXISTS idx_post_pass_not_deleted
ON post_pass_ingestions(deleted_at, status, next_poll_at);
