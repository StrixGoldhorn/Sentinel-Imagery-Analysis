-- 009_add_system_settings.sql: Centralized configuration table for feature-specific settings
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    section TEXT NOT NULL,
    value_json TEXT NOT NULL,
    description TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_system_settings_section ON system_settings(section);
