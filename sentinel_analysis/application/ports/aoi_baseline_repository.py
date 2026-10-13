"""Persistence contract for AOI historical baseline models."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from sentinel_analysis.domain.aoi_baseline import AOIBaseline


@runtime_checkable
class AOIBaselineRepository(Protocol):
    """Storage abstraction for historical baseline models."""

    def save(self, baseline: AOIBaseline) -> None:
        """Persist or overwrite an AOI baseline model."""
        ...

    def get(self, aoi_id: int) -> Optional[AOIBaseline]:
        """Retrieve the latest baseline model for an AOI ID."""
        ...

    def list_all(self) -> Sequence[AOIBaseline]:
        """List all persisted baseline models."""
        ...

    def delete(self, aoi_id: int) -> None:
        """Delete baseline model for an AOI."""
        ...
