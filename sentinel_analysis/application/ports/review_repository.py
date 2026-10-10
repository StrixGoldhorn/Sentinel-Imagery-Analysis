"""Port definition for analyst review repository."""

from __future__ import annotations

from typing import Protocol

from sentinel_analysis.domain.review import ReviewHistoryEntry, ReviewRecord


class ReviewRepository(Protocol):
    """Repository interface for storing and querying analyst reviews and audit history."""

    def get(self, review_id: str) -> ReviewRecord | None:
        """Fetch review record by unique identifier."""
        ...

    def get_by_id(self, review_id: str) -> ReviewRecord | None:
        """Fetch review record by unique identifier (alias for get)."""
        ...

    def get_by_scan_and_index(self, scan_id: str, detection_idx: int) -> ReviewRecord | None:
        """Fetch review record for a specific detection in a scan."""
        ...

    def save(self, record: ReviewRecord) -> None:
        """Persist or update a review record."""
        ...

    def add_history(self, entry: ReviewHistoryEntry) -> int:
        """Append an immutable audit entry to history."""
        ...

    def get_history(self, review_id: str) -> list[ReviewHistoryEntry]:
        """Fetch all immutable history records for a review, ordered chronologically."""
        ...

    def list_reviews(
        self,
        disposition: str | None = None,
        scan_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReviewRecord]:
        """List review records matching optional filters."""
        ...

    def count_by_disposition(self) -> dict[str, int]:
        """Return counts of reviews grouped by disposition."""
        ...
