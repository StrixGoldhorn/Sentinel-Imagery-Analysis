-- Preserve scan correlation when background task state is stored durably.
ALTER TABLE background_tasks ADD COLUMN scan_id TEXT;
