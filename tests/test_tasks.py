"""Unit tests for the ThreadedTaskQueue."""

import unittest
import time

from sentinel_analysis.infrastructure.tasks.queue import ThreadedTaskQueue


def test_task_execution_lifecycle() -> None:
    queue = ThreadedTaskQueue(max_workers=2)
    try:
        def sample_worker():
            time.sleep(0.1)
            return {"status": "ok", "items": 5}

        task = queue.submit("scan", "scan_123", sample_worker)
        assert task.task_type == "scan"
        assert task.scan_id == "scan_123"

        # Wait for completion
        time.sleep(0.3)

        retrieved = queue.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.status == "COMPLETED"
        assert retrieved.result == {"status": "ok", "items": 5}
    finally:
        queue.shutdown()


def test_task_failure_handling() -> None:
    queue = ThreadedTaskQueue(max_workers=2)
    try:
        def failing_worker():
            raise ValueError("Computation failed")

        task = queue.submit("scan", None, failing_worker)
        time.sleep(0.2)

        retrieved = queue.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.status == "FAILED"
        assert "Computation failed" in str(retrieved.error)
    finally:
        queue.shutdown()


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

