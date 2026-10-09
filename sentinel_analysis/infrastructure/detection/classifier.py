"""Standardized SAR ship classification and maritime sizing taxonomy."""

from __future__ import annotations

from typing import Optional


def classify_vessel_physical(
    length_m: float,
    beam_m: float,
    mean_intensity: float = 120.0,
    peak_intensity: float = 230.0,
) -> tuple[str, float]:
    """Rule-based physical SAR ship classification from dimensions and radar cross-section.

    Classifies detected vessels into standard categories:
    - Military
    - Cargo
    - Tanker
    - Passenger
    - Tug
    - Fishing
    - Other

    Args:
        length_m: Estimated hull length in meters.
        beam_m: Estimated hull beam (width) in meters.
        mean_intensity: Average radar backscatter pixel value in target mask (0-255).
        peak_intensity: Maximum radar backscatter pixel value in target mask (0-255).

    Returns:
        tuple[str, float]: (vessel_class, confidence)
    """
    aspect_ratio = length_m / max(1.0, beam_m)

    # 1. High radar return with sharp angular features & streamlined aspect ratio (corvettes, frigates, patrol)
    if 40.0 <= length_m < 160.0 and peak_intensity >= 230 and aspect_ratio >= 4.8:
        return "Military", 0.80

    # 2. Very large vessels (>= 180m): Tankers / Bulkers / Large Container
    if length_m >= 180.0:
        if aspect_ratio >= 5.5:
            return "Cargo", 0.90
        return "Tanker", 0.88

    # 3. Medium-large commercial vessels (70m - 180m)
    if length_m >= 70.0:
        if aspect_ratio < 3.8 and mean_intensity > 120:
            return "Passenger", 0.82
        if aspect_ratio >= 4.0:
            return "Cargo", 0.85
        return "Tanker", 0.80

    # 4. Squat working vessels (Tugs, Workboats)
    if length_m < 45.0 and aspect_ratio <= 2.8:
        return "Tug", 0.82

    # 5. Small fishing craft
    if length_m < 50.0:
        return "Fishing", 0.86

    return "Other", 0.60


def get_vessel_hierarchy_label(
    vessel_class: Optional[str],
    length_m: float,
    beam_m: float,
) -> str:
    """Format vessel classification with detailed maritime sizing tier.

    Provides standardized IMO / naval taxonomy for intelligence reporting:
    - Super-Tanker (VLCC/ULCC)
    - Large Container Ship
    - Capesize Bulker / Tanker
    - Panamax Container / Cargo
    - Panamax Tanker / Bulker
    - Naval Surface Combatant
    - Passenger / Ro-Ro Ferry
    - Handymax / Coastal Commercial
    - Tug / Harbor Workboat
    - Offshore Support / Large Fishing
    - Small Craft / Artisanal Fishing
    - Small Craft / Skiff

    Args:
        vessel_class: Base vessel class (e.g., Cargo, Tanker, Fishing, etc.) or None.
        length_m: Estimated length in meters.
        beam_m: Estimated beam in meters.

    Returns:
        str: Human-readable operational classification label.
    """
    v_class = (vessel_class or "").strip()

    if length_m >= 280.0 or (length_m >= 240.0 and beam_m >= 44.0):
        if "Tanker" in v_class or "VLCC" in v_class or "ULCC" in v_class:
            return "Super-Tanker (VLCC/ULCC)"
        if "Cargo" in v_class or "Container" in v_class:
            return "Ultra-Large Container Vessel"
        return "Ultra-Large Commercial / Bulker"

    if length_m >= 200.0:
        if "Cargo" in v_class or "Container" in v_class:
            return "Large Container Ship"
        if "Tanker" in v_class:
            return "Suezmax / Aframax Tanker"
        return "Capesize Bulker / Tanker"

    if length_m >= 130.0:
        if "Passenger" in v_class or "Cruise" in v_class:
            return "Passenger / Cruise / Ferry"
        if "Cargo" in v_class or "Container" in v_class:
            return "Panamax Container / Cargo"
        if "Tanker" in v_class:
            return "Panamax Product Tanker"
        return "Panamax Commercial Vessel"

    if length_m >= 70.0:
        if "Military" in v_class or "Naval" in v_class:
            return "Naval Surface Combatant"
        if "Passenger" in v_class:
            return "Coastal Passenger / Ro-Ro Ferry"
        return "Handymax / Coastal Commercial"

    if length_m >= 30.0:
        if "Tug" in v_class:
            return "Tug / Harbor Workboat"
        if "Fishing" in v_class:
            return "Commercial Trawler / Fishing"
        return "Offshore Support / Workboat"

    if length_m > 0.0:
        if "Fishing" in v_class:
            return "Small Craft / Artisanal Fishing"
        return "Small Craft / Skiff"

    return v_class or "Commercial / Unclassified"
