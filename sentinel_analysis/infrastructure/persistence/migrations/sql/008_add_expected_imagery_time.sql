-- 008_add_expected_imagery_time.sql: Add expected_imagery_time column to post_pass_ingestions
ALTER TABLE post_pass_ingestions ADD COLUMN expected_imagery_time TEXT DEFAULT NULL;
