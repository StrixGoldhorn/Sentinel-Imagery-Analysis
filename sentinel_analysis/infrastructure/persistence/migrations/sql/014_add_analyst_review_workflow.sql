-- Migration 014: Add analyst review workflow tables for review queue, dispositions, and immutable history

CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL,
    detection_idx INTEGER NOT NULL,
    disposition TEXT NOT NULL DEFAULT 'pending',
    reviewer_id TEXT,
    original_bbox TEXT NOT NULL,
    corrected_bbox TEXT,
    confidence REAL DEFAULT 0.0,
    vessel_class TEXT,
    comments TEXT,
    reason_codes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scan_id, detection_idx)
);

CREATE INDEX IF NOT EXISTS idx_reviews_scan ON reviews(scan_id);
CREATE INDEX IF NOT EXISTS idx_reviews_disposition ON reviews(disposition);
CREATE INDEX IF NOT EXISTS idx_reviews_updated ON reviews(updated_at);

CREATE TABLE IF NOT EXISTS review_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL,
    action TEXT NOT NULL,
    disposition TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    corrected_bbox TEXT,
    comments TEXT,
    metadata TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (review_id) REFERENCES reviews(review_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_review_history_review ON review_history(review_id);
CREATE INDEX IF NOT EXISTS idx_review_history_timestamp ON review_history(timestamp);
