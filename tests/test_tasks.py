"""Unit tests for the ThreadedTaskQueue."""

import unittest
import time

from sentinel_analysis.infrastructure.tasks.queue import ThreadedTaskQueue


class TaskQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = ThreadedTaskQueue(max_workers=2)

    def tearDown(self) -> None:
        self.queue.shutdown()

    def test_task_execution_lifecycle(self) -> None:
        def sample_worker():
            time.sleep(0.1)
            return {"status": "ok", "items": 5}

        task = self.queue.submit("scan", "scan_123", sample_worker)
        self.assertEqual(task.task_type, "scan")
        self.assertEqual(task.scan_id, "scan_123")

        # Wait for completion
        time.sleep(0.3)

        retrieved = self.queue.get_task(task.task_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.status, "COMPLETED")
        self.assertEqual(retrieved.result, {"status": "ok", "items": 5})

    def test_task_failure_handling(self) -> None:
        def failing_worker():
            raise ValueError("Computation failed")

        task = self.queue.submit("scan", None, failing_worker)
        time.sleep(0.2)

        retrieved = self.queue.get_task(task.task_id)
        self.assertEqual(retrieved.status, "FAILED")
        self.assertIn("Computation failed", retrieved.error)


if __name__ == "__main__":
    unittest.main()
