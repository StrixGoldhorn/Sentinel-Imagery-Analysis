-- Auditable state transitions for post-pass ingestion jobs.
CREATE TABLE IF NOT EXISTS post_pass_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES post_pass_ingestions(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_post_pass_job_events_job_time
ON post_pass_job_events(job_id, created_at DESC);
