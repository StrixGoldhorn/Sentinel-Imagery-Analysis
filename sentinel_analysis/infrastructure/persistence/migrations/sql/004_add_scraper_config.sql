-- 004_add_scraper_config.sql: Table for managing scraper plugin activation and configuration
CREATE TABLE IF NOT EXISTS scraper_config (
    plugin_name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
