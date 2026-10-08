"""Threaded in-memory implementation of the TaskQueue port."""

import concurrent.futures
import threading
import uuid
import logging
import json
from dataclasses import replace
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from pathlib import Path

from sentinel_analysis.application.shutdown import shutdown_coordinator
from sentinel_analysis.domain.entities import BackgroundTask
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase

logger = logging.getLogger(__name__)



class ThreadedTaskQueue:
    """Thread-safe background task executor backed by a thread pool."""

    def __init__(self, max_workers: int = 8, database_path: Path | str | None = None) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._futures: dict[str, concurrent.futures.Future[Any]] = {}
        self._heartbeat_stops: dict[str, threading.Event] = {}
        self._owner_id = str(uuid.uuid4())
        self._database: SQLiteDatabase | None = None
        if database_path is not None:
            MigrationRunner(database_path).run_migrations()
            self._database = SQLiteDatabase(database_path)
            self._load_persisted_tasks()

    @staticmethod
    def _parse_dt(value: object) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _row_to_task(self, row: Any) -> BackgroundTask:
        result = None
        if row["result_json"]:
            try:
                decoded = json.loads(row["result_json"])
                result = decoded if isinstance(decoded, dict) else {"data": decoded}
            except (TypeError, ValueError):
                result = None
        return BackgroundTask(
            task_id=row["task_id"],
            task_type=row["task_type"],
            status=row["status"],
            progress=float(row["progress"] or 0.0),
            message=row["message"] or "",
            scan_id=row["scan_id"] if "scan_id" in row.keys() else None,
            created_at=self._parse_dt(row["created_at"]),
            completed_at=self._parse_dt(row["completed_at"]),
            result=result,
            error=row["error_text"],
        )

    def recover_crashed_tasks(self, lease_timeout_seconds: float = 300) -> list[str]:
        """Detect tasks abandoned by crashed workers or previous server lifecycles and recover their state."""
        recovered_ids: list[str] = []
        if self._database is None:
            return recovered_ids
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(
                """
                SELECT task_id, status FROM background_tasks
                WHERE status IN ('QUEUED', 'RUNNING')
                  AND (
                    lease_expires_at IS NULL
                    OR lease_expires_at <= ?
                    OR owner_id != ?
                  )
                """,
                (now_iso, self._owner_id),
            ).fetchall()
            for row in rows:
                task_id = row["task_id"]
                conn.execute(
                    """
                    UPDATE background_tasks
                    SET status = 'FAILED',
                        message = 'Worker crash detected or lease expired',
                        completed_at = ?,
                        error_text = 'Task worker died or heartbeat lease expired'
                    WHERE task_id = ?
                    """,
                    (now_iso, task_id),
                )
                recovered_ids.append(task_id)

        with self._lock:
            for task_id in recovered_ids:
                if task_id in self._tasks:
                    current = self._tasks[task_id]
                    self._tasks[task_id] = replace(
                        current,
                        status="FAILED",
                        message="Worker crash detected or lease expired",
                        completed_at=now,
                        error="Task worker died or heartbeat lease expired",
                    )
        return recovered_ids

    def _load_persisted_tasks(self) -> None:
        if self._database is None:
            return
        self.recover_crashed_tasks()
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(
                "SELECT * FROM background_tasks ORDER BY created_at DESC LIMIT 1000"
            ).fetchall()
        for row in rows:
            task = self._row_to_task(row)
            self._tasks[task.task_id] = task

    def _persist(self, task: BackgroundTask) -> None:
        if self._database is None:
            return
        result_json = json.dumps(task.result, default=str) if task.result is not None else None
        active = task.status in {"QUEUED", "RUNNING"}
        heartbeat = datetime.now(timezone.utc) if active else None
        lease_expires = heartbeat + timedelta(minutes=5) if heartbeat else None
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO background_tasks (
                    task_id, task_type, status, progress, message, scan_id,
                    created_at, completed_at, result_json, error_text,
                    owner_id, heartbeat_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    progress = excluded.progress,
                    message = excluded.message,
                    scan_id = excluded.scan_id,
                    completed_at = excluded.completed_at,
                    result_json = excluded.result_json,
                    error_text = excluded.error_text,
                    owner_id = excluded.owner_id,
                    heartbeat_at = excluded.heartbeat_at,
                    lease_expires_at = excluded.lease_expires_at
                """,
                (
                    task.task_id,
                    task.task_type,
                    task.status,
                    task.progress,
                    task.message,
                    task.scan_id,
                    task.created_at.isoformat() if task.created_at else None,
                    task.completed_at.isoformat() if task.completed_at else None,
                    result_json,
                    task.error,
                    self._owner_id,
                    heartbeat.isoformat() if heartbeat else None,
                    lease_expires.isoformat() if lease_expires else None,
                ),
            )

    def _heartbeat(self, task_id: str) -> None:
        if self._database is None:
            return
        now = datetime.now(timezone.utc)
        with self._database.connection() as conn:
            conn.execute(
                """
                UPDATE background_tasks SET heartbeat_at = ?, lease_expires_at = ?
                WHERE task_id = ? AND owner_id = ? AND status IN ('QUEUED', 'RUNNING')
                """,
                (now.isoformat(), (now + timedelta(minutes=5)).isoformat(), task_id, self._owner_id),
            )

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
            status="QUEUED",
            progress=0.0,
            message="Task initialized",
            scan_id=None,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            if actual_id in self._tasks:
                raise ValueError(f"Task ID already exists: {actual_id}")
            self._tasks[actual_id] = initial
            self._persist(initial)

        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(actual_id, heartbeat_stop),
            daemon=True,
            name=f"task-heartbeat-{actual_id}",
        )
        with self._lock:
            self._heartbeat_stops[actual_id] = heartbeat_stop
        heartbeat_thread.start()

        def _worker() -> None:
            if shutdown_coordinator.is_shutting_down:
                logger.info("Skipping task %s (%s): application is shutting down", actual_id, task_type)
                with self._lock:
                    current = self._tasks.get(actual_id)
                    self._tasks[actual_id] = BackgroundTask(
                        task_id=actual_id,
                        task_type=task_type,
                        status="FAILED",
                        progress=0.0,
                        message="Cancelled: application is shutting down",
                        scan_id=None,
                        created_at=current.created_at if current else None,
                        completed_at=datetime.now(timezone.utc),
                        error="Application is shutting down",
                    )
                    self._persist(self._tasks[actual_id])
                heartbeat_stop.set()
                with self._lock:
                    self._heartbeat_stops.pop(actual_id, None)
                return

            try:
                with self._lock:
                    current = self._tasks.get(actual_id)
                    if current is not None:
                        self._tasks[actual_id] = replace(
                            current,
                            status="RUNNING",
                            message="Task started",
                        )
                        self._persist(self._tasks[actual_id])
                result = target(*args, **kwargs)
                with self._lock:
                    current = self._tasks.get(actual_id)
                    self._tasks[actual_id] = BackgroundTask(
                        task_id=actual_id,
                        task_type=task_type,
                        status="COMPLETED",
                        progress=100.0,
                        message="Task completed successfully",
                        scan_id=self._result_scan_id(result),
                        created_at=current.created_at if current else None,
                        completed_at=datetime.now(timezone.utc),
                        result=result if isinstance(result, dict) else {"data": result},
                    )
                    self._persist(self._tasks[actual_id])
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
                        scan_id=current.scan_id if current else None,
                        created_at=current.created_at if current else None,
                        completed_at=datetime.now(timezone.utc),
                        error=str(exc) or "Unknown error",
                    )
                    self._persist(self._tasks[actual_id])
            finally:
                heartbeat_stop.set()
                with self._lock:
                    self._heartbeat_stops.pop(actual_id, None)

        try:
            future = self._executor.submit(_worker)
            with self._lock:
                self._futures[actual_id] = future
        except Exception as exc:
            heartbeat_stop.set()
            with self._lock:
                self._heartbeat_stops.pop(actual_id, None)
                current = self._tasks[actual_id]
                self._tasks[actual_id] = replace(
                    current,
                    status="FAILED",
                    message="Task could not be queued",
                    completed_at=datetime.now(timezone.utc),
                    error=str(exc) or "Task executor rejected submission",
                )
                self._persist(self._tasks[actual_id])
            raise
        return initial

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        if self._database is not None:
            self._load_one(task_id)
        with self._lock:
            return self._tasks.get(task_id)

    @staticmethod
    def _result_scan_id(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        for key in ("scan_id", "scan_folder", "folderName", "folder_name"):
            value = result.get(key)
            if value:
                return str(value)
        return None

    def _heartbeat_loop(self, task_id: str, stop_event: threading.Event) -> None:
        while not stop_event.wait(30.0):
            self._heartbeat(task_id)

    def _load_one(self, task_id: str) -> None:
        if self._database is None:
            return
        with self._database.connection(rows=True) as conn:
            row = conn.execute("SELECT * FROM background_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return
        task = self._row_to_task(row)
        with self._lock:
            self._tasks[task_id] = task

    def list_tasks(
        self,
        status: str | None = None,
        task_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BackgroundTask]:
        """List background tasks with optional filtering and pagination."""
        if self._database is not None:
            query = "SELECT * FROM background_tasks WHERE 1=1"
            params: list[Any] = []
            if status:
                query += " AND UPPER(status) = UPPER(?)"
                params.append(status)
            if task_type:
                query += " AND task_type = ?"
                params.append(task_type)
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([max(1, limit), max(0, offset)])
            with self._database.connection(rows=True) as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
            return [self._row_to_task(row) for row in rows]
        with self._lock:
            tasks = list(self._tasks.values())
            if status:
                tasks = [t for t in tasks if t.status.upper() == status.upper()]
            if task_type:
                tasks = [t for t in tasks if t.task_type == task_type]
            tasks.sort(key=lambda t: t.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            return tasks[offset : offset + limit]

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a queued or running task. Returns True if cancelled, False otherwise."""
        with self._lock:
            if task_id not in self._tasks and self._database is not None:
                self._load_one(task_id)
            task = self._tasks.get(task_id)
            if task is None or task.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                return False

            future = self._futures.get(task_id)
            if future is not None:
                future.cancel()

            stop = self._heartbeat_stops.pop(task_id, None)
            if stop is not None:
                stop.set()

            cancelled = replace(
                task,
                status="CANCELLED",
                message="Task cancelled by user",
                completed_at=datetime.now(timezone.utc),
                error="Cancelled",
            )
            self._tasks[task_id] = cancelled
            self._persist(cancelled)
            return True

    def prune_tasks(self, older_than_days: int = 7) -> int:
        """Prune old terminal tasks to free SQLite storage."""
        if self._database is None:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self._database.connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM background_tasks
                WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED')
                  AND completed_at IS NOT NULL
                  AND completed_at < ?
                """,
                (cutoff,),
            )
            deleted = cursor.rowcount
        return max(0, deleted)

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
    ) -> None:
        with self._lock:
            current = self._tasks.get(task_id)
            if current and current.status == "RUNNING":
                self._tasks[task_id] = replace(
                    current,
                    progress=min(100.0, max(0.0, float(progress))),
                    message=message or current.message,
                )
                self._persist(self._tasks[task_id])

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        if cancel_futures:
            with self._lock:
                pending = list(self._futures.items())
            for task_id, future in pending:
                if future.cancel():
                    stop = self._heartbeat_stops.pop(task_id, None)
                    if stop is not None:
                        stop.set()
                    with self._lock:
                        current = self._tasks.get(task_id)
                        if current is not None:
                            cancelled = replace(
                                current, status="FAILED", message="Task cancelled during shutdown",
                                completed_at=datetime.now(timezone.utc), error="Application is shutting down",
                            )
                            self._tasks[task_id] = cancelled
                            self._persist(cancelled)
        try:
            self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
        except TypeError:
            self._executor.shutdown(wait=wait)
