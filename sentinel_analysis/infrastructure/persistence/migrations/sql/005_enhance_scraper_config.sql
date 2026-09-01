-- 005_enhance_scraper_config.sql: Add dynamic config_json and cooldown tracking to scraper_config
ALTER TABLE scraper_config ADD COLUMN config_json TEXT DEFAULT '{}';
ALTER TABLE scraper_config ADD COLUMN cooldown_until DATETIME DEFAULT NULL;
ALTER TABLE scraper_config ADD COLUMN consecutive_failures INTEGER DEFAULT 0;
ALTER TABLE scraper_config ADD COLUMN last_failure_reason TEXT DEFAULT NULL;
