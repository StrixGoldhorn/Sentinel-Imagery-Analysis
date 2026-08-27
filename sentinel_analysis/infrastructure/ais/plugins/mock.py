"""Generated AIS records for local development."""

import random
from datetime import datetime, timezone
from typing import Callable

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition


class MockAISPlugin:
    name = "MockAISPlugin"

    def __init__(
        self,
        random_source: random.Random | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._random = random_source or random.Random()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def authenticate(self) -> None:
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange,
    ) -> list[AISRecord]:
        records = []
        for index in range(3):
            imo = f"IMO{self._random.randint(1_000_000, 9_999_999)}"
            mmsi = f"2{self._random.randint(10_000_000, 99_999_999)}"
            records.append(
                AISRecord(
                    Vessel(imo, mmsi, f"Mock Vessel {index}", "Cargo", f"MOCK{index}"),
                    VesselPosition(
                        mmsi,
                        self._random.uniform(bbox.min_latitude, bbox.max_latitude),
                        self._random.uniform(bbox.min_longitude, bbox.max_longitude),
                        self._clock(),
                        self._random.uniform(10, 25),
                        self._random.uniform(0, 360),
                    ),
                )
            )
        return records
