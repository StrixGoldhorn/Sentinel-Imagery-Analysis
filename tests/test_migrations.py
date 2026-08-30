"""Unit tests for the SQLite Migration Runner."""

import unittest
import sqlite3
from pathlib import Path
from contextlib import closing

from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner


class MigrationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(__file__).resolve().parent / "runtime" / "migration_test.db"
        self.db_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_migrations_applied_idempotently(self) -> None:
        runner = MigrationRunner(self.db_path)
        applied_first = runner.run_migrations()
        self.assertGreaterEqual(len(applied_first), 3)

        # Verify tables created
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.cursor()
            tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            self.assertIn("aoi", tables)
            self.assertIn("vessels", tables)
            self.assertIn("vessel_locations", tables)
            self.assertIn("background_tasks", tables)
            self.assertIn("_schema_migrations", tables)

            # Verify auto_capture_enabled column exists in aoi
            columns = [row[1] for row in cursor.execute("PRAGMA table_info(aoi)").fetchall()]
            self.assertIn("auto_capture_enabled", columns)


        # Running again should apply 0 new migrations
        applied_second = runner.run_migrations()
        self.assertEqual(len(applied_second), 0)



if __name__ == "__main__":
    unittest.main()
