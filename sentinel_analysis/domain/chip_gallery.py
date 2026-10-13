"""Domain models for multimodal candidate chip galleries.

Represents 4-quadrant multi-sensor surveillance chips around any detected candidate:
1. SAR: High-resolution Synthetic Aperture Radar crop with bounding box, wake vector, and intensity stats.
2. OPTICAL: Concurrent optical validation crop (Sentinel-2 multi-spectral) or tactical cloud-penetration card.
3. PREVIOUS_PASS: Co-registered prior satellite pass showing temporal change (presence/absence).
4. AIS_CONTEXT: 360-degree tactical mini-map with range rings, nearby AIS contacts, and kinematics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ChipModality(str, Enum):
    SAR = "SAR"
    OPTICAL = "OPTICAL"
    PREVIOUS_PASS = "PREVIOUS_PASS"
    AIS_CONTEXT = "AIS_CONTEXT"


@dataclass(frozen=True)
class ChipArtifact:
    """An individual image chip artifact within the multi-sensor candidate gallery."""

    modality: ChipModality
    available: bool
    status: str
    image_data_uri: str
    width: int
    height: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "modality": self.modality.value if isinstance(self.modality, ChipModality) else str(self.modality),
            "available": self.available,
            "status": self.status,
            "image_data_uri": self.image_data_uri,
            "width": self.width,
            "height": self.height,
            "metadata": dict(self.metadata),
        }


@dataclass
class CandidateChipGallery:
    """Complete 4-sensor situational chip gallery for a vessel detection candidate."""

    detection_index: int
    scan_id: str
    target_coordinates: dict[str, Any]
    chips: dict[str, ChipArtifact] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detection_index": self.detection_index,
            "scan_id": self.scan_id,
            "target_coordinates": dict(self.target_coordinates),
            "chips": {k: v.to_dict() for k, v in self.chips.items()},
            "summary": dict(self.summary),
        }
