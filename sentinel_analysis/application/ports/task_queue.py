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
        task_id: str,
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
