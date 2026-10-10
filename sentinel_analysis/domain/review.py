"""Domain models and business logic for analyst review workflow, dispositions, and dataset generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ReviewDisposition(str, Enum):
    """Standard analyst review dispositions."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"

    @classmethod
    def from_str(cls, val: str | None) -> ReviewDisposition:
        if not val:
            return cls.PENDING
        clean = str(val).strip().lower()
        for item in cls:
            if item.value == clean or item.name.lower() == clean:
                return item
        return cls.PENDING


class ReviewAction(str, Enum):
    """Immutable audit trail action types."""
    CREATED = "created"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MARKED_UNCERTAIN = "marked_uncertain"
    BOX_CORRECTED = "box_corrected"
    COMMENT_ADDED = "comment_added"
    UPDATED = "updated"


@dataclass
class ReviewBox:
    """Bounding box coordinates in pixels within an image."""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def __init__(
        self,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
        min_x: float | None = None,
        min_y: float | None = None,
        max_x: float | None = None,
        max_y: float | None = None,
    ) -> None:
        if min_x is not None and max_x is not None:
            self.x = float(min_x)
            self.width = float(max_x - min_x)
        else:
            self.x = float(x or 0.0)
            self.width = float(width or 0.0)

        if min_y is not None and max_y is not None:
            self.y = float(min_y)
            self.height = float(max_y - min_y)
        else:
            self.y = float(y or 0.0)
            self.height = float(height or 0.0)

    @property
    def min_x(self) -> float:
        return self.x

    @property
    def min_y(self) -> float:
        return self.y

    @property
    def max_x(self) -> float:
        return self.x + self.width

    @property
    def max_y(self) -> float:
        return self.y + self.height

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(float(self.x), 2),
            "y": round(float(self.y), 2),
            "width": round(float(self.width), 2),
            "height": round(float(self.height), 2),
            "min_x": round(float(self.min_x), 2),
            "min_y": round(float(self.min_y), 2),
            "max_x": round(float(self.max_x), 2),
            "max_y": round(float(self.max_y), 2),
        }

    def to_xyxy(self) -> tuple[float, float, float, float]:
        """Convert to (xmin, ymin, xmax, ymax)."""
        return (self.min_x, self.min_y, self.max_x, self.max_y)

    def to_yolo(self, img_width: float, img_height: float) -> dict[str, float]:
        """Convert to YOLO format: dict with normalized center coordinates and dimensions."""
        if img_width <= 0 or img_height <= 0:
            raise ValueError("Image dimensions must be positive")
        cx = (self.x + self.width / 2.0) / img_width
        cy = (self.y + self.height / 2.0) / img_height
        w = self.width / img_width
        h = self.height / img_height
        return {
            "x_center": round(max(0.0, min(1.0, cx)), 6),
            "y_center": round(max(0.0, min(1.0, cy)), 6),
            "width": round(max(0.0, min(1.0, w)), 6),
            "height": round(max(0.0, min(1.0, h)), 6),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ReviewBox | None:
        if not data or not isinstance(data, dict):
            return None
        try:
            if "min_x" in data and "max_x" in data and "min_y" in data and "max_y" in data:
                return cls(
                    min_x=float(data["min_x"]),
                    min_y=float(data["min_y"]),
                    max_x=float(data["max_x"]),
                    max_y=float(data["max_y"]),
                )
            x = float(data.get("x", 0.0))
            y = float(data.get("y", 0.0))
            w = float(data.get("width", 0.0))
            h = float(data.get("height", 0.0))
            return cls(x=x, y=y, width=w, height=h)
        except (ValueError, TypeError):
            return None


@dataclass
class ReviewHistoryEntry:
    """Immutable audit trail record for an analyst review action."""
    id: int | None
    review_id: str
    action: str
    disposition: str
    reviewer_id: str
    corrected_bbox: dict[str, Any] | None = None
    comments: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        ts = self.timestamp
        if ts.utcoffset() is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return {
            "id": self.id,
            "review_id": self.review_id,
            "action": self.action,
            "disposition": self.disposition,
            "reviewer_id": self.reviewer_id,
            "corrected_bbox": self.corrected_bbox,
            "comments": self.comments,
            "metadata": self.metadata,
            "timestamp": ts.astimezone(timezone.utc).isoformat(),
        }


@dataclass
class ReviewRecord:
    """Analyst review record representing the verification state of a detected contact."""
    review_id: str
    scan_id: str
    detection_idx: int
    disposition: str = ReviewDisposition.PENDING.value
    reviewer_id: str | None = None
    original_bbox: dict[str, Any] = field(default_factory=dict)
    corrected_bbox: dict[str, Any] | None = None
    confidence: float = 0.0
    vessel_class: str | None = None
    comments: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    history: list[ReviewHistoryEntry] = field(default_factory=list)

    lat: float | None = None
    lng: float | None = None
    geo_bbox: dict[str, Any] | None = None
    custom_crop_url: str | None = None

    @property
    def is_reviewed(self) -> bool:
        return self.disposition in (
            ReviewDisposition.ACCEPTED.value,
            ReviewDisposition.REJECTED.value,
            ReviewDisposition.UNCERTAIN.value,
        )

    @property
    def effective_bbox(self) -> Any:
        """Return the corrected bounding box if available, otherwise original detection box."""
        if self.corrected_bbox is not None:
            return self.corrected_bbox
        return self.original_bbox

    @property
    def effective_lat(self) -> float | None:
        if self.lat is not None:
            return self.lat
        if isinstance(self.original_bbox, dict):
            val = self.original_bbox.get("lat", self.original_bbox.get("latitude"))
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
        return None

    @property
    def effective_lng(self) -> float | None:
        if self.lng is not None:
            return self.lng
        if isinstance(self.original_bbox, dict):
            val = self.original_bbox.get("lng", self.original_bbox.get("longitude"))
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
        return None

    @property
    def effective_geo_bbox(self) -> dict[str, Any] | None:
        if self.geo_bbox is not None:
            return self.geo_bbox
        if isinstance(self.original_bbox, dict) and "geo_bbox" in self.original_bbox:
            return self.original_bbox["geo_bbox"]
        return None

    @property
    def crop_url(self) -> str:
        if self.custom_crop_url:
            return self.custom_crop_url
        return f"/api/scan/{self.scan_id}/crop?detection_idx={self.detection_idx}&padding=80&raw=1"

    def to_dict(self) -> dict[str, Any]:
        c_at = self.created_at
        if c_at.utcoffset() is None:
            c_at = c_at.replace(tzinfo=timezone.utc)
        u_at = self.updated_at
        if u_at.utcoffset() is None:
            u_at = u_at.replace(tzinfo=timezone.utc)

        orig_d = self.original_bbox.to_dict() if isinstance(self.original_bbox, ReviewBox) else self.original_bbox
        corr_d = self.corrected_bbox.to_dict() if isinstance(self.corrected_bbox, ReviewBox) else self.corrected_bbox
        eff = self.effective_bbox
        eff_d = eff.to_dict() if isinstance(eff, ReviewBox) else eff

        return {
            "review_id": self.review_id,
            "scan_id": self.scan_id,
            "detection_idx": self.detection_idx,
            "disposition": self.disposition,
            "reviewer_id": self.reviewer_id,
            "original_bbox": orig_d,
            "corrected_bbox": corr_d,
            "effective_bbox": eff_d,
            "confidence": round(float(self.confidence), 4),
            "vessel_class": self.vessel_class,
            "comments": self.comments,
            "reason_codes": list(self.reason_codes),
            "is_reviewed": self.is_reviewed,
            "crop_url": self.crop_url,
            "lat": self.effective_lat,
            "lng": self.effective_lng,
            "geo_bbox": self.effective_geo_bbox,
            "created_at": c_at.astimezone(timezone.utc).isoformat(),
            "updated_at": u_at.astimezone(timezone.utc).isoformat(),
            "history": [h.to_dict() for h in self.history],
        }


def calculate_iou(box1: Any, box2: Any) -> float:
    """Calculate Intersection over Union (IoU) between two bounding boxes {x, y, width, height} or ReviewBox."""
    b1 = box1.to_dict() if hasattr(box1, "to_dict") else (dict(box1) if isinstance(box1, dict) else {})
    b2 = box2.to_dict() if hasattr(box2, "to_dict") else (dict(box2) if isinstance(box2, dict) else {})

    x1_min = float(b1.get("min_x", b1.get("x", 0.0)))
    y1_min = float(b1.get("min_y", b1.get("y", 0.0)))
    x1_max = float(b1.get("max_x", x1_min + float(b1.get("width", 0.0))))
    y1_max = float(b1.get("max_y", y1_min + float(b1.get("height", 0.0))))

    x2_min = float(b2.get("min_x", b2.get("x", 0.0)))
    y2_min = float(b2.get("min_y", b2.get("y", 0.0)))
    x2_max = float(b2.get("max_x", x2_min + float(b2.get("width", 0.0))))
    y2_max = float(b2.get("max_y", y2_min + float(b2.get("height", 0.0))))

    inter_xmin = max(x1_min, x2_min)
    inter_ymin = max(y1_min, y2_min)
    inter_xmax = min(x1_max, x2_max)
    inter_ymax = min(y1_max, y2_max)

    inter_w = max(0.0, inter_xmax - inter_xmin)
    inter_h = max(0.0, inter_ymax - inter_ymin)
    intersection = inter_w * inter_h

    area1 = max(0.0, x1_max - x1_min) * max(0.0, y1_max - y1_min)
    area2 = max(0.0, x2_max - x2_min) * max(0.0, y2_max - y2_min)
    union = area1 + area2 - intersection

    if union <= 0.0:
        return 0.0
    return round(float(intersection / union), 4)
