"""Unit tests for the ThreadedTaskQueue."""

import unittest
import time
import threading
from pathlib import Path
import shutil

from sentinel_analysis.infrastructure.tasks.queue import ThreadedTaskQueue


def test_task_execution_lifecycle() -> None:
    queue = ThreadedTaskQueue(max_workers=2)
    try:
        def sample_worker():
            time.sleep(0.1)
            return {"status": "ok", "items": 5, "folderName": "scan_123"}

        task = queue.submit("scan", "scan_123", sample_worker)
        assert task.task_type == "scan"
        assert task.scan_id is None

        # Wait for completion
        time.sleep(0.3)

        retrieved = queue.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.status == "COMPLETED"
        assert retrieved.result == {"status": "ok", "items": 5, "folderName": "scan_123"}
        assert retrieved.scan_id == "scan_123"
    finally:
        queue.shutdown()


def test_task_failure_handling() -> None:
    from unittest.mock import patch

    queue = ThreadedTaskQueue(max_workers=2)
    try:
        def failing_worker():
            raise ValueError("Computation failed")

        with patch("sentinel_analysis.infrastructure.tasks.queue.logger.exception"):
            task = queue.submit("scan", None, failing_worker)
            time.sleep(0.2)

            retrieved = queue.get_task(task.task_id)
            assert retrieved is not None
            assert retrieved.status == "FAILED"
            assert "Computation failed" in str(retrieved.error)
    finally:
        queue.shutdown()


def test_waiting_task_reports_queued() -> None:
    queue = ThreadedTaskQueue(max_workers=1)
    release = threading.Event()
    try:
        queue.submit("scan", "first", lambda: release.wait(timeout=2))
        second = queue.submit("scan", "second", lambda: {"ok": True})
        assert second.status == "QUEUED"
        assert queue.get_task("second").status == "QUEUED"
    finally:
        release.set()
        queue.shutdown(wait=True)


def test_progress_update_preserves_task_identity() -> None:
    queue = ThreadedTaskQueue(max_workers=1)
    release = threading.Event()
    try:
        task = queue.submit("scan", "scan_with_progress", lambda: release.wait(timeout=2))
        for _ in range(20):
            if queue.get_task(task.task_id).status == "RUNNING":
                break
            time.sleep(0.01)
        queue.update_progress(task.task_id, 42, "Downloading tiles")
        updated = queue.get_task(task.task_id)
        assert updated.progress == 42
        assert updated.message == "Downloading tiles"
        assert updated.task_id == "scan_with_progress"
        assert updated.scan_id is None
    finally:
        release.set()
        queue.shutdown(wait=True)


def test_task_state_is_loaded_from_database_after_restart() -> None:
    temp_dir = Path(__file__).resolve().parent / "runtime" / "task_persistence"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        db_path = temp_dir / "tasks.db"
        first_queue = ThreadedTaskQueue(max_workers=1, database_path=db_path)
        task = first_queue.submit(
            "scan",
            "persisted_scan",
            lambda: {"ok": True, "folderName": "persisted_scan"},
        )
        for _ in range(50):
            if first_queue.get_task(task.task_id).status == "COMPLETED":
                break
            time.sleep(0.02)
        first_queue.shutdown(wait=True)

        second_queue = ThreadedTaskQueue(max_workers=1, database_path=db_path)
        try:
            restored = second_queue.get_task("persisted_scan")
            assert restored is not None
            assert restored.status == "COMPLETED"
            assert restored.result == {"ok": True, "folderName": "persisted_scan"}
            assert restored.scan_id == "persisted_scan"
        finally:
            second_queue.shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()
