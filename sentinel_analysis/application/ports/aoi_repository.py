"""Persistence contract for areas of interest."""

from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from sentinel_analysis.domain.entities import AreaOfInterest


@runtime_checkable
class AreaOfInterestRepository(Protocol):
    """Persist areas of interest and their prediction state."""

    def list(self) -> Sequence[AreaOfInterest]:
        ...

    def add(self, aoi: AreaOfInterest) -> int:
        ...

    def get(self, aoi_id: int) -> AreaOfInterest | None:
        ...

    def update_prediction(
        self,
        aoi_id: int,
        next_scan: datetime,
        last_checked: datetime,
    ) -> None:
        ...
