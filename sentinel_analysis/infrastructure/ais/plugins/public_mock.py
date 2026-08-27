"""Example normalized plugin standing in for a public AIS provider."""

from datetime import datetime, timezone
from typing import Callable

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition


class MockPublicAISPlugin:
    name = "MockPublicAISPlugin"

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def authenticate(self) -> None:
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange,
    ) -> list[AISRecord]:
        latitude, longitude = bbox.center
        return [
            AISRecord(
                Vessel("9175535", "311029200", "CARGO KING", "Cargo", "C6YM7"),
                VesselPosition(
                    "311029200", latitude, longitude, self._clock(), 14.5, 120,
                ),
            )
        ]
