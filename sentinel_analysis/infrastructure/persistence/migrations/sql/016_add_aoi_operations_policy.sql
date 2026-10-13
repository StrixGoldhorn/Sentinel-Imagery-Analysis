-- 016_add_aoi_operations_policy.sql: Add operational priority, collection budget, desired revisit rate, and retention policy
ALTER TABLE aoi ADD COLUMN priority TEXT DEFAULT 'MEDIUM';
ALTER TABLE aoi ADD COLUMN collection_budget_usd REAL DEFAULT 0.0;
ALTER TABLE aoi ADD COLUMN budget_spent_usd REAL DEFAULT 0.0;
ALTER TABLE aoi ADD COLUMN desired_revisit_rate_hours REAL DEFAULT NULL;
ALTER TABLE aoi ADD COLUMN retention_policy_days INTEGER DEFAULT NULL;
