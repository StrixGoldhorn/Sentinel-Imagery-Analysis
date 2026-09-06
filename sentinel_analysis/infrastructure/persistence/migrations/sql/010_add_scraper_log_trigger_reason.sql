-- 010_add_scraper_log_trigger_reason.sql: Add trigger_reason column to scraper_logs
ALTER TABLE scraper_logs ADD COLUMN trigger_reason TEXT DEFAULT 'Manual / Unspecified';
