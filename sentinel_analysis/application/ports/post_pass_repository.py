"""Port interface for post-pass imagery ingestion jobs repository."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from sentinel_analysis.domain.entities import PostPassIngestionJob


class PostPassIngestionRepository(Protocol):
    """Abstract persistence repository for autonomous post-pass ingestion jobs."""

    def add(self, job: PostPassIngestionJob) -> int:
        """Insert a new post-pass ingestion job and return its generated ID."""
        ...

    def get(self, job_id: int) -> Optional[PostPassIngestionJob]:
        """Fetch a specific job by ID."""
        ...

    def find_by_aoi_and_pass(self, aoi_id: int, pass_time: datetime) -> Optional[PostPassIngestionJob]:
        """Find an existing job for a specific AOI and satellite pass timestamp."""
        ...

    def get_active_jobs(self) -> list[PostPassIngestionJob]:
        """Return all jobs that are currently active."""
        ...

    def get_jobs_due_for_poll(self, now: datetime) -> list[PostPassIngestionJob]:
        """Return all jobs in POLLING_CATALOG status where next_poll_at <= now or next_poll_at is NULL."""
        ...

    def claim_jobs_due_for_poll(self, now: datetime, limit: int = 1) -> list[PostPassIngestionJob]:
        """Atomically claim due jobs so only one worker can process each job."""
        ...

    def claim_job(self, job_id: int, now: datetime) -> Optional[PostPassIngestionJob]:
        """Atomically claim one polling job, returning None if it is not claimable."""
        ...

    def update(self, job: PostPassIngestionJob) -> None:
        """Update job fields in the repository."""
        ...

    def list(self, limit: int = 50) -> list[PostPassIngestionJob]:
        """Return recent jobs ordered by pass_time descending."""
        ...

    def delete(self, job_id: int) -> None:
        """Delete an ingestion job by ID."""
        ...

    def reset_for_retry(self, job_id: int, now: datetime) -> Optional[PostPassIngestionJob]:
        """Atomically reset a terminal job if it remains retryable."""
        ...

    def list_events(self, job_id: int, limit: int = 100) -> list[dict[str, object]]:
        """Return the recorded state-transition history for one job."""
        ...
