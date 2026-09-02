-- 007_add_scraper_tag.sql: Add tag / custom category column to scraper_config
ALTER TABLE scraper_config ADD COLUMN tag TEXT DEFAULT NULL;
