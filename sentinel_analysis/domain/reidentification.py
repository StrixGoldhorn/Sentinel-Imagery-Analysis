"""Domain models and mathematical algorithms for multi-pass satellite vessel re-identification.

Enables matching candidate vessel detections across multiple satellite passes (SAR and optical)
using dimensional similarity (length, beam, aspect ratio), heading alignment, kinematic
dead-reckoning displacement feasibility, and competing-candidate ranking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.domain.identity_anomalies import haversine_distance_km


def initial_compass_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the initial compass bearing in degrees [0, 360) from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - (math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda))
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return round(bearing, 1)


class ReIDMatchStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    PLAUSIBLE = "PLAUSIBLE"
    PROBABLE = "PROBABLE"
    UNLIKELY = "UNLIKELY"


@dataclass(frozen=True)
class PassDetectionCandidate:
    """A detected vessel candidate from a satellite pass."""

    detection_id: str = ""
    scan_id: str = ""
    detection_index: int = 0
    latitude: float = 0.0
    longitude: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    heading_deg: Optional[float] = None
    speed_knots: Optional[float] = None
    rcs_mean: Optional[float] = None
    pixel_box: Optional[dict[str, float]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    heading: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.detection_id:
            object.__setattr__(self, "detection_id", f"det_{self.detection_index}")
        if not self.scan_id:
            object.__setattr__(self, "scan_id", "default_scan")
        eff_heading = self.heading_deg if self.heading_deg is not None else self.heading
        object.__setattr__(self, "heading_deg", eff_heading)
        object.__setattr__(self, "heading", eff_heading)
        if not (-90.0 <= self.latitude <= 90.0):
            raise DomainValidationError(f"Invalid latitude: {self.latitude}")
        if not (-180.0 <= self.longitude <= 180.0):
            raise DomainValidationError(f"Invalid longitude: {self.longitude}")

    @property
    def aspect_ratio(self) -> Optional[float]:
        if self.length_m and self.width_m and self.width_m > 0:
            return self.length_m / self.width_m
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "detection_id": self.detection_id,
            "scan_id": self.scan_id,
            "detection_index": self.detection_index,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "timestamp": self.timestamp.isoformat() if hasattr(self.timestamp, "isoformat") else str(self.timestamp),
            "length_m": round(self.length_m, 1) if self.length_m is not None else None,
            "width_m": round(self.width_m, 1) if self.width_m is not None else None,
            "heading_deg": round(self.heading_deg, 1) if self.heading_deg is not None else None,
            "speed_knots": round(self.speed_knots, 1) if self.speed_knots is not None else None,
            "rcs_mean": round(self.rcs_mean, 2) if self.rcs_mean is not None else None,
            "pixel_box": self.pixel_box,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ReIDSimilarityScore:
    """Detailed similarity metrics decomposed across physical and kinematic axes."""

    size_score: float
    heading_score: float
    kinematic_score: float
    overall_score: float
    distance_km: float
    distance_nm: float
    time_delta_hours: float
    implied_speed_knots: float
    size_diff_m: Optional[float] = None
    heading_diff_deg: Optional[float] = None
    predicted_position_error_km: Optional[float] = None

    @property
    def composite_score(self) -> float:
        return self.overall_score

    @property
    def dimension_similarity(self) -> float:
        return self.size_score

    @property
    def heading_similarity(self) -> float:
        return self.heading_score

    @property
    def kinematic_feasibility(self) -> float:
        return self.kinematic_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "size_score": round(self.size_score, 3),
            "heading_score": round(self.heading_score, 3),
            "kinematic_score": round(self.kinematic_score, 3),
            "overall_score": round(self.overall_score, 3),
            "distance_km": round(self.distance_km, 2),
            "distance_nm": round(self.distance_nm, 2),
            "time_delta_hours": round(self.time_delta_hours, 2),
            "implied_speed_knots": round(self.implied_speed_knots, 1),
            "size_diff_m": round(self.size_diff_m, 1) if self.size_diff_m is not None else None,
            "heading_diff_deg": round(self.heading_diff_deg, 1) if self.heading_diff_deg is not None else None,
            "predicted_position_error_km": round(self.predicted_position_error_km, 2)
            if self.predicted_position_error_km is not None
            else None,
        }


@dataclass(frozen=True)
class VesselReIDMatch:
    """A matched vessel pair across two satellite passes."""

    match_id: str
    target_1: PassDetectionCandidate
    target_2: PassDetectionCandidate
    similarity: ReIDSimilarityScore
    confidence: float
    match_status: ReIDMatchStatus
    explanation: str
    competing_candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        status_val = self.match_status.value if isinstance(self.match_status, ReIDMatchStatus) else str(self.match_status)
        return {
            "match_id": self.match_id,
            "target_1": self.target_1.to_dict(),
            "target_2": self.target_2.to_dict(),
            "current_detection_index": self.target_1.detection_index,
            "prior_detection_index": self.target_2.detection_index,
            "similarity": self.similarity.to_dict(),
            "similarity_score": round(self.similarity.overall_score, 3),
            "confidence": round(self.confidence, 3),
            "match_status": status_val,
            "status": status_val,
            "explanation": self.explanation,
            "competing_candidates": self.competing_candidates,
        }


def calculate_reid_similarity(
    c1: PassDetectionCandidate,
    c2: PassDetectionCandidate,
    time_delta_hours: Optional[float] = None,
    max_commercial_speed_knots: float = 35.0,
    length_sigma_m: float = 14.0,
    width_sigma_m: float = 6.0,
    heading_sigma_deg: float = 30.0,
) -> ReIDSimilarityScore:
    """Calculate multi-criteria similarity score between two vessel detections from different passes."""
    # 1. Geographic distance and elapsed time
    dist_km = haversine_distance_km(c1.latitude, c1.longitude, c2.latitude, c2.longitude)
    dist_nm = dist_km * 0.539957

    if time_delta_hours is not None:
        dt_hours = float(time_delta_hours)
    else:
        t1 = c1.timestamp if c1.timestamp.tzinfo else c1.timestamp.replace(tzinfo=timezone.utc)
        t2 = c2.timestamp if c2.timestamp.tzinfo else c2.timestamp.replace(tzinfo=timezone.utc)
        dt_hours = abs((t2 - t1).total_seconds()) / 3600.0

    if dt_hours > 0.0001:
        implied_speed = dist_nm / dt_hours
    else:
        implied_speed = 0.0 if dist_km < 0.2 else 999.0

    # 2. Size similarity (Gaussian decay on length, beam, aspect ratio)
    size_weights = []
    size_scores = []
    size_diff: Optional[float] = None

    if c1.length_m is not None and c2.length_m is not None:
        delta_l = abs(c1.length_m - c2.length_m)
        size_diff = delta_l
        s_len = math.exp(-(delta_l ** 2) / (2.0 * (length_sigma_m ** 2)))
        size_scores.append(s_len)
        size_weights.append(0.55)

    if c1.width_m is not None and c2.width_m is not None:
        delta_w = abs(c1.width_m - c2.width_m)
        s_wid = math.exp(-(delta_w ** 2) / (2.0 * (width_sigma_m ** 2)))
        size_scores.append(s_wid)
        size_weights.append(0.25)

    if c1.aspect_ratio is not None and c2.aspect_ratio is not None:
        delta_ar = abs(c1.aspect_ratio - c2.aspect_ratio)
        s_ar = math.exp(-(delta_ar ** 2) / 0.5)
        size_scores.append(s_ar)
        size_weights.append(0.20)

    if size_scores:
        total_w = sum(size_weights)
        size_score = sum(s * w for s, w in zip(size_scores, size_weights)) / total_w
    else:
        size_score = 0.50  # Neutral fallback if radar dimensions unmeasured

    # 3. Heading similarity
    heading_diff: Optional[float] = None
    if c1.heading_deg is not None and c2.heading_deg is not None:
        raw_diff = abs(c1.heading_deg - c2.heading_deg) % 360.0
        circ_diff = min(raw_diff, 360.0 - raw_diff)
        # Check bidirectional 180-deg symmetry in case radar heading lacks wake direction
        sym_diff = min(circ_diff, abs(180.0 - circ_diff))
        heading_diff = sym_diff
        heading_score = math.exp(-(sym_diff ** 2) / (2.0 * (heading_sigma_deg ** 2)))
    else:
        heading_score = 0.50  # Neutral fallback

    # 4. Kinematic displacement feasibility
    # Physical speed plausibility
    if implied_speed <= max_commercial_speed_knots:
        # Feasible speed: smooth score
        kin_speed_score = 1.0 - 0.40 * (implied_speed / max_commercial_speed_knots)
    else:
        excess = implied_speed - max_commercial_speed_knots
        # Sharp exponential drop for physically impossible speeds
        kin_speed_score = max(0.0, math.exp(-(excess ** 2) / 120.0) * 0.5)

    # Dead reckoning alignment: does displacement vector align with initial heading?
    pred_pos_err_km: Optional[float] = None
    vector_alignment = 0.5
    if dist_km > 0.3 and c1.heading_deg is not None:
        bearing = initial_compass_bearing(c1.latitude, c1.longitude, c2.latitude, c2.longitude)
        b_diff = min(abs(c1.heading_deg - bearing), 360.0 - abs(c1.heading_deg - bearing))
        # Alignment is highest when moving in heading direction (or opposite if 180 ambiguous)
        min_b_diff = min(b_diff, abs(180.0 - b_diff))
        vector_alignment = max(0.0, math.cos(math.radians(min_b_diff)))

    # Dead reckoning projection if target 1 speed is known
    if c1.speed_knots is not None and c1.heading_deg is not None and dt_hours > 0.01:
        v_kmh = c1.speed_knots * 1.852
        pred_dist_km = v_kmh * dt_hours
        rad_h = math.radians(c1.heading_deg)
        # Approximate dead-reckoning position
        dlat = (pred_dist_km / 111.0) * math.cos(rad_h)
        dlon = (pred_dist_km / (111.0 * max(0.01, math.cos(math.radians(c1.latitude))))) * math.sin(rad_h)
        pred_lat = c1.latitude + dlat
        pred_lon = c1.longitude + dlon
        pred_pos_err_km = haversine_distance_km(pred_lat, pred_lon, c2.latitude, c2.longitude)
        dr_score = math.exp(-(pred_pos_err_km ** 2) / (2.0 * max(1.0, pred_dist_km * 0.5) ** 2))
        kin_score = 0.5 * kin_speed_score + 0.3 * dr_score + 0.2 * vector_alignment
    else:
        kin_score = 0.7 * kin_speed_score + 0.3 * vector_alignment

    # 5. Composite Joint Score
    overall = 0.40 * size_score + 0.40 * kin_score + 0.20 * heading_score
    # Kinematic gating: physically impossible movements cannot produce high re-identification
    if kin_score < 0.35:
        overall *= max(0.1, kin_score / 0.35)
    overall = max(0.0, min(1.0, overall))

    return ReIDSimilarityScore(
        size_score=size_score,
        heading_score=heading_score,
        kinematic_score=kin_score,
        overall_score=overall,
        distance_km=dist_km,
        distance_nm=dist_nm,
        time_delta_hours=dt_hours,
        implied_speed_knots=implied_speed,
        size_diff_m=size_diff,
        heading_diff_deg=heading_diff,
        predicted_position_error_km=pred_pos_err_km,
    )
