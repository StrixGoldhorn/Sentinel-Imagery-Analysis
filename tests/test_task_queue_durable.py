"""Unit and integration tests for durable SQLite task queue with worker crash recovery."""

import shutil
import sqlite3
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel_analysis.domain.entities import BackgroundTask
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase
from sentinel_analysis.infrastructure.tasks.queue import ThreadedTaskQueue
from sentinel_analysis.interfaces.web.application import create_app
from sentinel_analysis.bootstrap.container import ApplicationContainer


class TestDurableTaskQueue(unittest.TestCase):
    def setUp(self):
        from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
        self.temp_dir = Path(__file__).resolve().parent / "runtime" / "durable_task_queue"
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / "tasks.db"
        MigrationRunner(self.db_path).run_migrations()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_wal_mode_and_synchronous_configured(self):
        db = SQLiteDatabase(self.db_path)
        with db.connection() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            sync_mode = conn.execute("PRAGMA synchronous;").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        # synchronous = NORMAL is 1 in SQLite
        self.assertEqual(sync_mode, 1)

    def test_worker_crash_recovery(self):
        # Insert a simulated task from a crashed worker directly into SQLite
        expired = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        db = SQLiteDatabase(self.db_path)
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO background_tasks (
                    task_id, task_type, status, progress, message, created_at, owner_id, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("task_crashed_1", "scan", "RUNNING", 10.0, "Processing...", expired, "crashed-worker-uuid", expired),
            )

        # Now boot queue2 - crash recovery must detect and mark it FAILED
        queue2 = ThreadedTaskQueue(max_workers=2, database_path=self.db_path)
        try:
            recovered_task = queue2.get_task("task_crashed_1")
            self.assertIsNotNone(recovered_task)
            self.assertEqual(recovered_task.status, "FAILED")
            self.assertIn("crash", recovered_task.message.lower())
        finally:
            queue2.shutdown(wait=False)

    def test_explicit_recover_crashed_tasks(self):
        queue = ThreadedTaskQueue(max_workers=2, database_path=self.db_path)
        try:
            # Insert another orphaned task manually
            expired = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            db = SQLiteDatabase(self.db_path)
            with db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO background_tasks (
                        task_id, task_type, status, progress, message, created_at, owner_id, lease_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("dead_worker_task", "analysis", "RUNNING", 50.0, "Running...", expired, "dead_owner", expired),
                )
            recovered = queue.recover_crashed_tasks()
            self.assertIn("dead_worker_task", recovered)
            task = queue.get_task("dead_worker_task")
            self.assertEqual(task.status, "FAILED")
        finally:
            queue.shutdown(wait=False)

    def test_list_tasks_and_pagination(self):
        queue = ThreadedTaskQueue(max_workers=2, database_path=self.db_path)
        try:
            # Submit several quick tasks
            for i in range(5):
                queue.submit("batch_scan", f"task_{i}", lambda idx=i: {"index": idx})
            time.sleep(0.3)

            all_tasks = queue.list_tasks()
            self.assertGreaterEqual(len(all_tasks), 5)

            paged = queue.list_tasks(limit=2, offset=0)
            self.assertEqual(len(paged), 2)

            filtered = queue.list_tasks(status="COMPLETED")
            self.assertTrue(all(t.status == "COMPLETED" for t in filtered))

            by_type = queue.list_tasks(task_type="batch_scan")
            self.assertTrue(all(t.task_type == "batch_scan" for t in by_type))
        finally:
            queue.shutdown(wait=True)

    def test_cancel_task(self):
        import threading
        release_event = threading.Event()
        queue = ThreadedTaskQueue(max_workers=1, database_path=self.db_path)
        try:
            # Submit a long-running task that pauses on release_event
            task = queue.submit("long_job", "task_cancel_1", lambda: release_event.wait(timeout=2))
            time.sleep(0.05)
            cancelled = queue.cancel_task("task_cancel_1")
            self.assertTrue(cancelled)

            retrieved = queue.get_task("task_cancel_1")
            self.assertEqual(retrieved.status, "CANCELLED")

            # Cancelling again returns False
            self.assertFalse(queue.cancel_task("task_cancel_1"))
        finally:
            release_event.set()
            queue.shutdown(wait=True)

    def test_prune_tasks(self):
        queue = ThreadedTaskQueue(max_workers=1, database_path=self.db_path)
        try:
            old_time = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
            db = SQLiteDatabase(self.db_path)
            with db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO background_tasks (
                        task_id, task_type, status, progress, message, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("ancient_task", "scan", "COMPLETED", 100.0, "Done", old_time, old_time),
                )
            deleted = queue.prune_tasks(older_than_days=7)
            self.assertGreaterEqual(deleted, 1)
            self.assertIsNone(queue.get_task("ancient_task"))
        finally:
            queue.shutdown(wait=False)


class TestTaskWebEndpoints(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def test_list_and_cancel_web_api(self):
        # List tasks
        resp = self.client.get("/api/tasks")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("tasks", data)
        self.assertIn("count", data)

        # Trigger recover endpoint
        rec_resp = self.client.post("/api/tasks/recover")
        self.assertEqual(rec_resp.status_code, 200)
        rec_data = rec_resp.get_json()
        self.assertIn("recovered_count", rec_data)

        # Attempt to cancel non-existent task
        cancel_resp = self.client.post("/api/tasks/non-existent-task-id/cancel")
        self.assertEqual(cancel_resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
