"""Domain models for tactical maritime risk score decomposition.

Breaks down opaque risk / suspicion scores into transparent, inspectable components:
- DARK_UNCORRELATED: Absence of correlated AIS broadcast, radar RCS presence, SOLAS carriage compliance.
- KINEMATIC_SPOOFING: Speed violations, teleportation, course discontinuities, wake vs AIS heading divergence.
- IDENTITY_INTEGRITY: MMSI reuse, duplicate identities, flag/callsign mutations, high-risk registries.
- BEHAVIORAL_TACTICAL: Open-water loitering, STS transshipment rendezvous, convoy formation, unusual routing.
- ENVIRONMENTAL: Off-corridor transits, shallow bathymetry, marine protected areas, infrastructure proximity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RiskCategory(str, Enum):
    DARK_UNCORRELATED = "DARK_UNCORRELATED"
    KINEMATIC_SPOOFING = "KINEMATIC_SPOOFING"
    IDENTITY_INTEGRITY = "IDENTITY_INTEGRITY"
    BEHAVIORAL_TACTICAL = "BEHAVIORAL_TACTICAL"
    ENVIRONMENTAL = "ENVIRONMENTAL"


class RiskThreatLevel(str, Enum):
    LOW = "LOW"             # 0 - 25
    GUARD = "GUARD"         # 26 - 50
    ELEVATED = "ELEVATED"   # 51 - 70
    HIGH = "HIGH"           # 71 - 85
    CRITICAL = "CRITICAL"   # 86 - 100


def threat_level_from_score(score: float) -> RiskThreatLevel:
    if score >= 86.0:
        return RiskThreatLevel.CRITICAL
    if score >= 71.0:
        return RiskThreatLevel.HIGH
    if score >= 51.0:
        return RiskThreatLevel.ELEVATED
    if score >= 26.0:
        return RiskThreatLevel.GUARD
    return RiskThreatLevel.LOW


@dataclass(frozen=True)
class RiskFactor:
    """An individual signal that either elevates or mitigates maritime risk."""

    name: str
    category: RiskCategory
    score_contribution: float
    weight: float
    description: str
    is_mitigating: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category.value if isinstance(self.category, RiskCategory) else str(self.category),
            "score_contribution": round(self.score_contribution, 1),
            "weight": round(self.weight, 2),
            "description": self.description,
            "is_mitigating": self.is_mitigating,
        }


@dataclass(frozen=True)
class RiskComponentScore:
    """Sub-score evaluation for a specific tactical intelligence dimension."""

    category: RiskCategory
    raw_score: float
    weight: float
    weighted_score: float
    factors: list[RiskFactor] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value if isinstance(self.category, RiskCategory) else str(self.category),
            "raw_score": round(self.raw_score, 1),
            "weight": round(self.weight, 2),
            "weighted_score": round(self.weighted_score, 1),
            "factors": [f.to_dict() for f in self.factors],
        }


@dataclass(frozen=True)
class RiskScoreDecomposition:
    """Full explainable breakdown of a candidate's tactical risk score."""

    target_id: str
    total_risk_score: float
    threat_level: RiskThreatLevel
    primary_driver: str
    component_scores: dict[str, RiskComponentScore]
    contributing_factors: list[RiskFactor]
    mitigating_factors: list[RiskFactor]
    analyst_summary: str
    recommended_actions: list[str]
    detection_index: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "detection_index": self.detection_index,
            "latitude": round(self.latitude, 6) if self.latitude is not None else None,
            "longitude": round(self.longitude, 6) if self.longitude is not None else None,
            "total_risk_score": round(self.total_risk_score, 1),
            "threat_level": self.threat_level.value if isinstance(self.threat_level, RiskThreatLevel) else str(self.threat_level),
            "primary_driver": self.primary_driver,
            "component_scores": {k: v.to_dict() for k, v in self.component_scores.items()},
            "contributing_factors": [f.to_dict() for f in self.contributing_factors],
            "mitigating_factors": [f.to_dict() for f in self.mitigating_factors],
            "analyst_summary": self.analyst_summary,
            "recommended_actions": list(self.recommended_actions),
            "metadata": self.metadata,
        }
