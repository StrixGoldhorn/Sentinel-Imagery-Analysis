-- 002_add_aoi_scheduler.sql: Add auto_capture_enabled toggle to areas of interest
ALTER TABLE aoi ADD COLUMN auto_capture_enabled INTEGER DEFAULT 0;
