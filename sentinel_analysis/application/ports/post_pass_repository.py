"""Port interface for post-pass imagery ingestion jobs repository."""

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
        """Return all jobs that are currently active (PENDING_PASS, POLLING_CATALOG, INGESTING)."""
        ...

    def get_jobs_due_for_poll(self, now: datetime) -> list[PostPassIngestionJob]:
        """Return all jobs in POLLING_CATALOG status where next_poll_at <= now or next_poll_at is NULL."""
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
