"""Application port for metocean (meteorological and oceanographic) condition forecasting."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from sentinel_analysis.domain.acquisition_suitability import MetoceanCondition
from sentinel_analysis.domain.entities import BoundingBox


@runtime_checkable
class MetoceanProvider(Protocol):
    """Provides forecasted or current marine weather and sea state conditions over an area."""

    def get_conditions(
        self,
        bbox: BoundingBox,
        target_time: datetime,
    ) -> MetoceanCondition:
        """Retrieve metocean conditions (wind, waves, cloud cover) for a bounding box and time."""
        ...
