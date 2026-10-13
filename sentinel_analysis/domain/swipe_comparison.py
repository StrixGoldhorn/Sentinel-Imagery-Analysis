"""Domain models and spatial helpers for repeat-pass SAR swipe comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Optional

from sentinel_analysis.domain.entities import BoundingBox


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points on Earth in meters."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def compute_bbox_intersection(
    b1: BoundingBox,
    b2: BoundingBox,
) -> tuple[Optional[BoundingBox], float]:
    """Calculate geographic intersection of two bounding boxes and overlap ratio."""
    inter_min_lon = max(b1.min_longitude, b2.min_longitude)
    inter_min_lat = max(b1.min_latitude, b2.min_latitude)
    inter_max_lon = min(b1.max_longitude, b2.max_longitude)
    inter_max_lat = min(b1.max_latitude, b2.max_latitude)

    if inter_min_lon >= inter_max_lon or inter_min_lat >= inter_max_lat:
        return None, 0.0

    inter_area = (inter_max_lon - inter_min_lon) * (inter_max_lat - inter_min_lat)
    area1 = (b1.max_longitude - b1.min_longitude) * (b1.max_latitude - b1.min_latitude)
    area2 = (b2.max_longitude - b2.min_longitude) * (b2.max_latitude - b2.min_latitude)
    min_area = min(area1, area2)

    overlap_ratio = inter_area / max(1e-9, min_area)
    inter_box = BoundingBox(inter_min_lon, inter_min_lat, inter_max_lon, inter_max_lat)
    return inter_box, round(overlap_ratio, 4)


@dataclass
class RepeatPassCandidate:
    """A repeat satellite acquisition candidate suitable for baseline comparison."""

    scan_id: str
    acquired_at: Optional[datetime]
    days_prior: float
    overlap_pct: float
    satellite: str = "Sentinel-1"
    orbit_direction: Optional[str] = None
    polarization: Optional[str] = None
    image_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "acquired_at": self.acquired_at.isoformat() if self.acquired_at else None,
            "days_prior": round(self.days_prior, 2),
            "overlap_pct": round(self.overlap_pct * 100.0, 1),
            "satellite": self.satellite,
            "orbit_direction": self.orbit_direction,
            "polarization": self.polarization,
            "image_url": self.image_url,
        }


@dataclass
class TargetChangeStatus:
    """Classification of target presence between repeat passes."""

    target_id: str
    status: str  # 'PERSISTENT', 'ARRIVED', 'DEPARTED'
    latitude: float
    longitude: float
    confidence: float = 0.8
    length_m: Optional[float] = None
    separation_m: Optional[float] = None
    pass_source: str = "TARGET"  # 'TARGET' or 'BASELINE'

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "status": self.status,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "confidence": round(self.confidence, 3),
            "length_m": round(self.length_m, 1) if self.length_m is not None else None,
            "separation_m": round(self.separation_m, 1) if self.separation_m is not None else None,
            "pass_source": self.pass_source,
        }

    def to_geojson_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(self.longitude, 6), round(self.latitude, 6)],
            },
            "properties": self.to_dict(),
        }


@dataclass
class SwipeComparisonReport:
    """Complete intelligence report for interactive before/after SAR swipe comparison."""

    target_scan_id: str
    baseline_scan_id: str
    target_acquisition: Optional[datetime]
    baseline_acquisition: Optional[datetime]
    days_difference: float
    hours_difference: float
    overlap_pct: float
    common_bbox: Optional[BoundingBox]
    target_image_url: str
    baseline_image_url: str
    targets: list[TargetChangeStatus] = field(default_factory=list)
    candidates: list[RepeatPassCandidate] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def persistent_count(self) -> int:
        return sum(1 for t in self.targets if t.status == "PERSISTENT")

    @property
    def arrived_count(self) -> int:
        return sum(1 for t in self.targets if t.status == "ARRIVED")

    @property
    def departed_count(self) -> int:
        return sum(1 for t in self.targets if t.status == "DEPARTED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": True,
            "target_scan_id": self.target_scan_id,
            "baseline_scan_id": self.baseline_scan_id,
            "target_acquisition": self.target_acquisition.isoformat() if self.target_acquisition else None,
            "baseline_acquisition": self.baseline_acquisition.isoformat() if self.baseline_acquisition else None,
            "temporal_delta": {
                "days": round(self.days_difference, 2),
                "hours": round(self.hours_difference, 1),
                "label": f"{abs(round(self.days_difference, 1))} days {'prior' if self.days_difference >= 0 else 'later'}",
            },
            "spatial_overlap": {
                "overlap_pct": round(self.overlap_pct * 100.0, 1),
                "common_bbox": self.common_bbox.as_list() if self.common_bbox else None,
            },
            "imagery": {
                "target_image_url": self.target_image_url,
                "baseline_image_url": self.baseline_image_url,
            },
            "diff_summary": {
                "total_targets": len(self.targets),
                "persistent_count": self.persistent_count,
                "arrived_count": self.arrived_count,
                "departed_count": self.departed_count,
            },
            "targets": [t.to_dict() for t in self.targets],
            "geojson": {
                "type": "FeatureCollection",
                "features": [t.to_geojson_feature() for t in self.targets],
            },
            "candidates": [c.to_dict() for c in self.candidates],
            "metadata": dict(self.metadata),
        }
