"""Threaded in-memory implementation of the TaskQueue port."""

import concurrent.futures
import threading
import uuid
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

from sentinel_analysis.domain.entities import BackgroundTask

logger = logging.getLogger(__name__)



class ThreadedTaskQueue:
    """Thread-safe background task executor backed by a thread pool."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._tasks: dict[str, BackgroundTask] = {}

    def submit(
        self,
        task_type: str,
        task_id: str | None,
        target: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> BackgroundTask:
        actual_id = task_id or str(uuid.uuid4())
        initial = BackgroundTask(
            task_id=actual_id,
            task_type=task_type,
            status="RUNNING",
            progress=0.0,
            message="Task initialized",
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._tasks[actual_id] = initial

        def _worker() -> None:
            try:
                result = target(*args, **kwargs)
                with self._lock:
                    current = self._tasks.get(actual_id)
                    self._tasks[actual_id] = BackgroundTask(
                        task_id=actual_id,
                        task_type=task_type,
                        status="COMPLETED",
                        progress=100.0,
                        message="Task completed successfully",
                        created_at=current.created_at if current else None,
                        completed_at=datetime.now(timezone.utc),
                        result=result if isinstance(result, dict) else {"data": result},
                    )
            except Exception as exc:
                logger.exception("Background task %s (%s) failed", actual_id, task_type, exc_info=exc)
                with self._lock:

                    current = self._tasks.get(actual_id)
                    self._tasks[actual_id] = BackgroundTask(
                        task_id=actual_id,
                        task_type=task_type,
                        status="FAILED",
                        progress=current.progress if current else 0.0,
                        message=str(exc) or "Task execution failed",
                        created_at=current.created_at if current else None,
                        completed_at=datetime.now(timezone.utc),
                        error=str(exc) or "Unknown error",
                    )

        self._executor.submit(_worker)
        return initial

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
    ) -> None:
        with self._lock:
            current = self._tasks.get(task_id)
            if current and current.status == "RUNNING":
                self._tasks[task_id] = BackgroundTask(
                    task_id=current.task_id,
                    task_type=current.task_type,
                    status="RUNNING",
                    progress=min(100.0, max(0.0, float(progress))),
                    message=message or current.message,
                    created_at=current.created_at,
                )

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
