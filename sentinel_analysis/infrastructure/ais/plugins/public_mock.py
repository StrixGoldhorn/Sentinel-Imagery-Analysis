"""Example normalized plugin standing in for a public AIS provider."""

from datetime import datetime, timezone

from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition


class MockPublicAISPlugin:
    name = "MockPublicAISPlugin"

    def authenticate(self) -> None:
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: tuple[datetime | None, datetime | None],
    ) -> list[AISRecord]:
        latitude, longitude = bbox.center
        return [
            AISRecord(
                Vessel("9175535", "311029200", "CARGO KING", "Cargo", "C6YM7"),
                VesselPosition(
                    "311029200", latitude, longitude, datetime.now(timezone.utc), 14.5, 120,
                ),
            )
        ]

