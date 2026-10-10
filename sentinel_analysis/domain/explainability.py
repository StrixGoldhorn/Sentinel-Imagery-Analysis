"""Domain models and tactical explainability rationale engine for maritime contacts.

Provides explainability breakdowns answering why a vessel was classified as:
- DARK_VESSEL: Radar RCS presence with absence of correlated AIS broadcast
- SPOOFED_AIS: Kinematic divergence (wake heading/speed vs AIS) or identity anomalies
- SOLAS_SUSPECT: Length >= 50m / 300 GT commercial craft violating mandatory AIS carriage
- TRANSSHIPMENT_SUSPECT: Ship-to-Ship close-proximity rendezvous outside designated zones
- OFFSHORE_INFRASTRUCTURE: Fixed platform/wind turbine false-alarm suppression
- COOPERATIVE_VESSEL: Nominal vessel with active, verified AIS telemetry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence


class TargetClassification(str, Enum):
    DARK_VESSEL = "DARK_VESSEL"
    SPOOFED_AIS = "SPOOFED_AIS"
    SOLAS_SUSPECT = "SOLAS_SUSPECT"
    TRANSSHIPMENT_SUSPECT = "TRANSSHIPMENT_SUSPECT"
    OFFSHORE_INFRASTRUCTURE = "OFFSHORE_INFRASTRUCTURE"
    COOPERATIVE_VESSEL = "COOPERATIVE_VESSEL"


class ThreatLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    ELEVATED = "ELEVATED"
    GUARD = "GUARD"
    LOW = "LOW"


@dataclass(frozen=True)
class EvidenceItem:
    """An individual piece of tactical evidence contributing to or mitigating suspicion."""

    factor: str
    description: str
    weight: float
    is_mitigating: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "description": self.description,
            "weight": round(self.weight, 2),
            "is_mitigating": self.is_mitigating,
        }


@dataclass(frozen=True)
class TargetRationale:
    """Detailed explainability rationale for a specific contact."""

    target_id: str
    primary_classification: TargetClassification
    secondary_classifications: list[TargetClassification]
    threat_level: ThreatLevel
    overall_suspicion_score: float
    tactical_summary: str
    contributing_evidence: list[EvidenceItem]
    mitigating_evidence: list[EvidenceItem]
    rules_triggered: list[str]
    recommended_action: str
    detection_index: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence(self) -> list[EvidenceItem]:
        return list(self.contributing_evidence) + list(self.mitigating_evidence)

    @property
    def summary_rationale(self) -> str:
        return self.tactical_summary

    @property
    def reason_codes(self) -> list[str]:
        return list(self.rules_triggered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "detection_index": self.detection_index,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "primary_classification": self.primary_classification.value,
            "secondary_classifications": [c.value for c in self.secondary_classifications],
            "threat_level": self.threat_level.value,
            "overall_suspicion_score": round(self.overall_suspicion_score, 2),
            "tactical_summary": self.tactical_summary,
            "evidence": [e.to_dict() for e in self.evidence],
            "contributing_evidence": [e.to_dict() for e in self.contributing_evidence],
            "mitigating_evidence": [e.to_dict() for e in self.mitigating_evidence],
            "rules_triggered": list(self.rules_triggered),
            "recommended_action": self.recommended_action,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ExplainabilityReport:
    """Aggregate tactical explainability report across an entire acquisition scan."""

    scan_id: Optional[str]
    total_targets: int
    dark_vessel_count: int
    spoofed_count: int
    solas_suspect_count: int
    transshipment_count: int
    cooperative_count: int
    infrastructure_count: int
    rationales: list[TargetRationale]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "total_targets": self.total_targets,
            "summary_counts": {
                "dark_vessel": self.dark_vessel_count,
                "spoofed_ais": self.spoofed_count,
                "solas_suspect": self.solas_suspect_count,
                "transshipment_suspect": self.transshipment_count,
                "cooperative_vessel": self.cooperative_count,
                "offshore_infrastructure": self.infrastructure_count,
            },
            "rationales": [r.to_dict() for r in self.rationales],
        }
