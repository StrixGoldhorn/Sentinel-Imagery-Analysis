-- Migration 017: Add provider health telemetry and operational quota tracking tables

CREATE TABLE IF NOT EXISTS provider_health (
    provider_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL NOT NULL DEFAULT 0.0,
    error_rate_pct REAL NOT NULL DEFAULT 0.0,
    last_success_at TEXT,
    last_failure_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    status_message TEXT NOT NULL DEFAULT 'Operational',
    endpoint_url TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_health_status ON provider_health(status);

CREATE TABLE IF NOT EXISTS provider_quotas (
    provider_id TEXT PRIMARY KEY,
    daily_request_limit INTEGER NOT NULL DEFAULT 1000,
    daily_requests_used INTEGER NOT NULL DEFAULT 0,
    monthly_request_limit INTEGER,
    monthly_requests_used INTEGER NOT NULL DEFAULT 0,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
    credit_balance_usd REAL,
    reset_at TEXT,
    updated_at TEXT NOT NULL
);
