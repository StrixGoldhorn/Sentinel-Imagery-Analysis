"""Domain entities used throughout the application."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import cos, isfinite, radians
from typing import Any, Optional

from sentinel_analysis.domain.correlation import ensure_correlation_id
from sentinel_analysis.domain.exceptions import DomainValidationError


BACKGROUND_TASK_STATUSES = frozenset({
    "PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED",
})


def _number(value: Any, field_name: str) -> float:
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


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{field_name} must be a positive integer")
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
    orbit_direction: Optional[str] = None
    relative_orbit: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "acquired_at", _utc_datetime(self.acquired_at, "Acquisition time"))
        object.__setattr__(self, "satellite", _required_text(self.satellite, "Satellite"))
        object.__setattr__(self, "product_type", _required_text(self.product_type, "Product type"))
        object.__setattr__(self, "product_id", _optional_text(self.product_id, "Product ID"))
        if self.orbit_direction is not None:
            norm_od = _optional_text(self.orbit_direction, "Orbit direction")
            object.__setattr__(self, "orbit_direction", norm_od.upper() if norm_od else None)
        if self.relative_orbit is not None:
            if isinstance(self.relative_orbit, bool) or not isinstance(self.relative_orbit, int) or self.relative_orbit <= 0:
                raise DomainValidationError("Relative orbit must be a positive integer")
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
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "folder_name", _required_text(self.folder_name, "Scan folder name"))
        object.__setattr__(self, "image_path", _required_text(self.image_path, "Scan image path"))
        if not isinstance(self.metadata, dict):
            raise DomainValidationError("Scan metadata must be a dictionary")
        meta = dict(self.metadata)
        cid = self.correlation_id or meta.get("correlation_id")
        cid_str = ensure_correlation_id(str(cid) if cid else None, prefix="scan")
        meta["correlation_id"] = cid_str
        object.__setattr__(self, "metadata", meta)
        object.__setattr__(self, "correlation_id", cid_str)


@dataclass(frozen=True)
class AreaOfInterest:
    name: str
    bbox: BoundingBox
    id: Optional[int] = None
    next_scan: Optional[datetime] = None
    last_checked: Optional[datetime] = None
    auto_capture_enabled: bool = False
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "Area-of-interest name"))
        if self.id is not None and (isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0):
            raise DomainValidationError("Area-of-interest ID must be a positive integer")
        if self.next_scan is not None:
            object.__setattr__(self, "next_scan", _utc_datetime(self.next_scan, "Next scan time"))
        if self.last_checked is not None:
            object.__setattr__(self, "last_checked", _utc_datetime(self.last_checked, "Last-checked time"))
        object.__setattr__(self, "auto_capture_enabled", bool(self.auto_capture_enabled))
        cid_str = ensure_correlation_id(self.correlation_id, prefix="aoi")
        object.__setattr__(self, "correlation_id", cid_str)


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
    wake_detected: Optional[bool] = None
    wake_heading: Optional[float] = None
    wake_speed_knots: Optional[float] = None
    wake_confidence: Optional[float] = None
    is_speed_spoofed: Optional[bool] = None
    is_course_spoofed: Optional[bool] = None
    vessel_class: Optional[str] = None
    classification_confidence: Optional[float] = None
    optical_status: Optional[str] = None
    optical_confirmed: Optional[bool] = None
    optical_confidence: Optional[float] = None
    temporal_change_type: Optional[str] = None
    provenance: Optional[dict[str, Any]] = None
    spatial_uncertainty: Optional[dict[str, Any]] = None
    dimension_uncertainty: Optional[dict[str, Any]] = None
    association_likelihood: Optional[float] = None
    association_probability: Optional[float] = None
    reason_codes: Optional[tuple[str, ...] | list[str]] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geo_bbox: Optional[dict[str, float]] = None
    geo_polygon: Optional[tuple[tuple[float, float], ...]] = None

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
        if self.wake_detected is not None:
            object.__setattr__(self, "wake_detected", bool(self.wake_detected))
        if self.wake_heading is not None:
            object.__setattr__(self, "wake_heading", _number(self.wake_heading, "Wake heading"))
        if self.wake_speed_knots is not None:
            object.__setattr__(self, "wake_speed_knots", _number(self.wake_speed_knots, "Wake speed knots"))
        if self.wake_confidence is not None:
            object.__setattr__(self, "wake_confidence", _number(self.wake_confidence, "Wake confidence"))
        if self.is_speed_spoofed is not None:
            object.__setattr__(self, "is_speed_spoofed", bool(self.is_speed_spoofed))
        if self.is_course_spoofed is not None:
            object.__setattr__(self, "is_course_spoofed", bool(self.is_course_spoofed))
        if self.vessel_class is not None:
            object.__setattr__(self, "vessel_class", _required_text(self.vessel_class, "Vessel class"))
        if self.classification_confidence is not None:
            class_conf = _number(self.classification_confidence, "Classification confidence")
            if not 0 <= class_conf <= 1:
                raise DomainValidationError("Classification confidence must be between 0 and 1")
            object.__setattr__(self, "classification_confidence", class_conf)
        if self.optical_status is not None:
            object.__setattr__(self, "optical_status", _required_text(self.optical_status, "Optical status"))
        if self.optical_confirmed is not None:
            object.__setattr__(self, "optical_confirmed", bool(self.optical_confirmed))
        if self.optical_confidence is not None:
            opt_conf = _number(self.optical_confidence, "Optical confidence")
            if not 0 <= opt_conf <= 1:
                raise DomainValidationError("Optical confidence must be between 0 and 1")
            object.__setattr__(self, "optical_confidence", opt_conf)
        if self.temporal_change_type is not None:
            object.__setattr__(self, "temporal_change_type", _required_text(self.temporal_change_type, "Temporal change type"))
        if self.provenance is not None:
            if not isinstance(self.provenance, dict):
                raise DomainValidationError("Detection provenance must be a dictionary")
            object.__setattr__(self, "provenance", dict(self.provenance))
        if self.spatial_uncertainty is not None:
            if hasattr(self.spatial_uncertainty, "to_dict"):
                object.__setattr__(self, "spatial_uncertainty", self.spatial_uncertainty.to_dict())
            elif isinstance(self.spatial_uncertainty, dict):
                object.__setattr__(self, "spatial_uncertainty", dict(self.spatial_uncertainty))
            else:
                raise DomainValidationError("Detection spatial uncertainty must be a dictionary")
        if self.dimension_uncertainty is not None:
            if hasattr(self.dimension_uncertainty, "to_dict"):
                object.__setattr__(self, "dimension_uncertainty", self.dimension_uncertainty.to_dict())
            elif isinstance(self.dimension_uncertainty, dict):
                object.__setattr__(self, "dimension_uncertainty", dict(self.dimension_uncertainty))
            else:
                raise DomainValidationError("Detection dimension uncertainty must be a dictionary")
        if self.association_likelihood is not None:
            assoc_like = _number(self.association_likelihood, "Detection association likelihood")
            if not 0 <= assoc_like <= 1:
                raise DomainValidationError("Detection association likelihood must be between 0 and 1")
            object.__setattr__(self, "association_likelihood", assoc_like)
        if self.association_probability is not None:
            assoc_prob = _number(self.association_probability, "Detection association probability")
            if not 0 <= assoc_prob <= 1:
                raise DomainValidationError("Detection association probability must be between 0 and 1")
            object.__setattr__(self, "association_probability", assoc_prob)
        if self.reason_codes is not None:
            if not isinstance(self.reason_codes, (list, tuple)):
                raise DomainValidationError("Detection reason codes must be a sequence of strings")
            object.__setattr__(self, "reason_codes", tuple(str(code).strip() for code in self.reason_codes if str(code).strip()))
        if self.latitude is not None:
            lat = _number(self.latitude, "Detection latitude")
            if not -90.0 <= lat <= 90.0:
                raise DomainValidationError("Detection latitude must be between -90 and 90")
            object.__setattr__(self, "latitude", lat)
        if self.longitude is not None:
            lon = _number(self.longitude, "Detection longitude")
            if not -180.0 <= lon <= 180.0:
                raise DomainValidationError("Detection longitude must be between -180 and 180")
            object.__setattr__(self, "longitude", lon)
        if self.geo_bbox is not None:
            if not isinstance(self.geo_bbox, dict):
                raise DomainValidationError("Detection geo_bbox must be a dictionary")
            object.__setattr__(self, "geo_bbox", dict(self.geo_bbox))
        if self.geo_polygon is not None:
            if not isinstance(self.geo_polygon, (list, tuple)) or len(self.geo_polygon) < 3:
                raise DomainValidationError("Detection geo_polygon must contain at least 3 vertices")
            pts_geo = tuple((float(pt[0]), float(pt[1])) for pt in self.geo_polygon)
            object.__setattr__(self, "geo_polygon", pts_geo)

    @property
    def lat(self) -> Optional[float]:
        return self.latitude

    @property
    def lng(self) -> Optional[float]:
        return self.longitude

    @property
    def lon(self) -> Optional[float]:
        return self.longitude


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
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _required_text(self.task_id, "Task ID"))
        object.__setattr__(self, "task_type", _required_text(self.task_type, "Task type"))
        normalized_status = _required_text(self.status, "Task status").upper()
        if normalized_status not in BACKGROUND_TASK_STATUSES:
            raise DomainValidationError(
                f"Task status must be one of: {', '.join(sorted(BACKGROUND_TASK_STATUSES))}"
            )
        object.__setattr__(self, "status", normalized_status)
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
        cid_str = ensure_correlation_id(self.correlation_id, prefix="task")
        object.__setattr__(self, "correlation_id", cid_str)


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
    relative_orbit: Optional[int] = None
    trigger_type: str = "MANUAL"
    prediction_source: Optional[str] = None
    workflow_id: Optional[str] = None
    basis_product_id: Optional[str] = None
    basis_acquisition_time: Optional[datetime] = None
    basis_satellite: Optional[str] = None
    basis_relative_orbit: Optional[int] = None
    status: str = "POLLING_CATALOG"
    attempts: int = 0
    last_polled_at: Optional[datetime] = None
    next_poll_at: Optional[datetime] = None
    scan_folder: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    aoi_name: Optional[str] = None
    expected_imagery_time: Optional[datetime] = None
    correlation_id: Optional[str] = None
    id: Optional[int] = None

    def __post_init__(self) -> None:
        if isinstance(self.aoi_id, bool) or not isinstance(self.aoi_id, int) or self.aoi_id <= 0:
            raise DomainValidationError("AOI ID must be a positive integer")
        object.__setattr__(self, "pass_time", _utc_datetime(self.pass_time, "Pass time"))
        object.__setattr__(self, "satellite", _required_text(self.satellite, "Satellite"))
        if self.orbit_direction is not None:
            object.__setattr__(self, "orbit_direction", _optional_text(self.orbit_direction, "Orbit direction"))
        if self.relative_orbit is not None:
            if isinstance(self.relative_orbit, bool) or not isinstance(self.relative_orbit, int) or self.relative_orbit <= 0:
                raise DomainValidationError("Relative orbit must be a positive integer")
        object.__setattr__(self, "trigger_type", _required_text(self.trigger_type, "Trigger type").upper())
        object.__setattr__(self, "prediction_source", _optional_text(self.prediction_source, "Prediction source"))
        object.__setattr__(self, "workflow_id", _optional_text(self.workflow_id, "Workflow ID"))
        object.__setattr__(self, "basis_product_id", _optional_text(self.basis_product_id, "Basis product ID"))
        if self.basis_acquisition_time is not None:
            object.__setattr__(self, "basis_acquisition_time", _utc_datetime(self.basis_acquisition_time, "Basis acquisition time"))
        object.__setattr__(self, "basis_satellite", _optional_text(self.basis_satellite, "Basis satellite"))
        if self.basis_relative_orbit is not None:
            if isinstance(self.basis_relative_orbit, bool) or not isinstance(self.basis_relative_orbit, int) or self.basis_relative_orbit <= 0:
                raise DomainValidationError("Basis relative orbit must be a positive integer")
        status = _required_text(self.status, "Job status").upper()
        valid_statuses = {
            "PENDING_PASS", "POLLING_CATALOG", "QUERYING_CATALOG", "INGESTING",
            "COMPLETED", "TIMED_OUT", "WAIT_EXPIRED", "FAILED",
        }
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
        if self.expected_imagery_time is not None:
            object.__setattr__(self, "expected_imagery_time", _utc_datetime(self.expected_imagery_time, "Expected imagery time"))
        else:
            object.__setattr__(self, "expected_imagery_time", self.pass_time)
        cid_str = ensure_correlation_id(self.correlation_id, prefix="job")
        object.__setattr__(self, "correlation_id", cid_str)


@dataclass(frozen=True)
class TransshipmentRendezvous:
    vessel_a_index: int
    vessel_b_index: int
    distance_meters: float
    relative_bearing_deg: float
    vessel_a_is_dark: bool
    vessel_b_is_dark: bool
    risk_level: str
    risk_score: float
    vessel_a_lat: float
    vessel_a_lon: float
    vessel_b_lat: float
    vessel_b_lon: float
    vessel_a_length: Optional[float] = None
    vessel_b_length: Optional[float] = None
    vessel_a_identifier: Optional[str] = None
    vessel_b_identifier: Optional[str] = None
    narrative: str = ""

    def __post_init__(self) -> None:
        _non_negative_integer(self.vessel_a_index, "Vessel A index")
        _non_negative_integer(self.vessel_b_index, "Vessel B index")
        object.__setattr__(self, "distance_meters", _number(self.distance_meters, "Distance meters"))
        object.__setattr__(self, "relative_bearing_deg", _number(self.relative_bearing_deg, "Relative bearing"))
        object.__setattr__(self, "risk_score", _number(self.risk_score, "Risk score"))
        object.__setattr__(self, "vessel_a_lat", _number(self.vessel_a_lat, "Vessel A latitude"))
        object.__setattr__(self, "vessel_a_lon", _number(self.vessel_a_lon, "Vessel A longitude"))
        object.__setattr__(self, "vessel_b_lat", _number(self.vessel_b_lat, "Vessel B latitude"))
        object.__setattr__(self, "vessel_b_lon", _number(self.vessel_b_lon, "Vessel B longitude"))
        object.__setattr__(self, "risk_level", _required_text(self.risk_level, "Risk level").upper())


@dataclass(frozen=True)
class LoiteringAnomaly:
    vessel_index: int
    lat: float
    lon: float
    is_dark: bool
    speed_knots: float
    risk_level: str
    risk_score: float
    length: Optional[float] = None
    identifier: Optional[str] = None
    narrative: str = ""

    def __post_init__(self) -> None:
        _non_negative_integer(self.vessel_index, "Vessel index")
        object.__setattr__(self, "lat", _number(self.lat, "Latitude"))
        object.__setattr__(self, "lon", _number(self.lon, "Longitude"))
        object.__setattr__(self, "speed_knots", _number(self.speed_knots, "Speed knots"))
        object.__setattr__(self, "risk_score", _number(self.risk_score, "Risk score"))
        object.__setattr__(self, "risk_level", _required_text(self.risk_level, "Risk level").upper())


@dataclass(frozen=True)
class GeofenceZone:
    zone_id: str
    name: str
    zone_type: str  # "MPA", "EEZ", "RESTRICTED_ANCHORAGE", "TRAFFIC_SEPARATION_SCHEME", "FISHERIES_EXCLUSION"
    polygon: tuple[tuple[float, float], ...]  # (lat, lon) coordinates
    description: str = ""
    restrictions: tuple[str, ...] = ("NO_DARK_VESSEL",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_id", _required_text(self.zone_id, "Zone ID"))
        object.__setattr__(self, "name", _required_text(self.name, "Zone name"))
        object.__setattr__(self, "zone_type", _required_text(self.zone_type, "Zone type").upper())
        if not isinstance(self.polygon, (list, tuple)) or len(self.polygon) < 3:
            raise DomainValidationError("GeofenceZone polygon must contain at least 3 (lat, lon) vertices")
        clean_pts = tuple((float(pt[0]), float(pt[1])) for pt in self.polygon)
        object.__setattr__(self, "polygon", clean_pts)
        if isinstance(self.restrictions, (list, tuple)):
            object.__setattr__(self, "restrictions", tuple(str(r).upper() for r in self.restrictions))


@dataclass(frozen=True)
class GeofenceBreach:
    zone_id: str
    zone_name: str
    zone_type: str
    vessel_index: int
    vessel_identifier: str
    is_dark: bool
    lat: float
    lon: float
    violation_type: str
    severity: str
    narrative: str
    vessel_class: Optional[str] = None
    speed_knots: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_id", _required_text(self.zone_id, "Zone ID"))
        object.__setattr__(self, "zone_name", _required_text(self.zone_name, "Zone name"))
        object.__setattr__(self, "zone_type", _required_text(self.zone_type, "Zone type").upper())
        _non_negative_integer(self.vessel_index, "Vessel index")
        object.__setattr__(self, "lat", _number(self.lat, "Latitude"))
        object.__setattr__(self, "lon", _number(self.lon, "Longitude"))
        object.__setattr__(self, "severity", _required_text(self.severity, "Severity").upper())
        object.__setattr__(self, "violation_type", _required_text(self.violation_type, "Violation type").upper())


@dataclass(frozen=True)
class TemporalChangePoint:
    x: float
    y: float
    change_type: str  # "ARRIVED", "DEPARTED", "PERSISTENT_STRUCTURE"
    magnitude_db: float
    confidence: float
    lat: Optional[float] = None
    lon: Optional[float] = None
    narrative: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _number(self.x, "X coordinate"))
        object.__setattr__(self, "y", _number(self.y, "Y coordinate"))
        object.__setattr__(self, "change_type", _required_text(self.change_type, "Change type").upper())
        object.__setattr__(self, "magnitude_db", _number(self.magnitude_db, "Magnitude dB"))
        object.__setattr__(self, "confidence", _number(self.confidence, "Confidence"))
        if self.lat is not None:
            object.__setattr__(self, "lat", _number(self.lat, "Latitude"))
        if self.lon is not None:
            object.__setattr__(self, "lon", _number(self.lon, "Longitude"))


@dataclass(frozen=True)
class MultiTemporalChangeReport:
    reference_scan: str
    target_scan: str
    arrived_count: int
    departed_count: int
    persistent_structures_count: int
    change_map_path: Optional[str] = None
    timestamp_t1: Optional[str] = None
    timestamp_t2: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_scan", _required_text(self.reference_scan, "Reference scan"))
        object.__setattr__(self, "target_scan", _required_text(self.target_scan, "Target scan"))
        _non_negative_integer(self.arrived_count, "Arrived count")
        _non_negative_integer(self.departed_count, "Departed count")
        _non_negative_integer(self.persistent_structures_count, "Persistent structures count")


@dataclass(frozen=True)
class WebhookConfig:
    id: str
    url: str
    service_type: str = "generic"
    name: str = ""
    enabled: bool = True
    min_severity: str = "INFO"
    secret_token: Optional[str] = None
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "Webhook ID"))
        object.__setattr__(self, "url", _required_text(self.url, "Webhook URL"))
        object.__setattr__(self, "service_type", _required_text(self.service_type, "Service type").lower())
        object.__setattr__(self, "name", str(self.name or self.id))
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "min_severity", _required_text(self.min_severity, "Min severity").upper())


@dataclass(frozen=True)
class MaritimeAlert:
    alert_id: str
    event_type: str
    severity: str
    title: str
    summary: str
    details: dict[str, object] = field(default_factory=dict)
    timestamp: Optional[datetime] = None
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "alert_id", _required_text(self.alert_id, "Alert ID"))
        object.__setattr__(self, "event_type", _required_text(self.event_type, "Event type").upper())
        object.__setattr__(self, "severity", _required_text(self.severity, "Severity").upper())
        object.__setattr__(self, "title", _required_text(self.title, "Title"))
        object.__setattr__(self, "summary", _required_text(self.summary, "Summary"))
        if not isinstance(self.details, dict):
            raise DomainValidationError("Alert details must be a dictionary")
        object.__setattr__(self, "details", dict(self.details))
        if self.timestamp is not None:
            object.__setattr__(self, "timestamp", _utc_datetime(self.timestamp, "Timestamp"))
        object.__setattr__(self, "correlation_id", ensure_correlation_id(self.correlation_id, prefix="alert"))


@dataclass(frozen=True)
class TrafficHeatmapPoint:
    latitude: float
    longitude: float
    intensity: float
    category: str = "ais"
    count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "latitude", round(float(self.latitude), 6))
        object.__setattr__(self, "longitude", round(float(self.longitude), 6))
        object.__setattr__(self, "intensity", max(0.0, min(1.0, round(float(self.intensity), 4))))
        object.__setattr__(self, "category", _required_text(self.category, "Category").lower())
        _positive_integer(self.count, "Point count")


@dataclass(frozen=True)
class TrafficHeatmapReport:
    total_ais_points: int
    total_dark_vessels: int
    total_cells: int
    points: list[TrafficHeatmapPoint] = field(default_factory=list)
    bbox: Optional[BoundingBox] = None
    generated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _non_negative_integer(self.total_ais_points, "Total AIS points")
        _non_negative_integer(self.total_dark_vessels, "Total dark vessels")
        _non_negative_integer(self.total_cells, "Total cells")
        if self.generated_at is not None:
            object.__setattr__(self, "generated_at", _utc_datetime(self.generated_at, "Generated at"))


@dataclass(frozen=True)
class StorageQuotaReport:
    total_bytes_used: int
    quota_bytes: int
    usage_percent: float
    scans_bytes: int
    cache_bytes: int
    database_bytes: int
    scan_count: int
    oldest_scan_date: Optional[datetime] = None
    quota_exceeded: bool = False

    def __post_init__(self) -> None:
        _non_negative_integer(self.total_bytes_used, "Total bytes used")
        _positive_integer(self.quota_bytes, "Quota bytes")
        object.__setattr__(self, "usage_percent", round(_number(self.usage_percent, "Usage percent"), 2))
        _non_negative_integer(self.scans_bytes, "Scans bytes")
        _non_negative_integer(self.cache_bytes, "Cache bytes")
        _non_negative_integer(self.database_bytes, "Database bytes")
        _non_negative_integer(self.scan_count, "Scan count")
        if self.oldest_scan_date is not None:
            object.__setattr__(self, "oldest_scan_date", _utc_datetime(self.oldest_scan_date, "Oldest scan date"))
        object.__setattr__(self, "quota_exceeded", bool(self.quota_exceeded))


@dataclass(frozen=True)
class ArchivalOutcome:
    archived_scans: tuple[str, ...] = field(default_factory=tuple)
    pruned_cache_files: int = 0
    bytes_freed: int = 0
    archive_paths: tuple[str, ...] = field(default_factory=tuple)
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.archived_scans, (list, tuple)):
            object.__setattr__(self, "archived_scans", tuple(str(s) for s in self.archived_scans))
        if isinstance(self.archive_paths, (list, tuple)):
            object.__setattr__(self, "archive_paths", tuple(str(p) for p in self.archive_paths))
        _non_negative_integer(self.pruned_cache_files, "Pruned cache files")
        _non_negative_integer(self.bytes_freed, "Bytes freed")
        if self.timestamp is not None:
            object.__setattr__(self, "timestamp", _utc_datetime(self.timestamp, "Archival timestamp"))





