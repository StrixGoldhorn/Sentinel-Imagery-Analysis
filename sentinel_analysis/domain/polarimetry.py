"""Domain models and value objects for SAR polarimetry and preset configurations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PolarimetricPresetType(str, Enum):
    """Standard SAR polarimetric visualization and analysis presets."""

    VV = "vv"
    VH = "vh"
    VV_VH_RATIO = "vv_vh_ratio"
    FALSE_COLOR_COMPOSITE = "false_color_composite"
    CHANGE_DETECTION = "change_detection"


@dataclass(frozen=True)
class PolarimetricPreset:
    """Tactical description and band configuration for a polarimetric preset."""

    preset_id: str
    name: str
    display_title: str
    description: str
    tactical_purpose: str
    channels_description: str
    color_mapping: dict[str, str]
    supports_multitemporal: bool = False
    recommended_min_dynamic_db: float = -25.0
    recommended_max_dynamic_db: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "name": self.name,
            "display_title": self.display_title,
            "description": self.description,
            "tactical_purpose": self.tactical_purpose,
            "channels_description": self.channels_description,
            "color_mapping": dict(self.color_mapping),
            "supports_multitemporal": self.supports_multitemporal,
            "recommended_min_dynamic_db": self.recommended_min_dynamic_db,
            "recommended_max_dynamic_db": self.recommended_max_dynamic_db,
        }


class PolarimetricPresetCatalog:
    """Standard catalog of SAR polarization presets for maritime intelligence."""

    _PRESETS: dict[str, PolarimetricPreset] = {
        PolarimetricPresetType.VV.value: PolarimetricPreset(
            preset_id=PolarimetricPresetType.VV.value,
            name="VV",
            display_title="VV Co-Polarization (Single-Bounce Surface)",
            description=(
                "Vertical transmit and vertical receive channel. Highlights sea surface roughness, "
                "wind patterns, ocean currents, and direct metallic hull backscatter."
            ),
            tactical_purpose="Direct surface scattering; waterline ship hull detection and sea-state characterization.",
            channels_description="Monochrome single channel: VV backscatter amplitude (dB or digital number).",
            color_mapping={"intensity": "VV backscatter amplitude"},
            supports_multitemporal=False,
            recommended_min_dynamic_db=-25.0,
            recommended_max_dynamic_db=0.0,
        ),
        PolarimetricPresetType.VH.value: PolarimetricPreset(
            preset_id=PolarimetricPresetType.VH.value,
            name="VH",
            display_title="VH Cross-Polarization (Volume & Dihedral Scattering)",
            description=(
                "Vertical transmit and horizontal receive channel. Depolarized returns from complex "
                "vessel superstructures, container stacks, deck cranes, and mast geometries while sea clutter is suppressed."
            ),
            tactical_purpose="Superstructure isolation; high target-to-clutter ratio (TCR) for small and medium vessels.",
            channels_description="Monochrome single channel: VH backscatter amplitude with clutter rejection.",
            color_mapping={"intensity": "VH backscatter amplitude"},
            supports_multitemporal=False,
            recommended_min_dynamic_db=-30.0,
            recommended_max_dynamic_db=-5.0,
        ),
        PolarimetricPresetType.VV_VH_RATIO.value: PolarimetricPreset(
            preset_id=PolarimetricPresetType.VV_VH_RATIO.value,
            name="VV/VH Ratio",
            display_title="VV/VH Polarization Ratio (Depolarization Index)",
            description=(
                "Ratio of co-polarization to cross-polarization backscatter. Rough sea surfaces exhibit "
                "strong single-bounce returns (high VV/VH ratio), whereas metallic ships cause intense depolarization "
                "(low VV/VH ratio), cleanly separating ships from wind streaks."
            ),
            tactical_purpose="Discriminating metallic man-made structures from ocean clutter, wave breaks, and azimuth ambiguities.",
            channels_description="Normalized ratio map: I_VV / (I_VH + epsilon) or sigma0_VV(dB) - sigma0_VH(dB).",
            color_mapping={"intensity": "VV/VH ratio magnitude (inverted: vessels dark/bright)"},
            supports_multitemporal=False,
            recommended_min_dynamic_db=0.0,
            recommended_max_dynamic_db=15.0,
        ),
        PolarimetricPresetType.FALSE_COLOR_COMPOSITE.value: PolarimetricPreset(
            preset_id=PolarimetricPresetType.FALSE_COLOR_COMPOSITE.value,
            name="False Color Composite",
            display_title="Polarimetric False-Color Composite (RGB)",
            description=(
                "Three-channel RGB color composite mapping: Red = VV amplitude, Green = VH amplitude, "
                "Blue = VV/VH ratio (or normalized difference). Vessels appear brightly colored (yellow/white/green) "
                "against red/dark ocean background."
            ),
            tactical_purpose="Comprehensive multi-polarization visual reconnaissance combining surface, volume, and ratio cues.",
            channels_description="RGB 3-channel: Red=VV (Surface), Green=VH (Superstructure), Blue=Ratio (Depolarization).",
            color_mapping={
                "red": "VV amplitude (surface roughness)",
                "green": "VH amplitude (superstructure depolarization)",
                "blue": "VV/VH ratio or difference (target separation)",
            },
            supports_multitemporal=False,
            recommended_min_dynamic_db=-25.0,
            recommended_max_dynamic_db=5.0,
        ),
        PolarimetricPresetType.CHANGE_DETECTION.value: PolarimetricPreset(
            preset_id=PolarimetricPresetType.CHANGE_DETECTION.value,
            name="SAR Change Detection",
            display_title="Multi-Temporal Change Detection (T2 vs T1)",
            description=(
                "Differential radar backscatter between repeat-pass satellite acquisitions. "
                "Highlights newly arrived vessels (green), departed vessels (red), and persistent offshore structures (cyan)."
            ),
            tactical_purpose="Tracking port occupancy, dark vessel arrivals/departures, and anchorage turnover over time.",
            channels_description="Differential composite: Green=Arrived (+dB surge), Red=Departed (-dB drop), Grayscale=Persistent.",
            color_mapping={
                "green": "Arrived targets (T2 > T1)",
                "red": "Departed targets (T1 > T2)",
                "cyan": "Persistent offshore assets",
                "gray": "Unchanged ocean/terrain",
            },
            supports_multitemporal=True,
            recommended_min_dynamic_db=-10.0,
            recommended_max_dynamic_db=10.0,
        ),
    }

    @classmethod
    def get_all(cls) -> list[PolarimetricPreset]:
        """Return all supported polarimetric presets in recommended order."""
        return list(cls._PRESETS.values())

    @classmethod
    def get_preset(cls, preset_id: str) -> PolarimetricPreset:
        """Get specific preset by key or raise KeyError."""
        clean_id = (preset_id or "").lower().strip()
        if clean_id not in cls._PRESETS:
            valid_keys = ", ".join(cls._PRESETS.keys())
            raise ValueError(f"Unknown polarimetric preset '{preset_id}'. Valid presets: {valid_keys}")
        return cls._PRESETS[clean_id]

    @classmethod
    def is_valid(cls, preset_id: str) -> bool:
        """Check if preset identifier is recognized."""
        return (preset_id or "").lower().strip() in cls._PRESETS
