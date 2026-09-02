"""Domain entities used throughout the application."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import cos, isfinite, radians
from typing import Optional

from sentinel_analysis.domain.exceptions import DomainValidationError


def _number(value: object, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} must be numeric") from exc
    if not isfinite(normalized):
        raise DomainValidationError(f"{field_name} must be finite")
    return normalized


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} must not be empty")
    return value.strip()


def _optional_text(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} must be a datetime")
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _non_negative_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class BoundingBox:
    min_longitude: float
    min_latitude: float
    max_longitude: float
    max_latitude: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_longitude", _number(self.min_longitude, "Minimum longitude"))
        object.__setattr__(self, "min_latitude", _number(self.min_latitude, "Minimum latitude"))
        object.__setattr__(self, "max_longitude", _number(self.max_longitude, "Maximum longitude"))
        object.__setattr__(self, "max_latitude", _number(self.max_latitude, "Maximum latitude"))
        if not (-180 <= self.min_longitude <= 180 and -180 <= self.max_longitude <= 180):
            raise DomainValidationError("Longitudes must be between -180 and 180")
        if not (-90 <= self.min_latitude <= 90 and -90 <= self.max_latitude <= 90):
            raise DomainValidationError("Latitudes must be between -90 and 90")
        if self.min_longitude >= self.max_longitude:
            raise DomainValidationError("Minimum longitude must be less than maximum longitude")
        if self.min_latitude >= self.max_latitude:
            raise DomainValidationError("Minimum latitude must be less than maximum latitude")

    @classmethod
    def from_sequence(cls, values: list[float] | tuple[float, ...]) -> "BoundingBox":
        if len(values) != 4:
            raise DomainValidationError("A bounding box must contain four coordinates")
        try:
            coordinates = tuple(float(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("Bounding-box coordinates must be numeric") from exc
        return cls(*coordinates)

    def as_list(self) -> list[float]:
        return [self.min_longitude, self.min_latitude, self.max_longitude, self.max_latitude]

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.min_latitude + self.max_latitude) / 2,
            (self.min_longitude + self.max_longitude) / 2,
        )

    def split_into_zones(self, zone_size_nm: float = 10.0) -> list["BoundingBox"]:
        """Subdivide bounding box into smaller geographic zones based on nautical miles.
        
        Matches SeaSentry's multi-zone scraping grid strategy to handle scraping sites
        with viewport / API response limits.
        """
        if zone_size_nm <= 0:
            raise DomainValidationError("Zone size must be a positive number of nautical miles")

        lat_step = zone_size_nm / 60.0
        avg_lat = (self.min_latitude + self.max_latitude) / 2.0
        cos_lat = max(0.01, cos(radians(avg_lat)))
        lon_step = lat_step / cos_lat

        # Return single zone if already within threshold
        if (self.max_latitude - self.min_latitude) <= lat_step and (self.max_longitude - self.min_longitude) <= lon_step:
            return [self]

        zones: list[BoundingBox] = []
        curr_lat = self.min_latitude
        while curr_lat < self.max_latitude:
            next_lat = min(curr_lat + lat_step, self.max_latitude)
            curr_lon = self.min_longitude
            while curr_lon < self.max_longitude:
                next_lon = min(curr_lon + lon_step, self.max_longitude)
                zones.append(
                    BoundingBox(
                        min_longitude=curr_lon,
                        min_latitude=curr_lat,
                        max_longitude=next_lon,
                        max_latitude=next_lat,
                    )
                )
                curr_lon += lon_step
            curr_lat += lat_step

        return zones


@dataclass(frozen=True)
class Acquisition:
    acquired_at: datetime
    satellite: str
    product_type: str
    product_id: Optional[str] = None
    polarizations: tuple[str, ...] = ("VH",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "acquired_at", _utc_datetime(self.acquired_at, "Acquisition time"))
        object.__setattr__(self, "satellite", _required_text(self.satellite, "Satellite"))
        object.__setattr__(self, "product_type", _required_text(self.product_type, "Product type"))
        object.__setattr__(self, "product_id", _optional_text(self.product_id, "Product ID"))
        if not isinstance(self.polarizations, (list, tuple)) or not self.polarizations:
            raise DomainValidationError("Polarizations must be a non-empty sequence of strings")
        object.__setattr__(self, "polarizations", tuple(str(p).strip().upper() for p in self.polarizations if str(p).strip()))


@dataclass(frozen=True)
class ImageTile:
    bbox: BoundingBox
    width: int
    height: int
    x: int
    y: int

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or not isinstance(self.width, int) or self.width <= 0:
            raise DomainValidationError("Tile width must be a positive integer")
        if isinstance(self.height, bool) or not isinstance(self.height, int) or self.height <= 0:
            raise DomainValidationError("Tile height must be a positive integer")
        _non_negative_integer(self.x, "Tile x index")
        _non_negative_integer(self.y, "Tile y index")


@dataclass(frozen=True)
class Scan:
    folder_name: str
    bbox: BoundingBox
    acquisition: Acquisition
    image_path: str
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "folder_name", _required_text(self.folder_name, "Scan folder name"))
        object.__setattr__(self, "image_path", _required_text(self.image_path, "Scan image path"))
        if not isinstance(self.metadata, dict):
            raise DomainValidationError("Scan metadata must be a dictionary")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class AreaOfInterest:
    name: str
    bbox: BoundingBox
    id: Optional[int] = None
    next_scan: Optional[datetime] = None
    last_checked: Optional[datetime] = None
    auto_capture_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "Area-of-interest name"))
        if self.id is not None and (isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0):
            raise DomainValidationError("Area-of-interest ID must be a positive integer")
        if self.next_scan is not None:
            object.__setattr__(self, "next_scan", _utc_datetime(self.next_scan, "Next scan time"))
        if self.last_checked is not None:
            object.__setattr__(self, "last_checked", _utc_datetime(self.last_checked, "Last-checked time"))
        object.__setattr__(self, "auto_capture_enabled", bool(self.auto_capture_enabled))


@dataclass(frozen=True)
class ShipDetection:
    x: int
    y: int
    width: int
    height: int
    confidence: Optional[float] = None
    angle: Optional[float] = None
    length: Optional[float] = None
    beam: Optional[float] = None
    center_x: Optional[float] = None
    center_y: Optional[float] = None
    polygon_points: Optional[tuple[tuple[float, float], ...]] = None

    def __post_init__(self) -> None:
        _non_negative_integer(self.x, "Detection x coordinate")
        _non_negative_integer(self.y, "Detection y coordinate")
        if isinstance(self.width, bool) or not isinstance(self.width, int) or self.width <= 0:
            raise DomainValidationError("Detection width must be a positive integer")
        if isinstance(self.height, bool) or not isinstance(self.height, int) or self.height <= 0:
            raise DomainValidationError("Detection height must be a positive integer")
        if self.confidence is not None:
            confidence = _number(self.confidence, "Detection confidence")
            if not 0 <= confidence <= 1:
                raise DomainValidationError("Detection confidence must be between 0 and 1")
            object.__setattr__(self, "confidence", confidence)
        if self.angle is not None:
            object.__setattr__(self, "angle", _number(self.angle, "Detection angle"))
        if self.length is not None:
            length = _number(self.length, "Detection length")
            if length <= 0:
                raise DomainValidationError("Detection length must be positive")
            object.__setattr__(self, "length", length)
        if self.beam is not None:
            beam = _number(self.beam, "Detection beam")
            if beam <= 0:
                raise DomainValidationError("Detection beam must be positive")
            object.__setattr__(self, "beam", beam)
        if self.center_x is not None:
            object.__setattr__(self, "center_x", _number(self.center_x, "Detection center x"))
        if self.center_y is not None:
            object.__setattr__(self, "center_y", _number(self.center_y, "Detection center y"))
        if self.polygon_points is not None:
            if not isinstance(self.polygon_points, (list, tuple)) or len(self.polygon_points) < 3:
                raise DomainValidationError("Polygon points must contain at least 3 vertices")
            pts = tuple((float(pt[0]), float(pt[1])) for pt in self.polygon_points)
            object.__setattr__(self, "polygon_points", pts)


@dataclass(frozen=True)
class BackgroundTask:
    task_id: str
    task_type: str
    status: str = "PENDING"
    progress: float = 0.0
    message: str = ""
    scan_id: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[dict[str, object]] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _required_text(self.task_id, "Task ID"))
        object.__setattr__(self, "task_type", _required_text(self.task_type, "Task type"))
        object.__setattr__(self, "status", _required_text(self.status, "Task status").upper())
        progress = _number(self.progress, "Task progress")
        if not 0.0 <= progress <= 100.0:
            raise DomainValidationError("Task progress must be between 0.0 and 100.0")
        object.__setattr__(self, "progress", progress)
        object.__setattr__(self, "message", str(self.message or "").strip())
        if self.scan_id is not None:
            object.__setattr__(self, "scan_id", _optional_text(self.scan_id, "Scan ID"))
        if self.created_at is not None:
            object.__setattr__(self, "created_at", _utc_datetime(self.created_at, "Task created at"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", _utc_datetime(self.completed_at, "Task completed at"))
        if self.result is not None and not isinstance(self.result, dict):
            raise DomainValidationError("Task result must be a dictionary")
        if self.error is not None:
            object.__setattr__(self, "error", _optional_text(self.error, "Task error"))


@dataclass(frozen=True)
class Vessel:
    imo: str
    mmsi: str
    name: Optional[str] = None
    vessel_type: Optional[str] = None
    callsign: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "imo", _required_text(self.imo, "IMO identifier"))
        object.__setattr__(self, "mmsi", _required_text(self.mmsi, "MMSI identifier"))
        object.__setattr__(self, "name", _optional_text(self.name, "Vessel name"))
        object.__setattr__(self, "vessel_type", _optional_text(self.vessel_type, "Vessel type"))
        object.__setattr__(self, "callsign", _optional_text(self.callsign, "Callsign"))


@dataclass(frozen=True)
class VesselPosition:
    mmsi: str
    latitude: float
    longitude: float
    timestamp: datetime
    speed: Optional[float] = None
    heading: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mmsi", _required_text(self.mmsi, "MMSI identifier"))
        latitude = _number(self.latitude, "Latitude")
        longitude = _number(self.longitude, "Longitude")
        if not -90 <= latitude <= 90:
            raise DomainValidationError("Latitude must be between -90 and 90")
        if not -180 <= longitude <= 180:
            raise DomainValidationError("Longitude must be between -180 and 180")
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "timestamp", _utc_datetime(self.timestamp, "Position timestamp"))
        if self.speed is not None:
            speed = _number(self.speed, "Speed")
            if speed < 0:
                raise DomainValidationError("Speed must not be negative")
            object.__setattr__(self, "speed", speed)
        if self.heading is not None:
            heading = _number(self.heading, "Heading")
            if not 0 <= heading <= 360:
                raise DomainValidationError("Heading must be between 0 and 360 degrees")
            object.__setattr__(self, "heading", heading)


@dataclass(frozen=True)
class AISRecord:
    vessel: Vessel
    position: VesselPosition

    def __post_init__(self) -> None:
        if self.vessel.mmsi != self.position.mmsi:
            raise DomainValidationError("Vessel and position MMSI identifiers must match")


@dataclass(frozen=True)
class PostPassIngestionJob:
    aoi_id: int
    pass_time: datetime
    satellite: str = "Sentinel-1"
    orbit_direction: Optional[str] = None
    status: str = "POLLING_CATALOG"
    attempts: int = 0
    last_polled_at: Optional[datetime] = None
    next_poll_at: Optional[datetime] = None
    scan_folder: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    id: Optional[int] = None
    aoi_name: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.aoi_id, bool) or not isinstance(self.aoi_id, int) or self.aoi_id <= 0:
            raise DomainValidationError("AOI ID must be a positive integer")
        object.__setattr__(self, "pass_time", _utc_datetime(self.pass_time, "Pass time"))
        object.__setattr__(self, "satellite", _required_text(self.satellite, "Satellite"))
        if self.orbit_direction is not None:
            object.__setattr__(self, "orbit_direction", _optional_text(self.orbit_direction, "Orbit direction"))
        status = _required_text(self.status, "Job status").upper()
        valid_statuses = {"PENDING_PASS", "POLLING_CATALOG", "INGESTING", "COMPLETED", "TIMED_OUT", "FAILED"}
        if status not in valid_statuses:
            raise DomainValidationError(f"Invalid job status: {status}. Must be one of {valid_statuses}")
        object.__setattr__(self, "status", status)
        _non_negative_integer(self.attempts, "Attempts")
        if self.last_polled_at is not None:
            object.__setattr__(self, "last_polled_at", _utc_datetime(self.last_polled_at, "Last polled at"))
        if self.next_poll_at is not None:
            object.__setattr__(self, "next_poll_at", _utc_datetime(self.next_poll_at, "Next poll at"))
        if self.scan_folder is not None:
            object.__setattr__(self, "scan_folder", _optional_text(self.scan_folder, "Scan folder"))
        if self.error_message is not None:
            object.__setattr__(self, "error_message", _optional_text(self.error_message, "Error message"))
        if self.created_at is not None:
            object.__setattr__(self, "created_at", _utc_datetime(self.created_at, "Created at"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", _utc_datetime(self.completed_at, "Completed at"))
        if self.id is not None and (isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0):
            raise DomainValidationError("Job ID must be a positive integer")
        if self.aoi_name is not None:
            object.__setattr__(self, "aoi_name", _optional_text(self.aoi_name, "AOI name"))

