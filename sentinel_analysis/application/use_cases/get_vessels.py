"""Query normalized AIS vessel records and positions."""

from datetime import datetime
from typing import Any

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.domain.entities import BoundingBox


class GetVesselPositions:
    """Use case to query persisted AIS vessel positions with spatial/temporal filters."""

    def __init__(self, repository: AISRepository) -> None:
        self._repository = repository

    def execute(
        self,
        bbox: BoundingBox | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        limit: int = 500,
        latest_only: bool = True,
    ) -> list[dict[str, Any]]:
        return self._repository.get_vessel_positions(
            bbox=bbox,
            time_range=time_range,
            limit=limit,
            latest_only=latest_only,
        )
