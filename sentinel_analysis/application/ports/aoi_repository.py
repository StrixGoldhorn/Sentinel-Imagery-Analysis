from datetime import datetime
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from sentinel_analysis.domain.entities import AreaOfInterest


@runtime_checkable
class AreaOfInterestRepository(Protocol):
    """Persist areas of interest, prediction state, and flypast forecast caches."""

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

    def get_cached_forecast(self, aoi_id: int) -> Optional[dict[str, Any]]:
        ...

    def save_cached_forecast(
        self,
        aoi_id: int,
        forecast_data: dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> None:
        ...

    def clear_cached_forecast(self, aoi_id: int) -> None:
        ...

