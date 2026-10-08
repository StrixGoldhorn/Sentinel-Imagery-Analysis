"""Application-owned contract for background task execution."""

from collections.abc import Callable
from typing import Any, Optional, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import BackgroundTask


@runtime_checkable
class TaskQueue(Protocol):
    """Protocol for submitting and tracking asynchronous jobs."""

    def submit(
        self,
        task_type: str,
        task_id: str | None,
        target: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> BackgroundTask:
        """Submit a background job for asynchronous execution."""
        ...

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        """Retrieve task state by ID."""
        ...

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
    ) -> None:
        """Update progress percentage (0-100) and human-readable status message."""
        ...

    def list_tasks(
        self,
        status: str | None = None,
        task_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BackgroundTask]:
        """List background tasks with optional filtering."""
        ...

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a queued or running task. Returns True if successfully cancelled."""
        ...

    def recover_crashed_tasks(self, lease_timeout_seconds: float = 300) -> list[str]:
        """Identify and recover stale or crashed worker tasks. Returns recovered task IDs."""
        ...
