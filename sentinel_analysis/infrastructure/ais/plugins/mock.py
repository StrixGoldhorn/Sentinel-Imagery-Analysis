"""Generated AIS records for local development."""

import random
from datetime import datetime, timezone

from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition


class MockAISPlugin:
    name = "MockAISPlugin"

    def authenticate(self) -> None:
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: tuple[datetime | None, datetime | None],
    ) -> list[AISRecord]:
        records = []
        for index in range(3):
            imo = f"IMO{random.randint(1_000_000, 9_999_999)}"
            mmsi = f"2{random.randint(10_000_000, 99_999_999)}"
            records.append(
                AISRecord(
                    Vessel(imo, mmsi, f"Mock Vessel {index}", "Cargo", f"MOCK{index}"),
                    VesselPosition(
                        mmsi,
                        random.uniform(bbox.min_latitude, bbox.max_latitude),
                        random.uniform(bbox.min_longitude, bbox.max_longitude),
                        datetime.now(timezone.utc),
                        random.uniform(10, 25),
                        random.uniform(0, 360),
                    ),
                )
            )
        return records

