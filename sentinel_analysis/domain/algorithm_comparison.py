"""Domain entities and algorithms for comparing SAR ship detection models.

Supports side-by-side evaluation of CFAR, Classical CV, and ONNX Deep Learning detectors:
- Pairwise Intersection-over-Union (IoU) box matching
- Jaccard similarity and cross-algorithm agreement matrices
- Multi-algorithm consensus clustering (3-way consensus, 2-way consensus, unique hits)
- Performance and dimension metrics across detectors
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Optional, Sequence


def calculate_box_iou(
    box_a: tuple[float, float, float, float] | dict[str, Any],
    box_b: tuple[float, float, float, float] | dict[str, Any],
) -> float:
    """Calculate Intersection-over-Union (IoU) between two bounding boxes.

    Boxes are represented as (x, y, width, height) or dicts with keys 'x', 'y', 'width', 'height'.
    """
    if isinstance(box_a, dict):
        xa1, ya1 = float(box_a["x"]), float(box_a["y"])
        wa, ha = float(box_a["width"]), float(box_a["height"])
    else:
        xa1, ya1, wa, ha = float(box_a[0]), float(box_a[1]), float(box_a[2]), float(box_a[3])

    if isinstance(box_b, dict):
        xb1, yb1 = float(box_b["x"]), float(box_b["y"])
        wb, hb = float(box_b["width"]), float(box_b["height"])
    else:
        xb1, yb1, wb, hb = float(box_b[0]), float(box_b[1]), float(box_b[2]), float(box_b[3])

    xa2, ya2 = xa1 + wa, ya1 + ha
    xb2, yb2 = xb1 + wb, yb1 + hb

    inter_x1 = max(xa1, xb1)
    inter_y1 = max(ya1, yb1)
    inter_x2 = min(xa2, xb2)
    inter_y2 = min(ya2, yb2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    intersection = inter_w * inter_h

    area_a = max(0.0, wa * ha)
    area_b = max(0.0, wb * hb)
    union = area_a + area_b - intersection

    if union <= 0.0:
        return 0.0
    return float(intersection / union)


@dataclass(frozen=True)
class PairwiseMatch:
    """Agreement statistics between two detection algorithms."""

    algo_a: str
    algo_b: str
    matched_count: int
    unmatched_a_count: int
    unmatched_b_count: int
    jaccard_similarity: float
    mean_iou: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "algo_a": self.algo_a,
            "algo_b": self.algo_b,
            "matched_count": self.matched_count,
            "unmatched_a_count": self.unmatched_a_count,
            "unmatched_b_count": self.unmatched_b_count,
            "jaccard_similarity": round(self.jaccard_similarity, 4),
            "mean_iou": round(self.mean_iou, 4),
        }


@dataclass(frozen=True)
class ConsensusContact:
    """Unified target formed by consensus clustering of algorithm detections."""

    contact_id: str
    agreement_level: str  # "HIGH" (3/3), "MODERATE" (2/3), "SINGLE_ALGORITHM" (1/3)
    detected_by: list[str]
    consensus_box: dict[str, int]
    mean_confidence: float
    algorithm_detections: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "agreement_level": self.agreement_level,
            "detected_by": list(self.detected_by),
            "consensus_box": dict(self.consensus_box),
            "mean_confidence": round(self.mean_confidence, 4),
            "algorithm_detections": self.algorithm_detections,
        }


@dataclass(frozen=True)
class AlgorithmMetrics:
    """Summary metrics for a specific detection model."""

    algorithm: str
    detection_count: int
    mean_confidence: float
    mean_length_m: Optional[float] = None
    mean_beam_m: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "detection_count": self.detection_count,
            "mean_confidence": round(self.mean_confidence, 4),
            "mean_length_m": round(self.mean_length_m, 2) if self.mean_length_m is not None else None,
            "mean_beam_m": round(self.mean_beam_m, 2) if self.mean_beam_m is not None else None,
        }


@dataclass(frozen=True)
class DetectionComparisonResult:
    """Side-by-side multi-algorithm comparison result."""

    scan_id: Optional[str]
    algorithm_metrics: dict[str, AlgorithmMetrics]
    pairwise_metrics: dict[str, PairwiseMatch]
    three_way_consensus_count: int
    two_way_consensus_count: int
    unique_counts: dict[str, int]
    total_unique_targets: int
    consensus_contacts: list[ConsensusContact]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "total_unique_targets": self.total_unique_targets,
            "three_way_consensus_count": self.three_way_consensus_count,
            "two_way_consensus_count": self.two_way_consensus_count,
            "unique_counts": dict(self.unique_counts),
            "algorithm_metrics": {k: v.to_dict() for k, v in self.algorithm_metrics.items()},
            "pairwise_metrics": {k: v.to_dict() for k, v in self.pairwise_metrics.items()},
            "consensus_contacts": [c.to_dict() for c in self.consensus_contacts],
            "summary": self.summary,
        }
