"""Domain entities used throughout the application."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class BoundingBox:
    min_longitude: float
    min_latitude: float
    max_longitude: float
    max_latitude: float

    def __post_init__(self) -> None:
        if not (-180 <= self.min_longitude <= 180 and -180 <= self.max_longitude <= 180):
            raise ValueError("Longitudes must be between -180 and 180")
        if not (-90 <= self.min_latitude <= 90 and -90 <= self.max_latitude <= 90):
            raise ValueError("Latitudes must be between -90 and 90")
        if self.min_longitude >= self.max_longitude:
            raise ValueError("Minimum longitude must be less than maximum longitude")
        if self.min_latitude >= self.max_latitude:
            raise ValueError("Minimum latitude must be less than maximum latitude")

    @classmethod
    def from_sequence(cls, values: list[float] | tuple[float, ...]) -> "BoundingBox":
        if len(values) != 4:
            raise ValueError("A bounding box must contain four coordinates")
        try:
            return cls(*(float(value) for value in values))
        except (TypeError, ValueError) as exc:
            raise ValueError("Bounding-box coordinates must be numeric") from exc

    def as_list(self) -> list[float]:
        return [self.min_longitude, self.min_latitude, self.max_longitude, self.max_latitude]

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.min_latitude + self.max_latitude) / 2,
            (self.min_longitude + self.max_longitude) / 2,
        )


@dataclass(frozen=True)
class Acquisition:
    acquired_at: datetime
    satellite: str
    product_type: str
    product_id: Optional[str] = None


@dataclass(frozen=True)
class ImageTile:
    bbox: BoundingBox
    width: int
    height: int
    x: int
    y: int


@dataclass(frozen=True)
class Scan:
    folder_name: str
    bbox: BoundingBox
    acquisition: Acquisition
    image_path: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ShipDetection:
    x: int
    y: int
    width: int
    height: int
    confidence: Optional[float] = None


@dataclass(frozen=True)
class Vessel:
    imo: str
    mmsi: str
    name: Optional[str] = None
    vessel_type: Optional[str] = None
    callsign: Optional[str] = None


@dataclass(frozen=True)
class VesselPosition:
    mmsi: str
    latitude: float
    longitude: float
    timestamp: datetime
    speed: Optional[float] = None
    heading: Optional[float] = None


@dataclass(frozen=True)
class AISRecord:
    vessel: Vessel
    position: VesselPosition

