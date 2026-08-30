-- 003_add_tasks_table.sql: Background task execution state
CREATE TABLE IF NOT EXISTS background_tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL DEFAULT 0.0,
    message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    result_json TEXT,
    error_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_tasks_status ON background_tasks(status);
