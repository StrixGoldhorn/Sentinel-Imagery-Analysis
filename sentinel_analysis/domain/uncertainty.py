"""Domain entity and utilities for calibrated contact uncertainty and reason codes.

Provides rigorous geospatial and kinematic uncertainty modeling:
- Spatial localization uncertainty (Circular Error Probable / CEP and error covariance ellipse)
- Dimension uncertainty bounds (hull length, beam, and heading margins of error)
- Calibrated multi-hypothesis association likelihood
- Tactical surveillance reason codes
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sentinel_analysis.domain.exceptions import DomainValidationError

# Standard tactical surveillance reason codes
REASON_RADAR_STRONG_REFLECTOR = "RADAR_STRONG_REFLECTOR"
REASON_RADAR_WEAK_SIGNAL = "RADAR_WEAK_SIGNAL"
REASON_RADAR_HIGH_ASPECT = "RADAR_HIGH_ASPECT"
REASON_WAKE_CONFIRMED = "WAKE_CONFIRMED"
REASON_AIS_KINEMATIC_MATCH = "AIS_KINEMATIC_MATCH"
REASON_AIS_BUFFER_ASSOCIATION = "AIS_BUFFER_ASSOCIATION"
REASON_DARK_VESSEL_SUSPECT = "DARK_VESSEL_SUSPECT"
REASON_SOLAS_TRANSPONDER_OFF = "SOLAS_TRANSPONDER_OFF"
REASON_SPEED_SPOOFING_DETECTED = "SPEED_SPOOFING_DETECTED"
REASON_COURSE_SPOOFING_DETECTED = "COURSE_SPOOFING_DETECTED"
REASON_DIMENSION_CONSISTENT = "DIMENSION_CONSISTENT"
REASON_DIMENSION_MISMATCH = "DIMENSION_MISMATCH"
REASON_OPTICAL_CONFIRMED = "OPTICAL_CONFIRMED"
REASON_OPTICAL_FALSE_ALARM = "OPTICAL_FALSE_ALARM"
REASON_PERSISTENT_STRUCTURE = "PERSISTENT_STRUCTURE"
REASON_NO_AIS_BROADCAST = "NO_AIS_BROADCAST"

# Concise aliases for tactical surveillance reason codes
REASON_RADAR_STRONG = REASON_RADAR_STRONG_REFLECTOR
REASON_RADAR_WEAK = REASON_RADAR_WEAK_SIGNAL
REASON_DARK_VESSEL = REASON_DARK_VESSEL_SUSPECT
REASON_SOLAS_OFF = REASON_SOLAS_TRANSPONDER_OFF
REASON_SPEED_SPOOFED = REASON_SPEED_SPOOFING_DETECTED
REASON_COURSE_SPOOFED = REASON_COURSE_SPOOFING_DETECTED
REASON_PERSISTENT_STRUCT = REASON_PERSISTENT_STRUCTURE


class UncertaintyDict(dict):
    """Dictionary subclass providing .to_dict() and attribute access for seamless chaining."""

    def to_dict(self) -> dict[str, Any]:
        return dict(self)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'UncertaintyDict' object has no attribute '{name}'")


@dataclass(frozen=True)
class SpatialUncertainty:
    """Spatial position error model for a radar contact.

    Attributes:
        cep_meters: Circular Error Probable (50% probability radius in meters).
        semi_major_axis_meters: Semi-major axis of the 95% spatial error ellipse in meters.
        semi_minor_axis_meters: Semi-minor axis of the 95% spatial error ellipse in meters.
        orientation_deg: Orientation angle of semi-major axis (degrees clockwise from True North).
        confidence_level: Statistical confidence level of the ellipse (typically 0.95).
    """

    cep_meters: float = 14.5
    semi_major_axis_meters: float = 18.0
    semi_minor_axis_meters: float = 12.0
    orientation_deg: float = 0.0
    confidence_level: float = 0.95
    semi_major_m: float | None = None
    semi_minor_m: float | None = None

    def __post_init__(self) -> None:
        if self.semi_major_m is not None:
            object.__setattr__(self, "semi_major_axis_meters", float(self.semi_major_m))
        if self.semi_minor_m is not None:
            object.__setattr__(self, "semi_minor_axis_meters", float(self.semi_minor_m))

        if self.cep_meters < 0:
            raise DomainValidationError("CEP meters must be non-negative")
        if self.semi_major_axis_meters < 0:
            raise DomainValidationError("Semi-major axis must be non-negative")
        if self.semi_minor_axis_meters < 0:
            raise DomainValidationError("Semi-minor axis must be non-negative")
        if not 0.0 <= self.confidence_level <= 1.0:
            raise DomainValidationError("Confidence level must be between 0 and 1")

        object.__setattr__(self, "cep_meters", round(float(self.cep_meters), 2))
        object.__setattr__(self, "semi_major_axis_meters", round(float(self.semi_major_axis_meters), 2))
        object.__setattr__(self, "semi_minor_axis_meters", round(float(self.semi_minor_axis_meters), 2))
        object.__setattr__(self, "semi_major_m", round(float(self.semi_major_axis_meters), 2))
        object.__setattr__(self, "semi_minor_m", round(float(self.semi_minor_axis_meters), 2))
        object.__setattr__(self, "orientation_deg", round(float(self.orientation_deg) % 360.0, 1))
        object.__setattr__(self, "confidence_level", round(float(self.confidence_level), 3))

    def __getitem__(self, item: str) -> Any:
        return self.to_dict()[item]

    def get(self, item: str, default: Any = None) -> Any:
        return self.to_dict().get(item, default)

    def to_dict(self) -> UncertaintyDict:
        return UncertaintyDict({
            "cep_meters": self.cep_meters,
            "semi_major_axis_meters": self.semi_major_axis_meters,
            "semi_minor_axis_meters": self.semi_minor_axis_meters,
            "semi_major_m": self.semi_major_axis_meters,
            "semi_minor_m": self.semi_minor_axis_meters,
            "orientation_deg": self.orientation_deg,
            "confidence_level": self.confidence_level,
        })


@dataclass(frozen=True)
class DimensionUncertainty:
    """Estimated physical dimension error bounds for a detected vessel contact.

    Attributes:
        length_uncertainty_m: Margin of error on estimated length in meters (± meters).
        beam_uncertainty_m: Margin of error on estimated beam/width in meters (± meters).
        heading_uncertainty_deg: Margin of error on estimated heading/angle in degrees (± degrees).
        confidence_level: Statistical confidence level of the bounds (typically 0.95).
        aspect_ratio_confidence: Confidence score of hull aspect ratio geometry (0.0 to 1.0).
    """

    length_uncertainty_m: float = 6.0
    beam_uncertainty_m: float = 3.5
    heading_uncertainty_deg: float = 7.5
    confidence_level: float = 0.95
    aspect_ratio_confidence: float = 0.95

    def __post_init__(self) -> None:
        if self.length_uncertainty_m < 0:
            raise DomainValidationError("Length uncertainty must be non-negative")
        if self.beam_uncertainty_m < 0:
            raise DomainValidationError("Beam uncertainty must be non-negative")
        if self.heading_uncertainty_deg < 0 or self.heading_uncertainty_deg > 180.0:
            raise DomainValidationError("Heading uncertainty must be between 0 and 180 degrees")
        if not 0.0 <= self.confidence_level <= 1.0:
            raise DomainValidationError("Confidence level must be between 0 and 1")
        if not 0.0 <= self.aspect_ratio_confidence <= 1.0:
            raise DomainValidationError("Aspect ratio confidence must be between 0 and 1")

        object.__setattr__(self, "length_uncertainty_m", round(float(self.length_uncertainty_m), 2))
        object.__setattr__(self, "beam_uncertainty_m", round(float(self.beam_uncertainty_m), 2))
        object.__setattr__(self, "heading_uncertainty_deg", round(float(self.heading_uncertainty_deg), 2))
        object.__setattr__(self, "confidence_level", round(float(self.confidence_level), 3))
        object.__setattr__(self, "aspect_ratio_confidence", round(float(self.aspect_ratio_confidence), 3))

    def __getitem__(self, item: str) -> Any:
        return self.to_dict()[item]

    def get(self, item: str, default: Any = None) -> Any:
        return self.to_dict().get(item, default)

    def to_dict(self) -> UncertaintyDict:
        return UncertaintyDict({
            "length_uncertainty_m": self.length_uncertainty_m,
            "beam_uncertainty_m": self.beam_uncertainty_m,
            "heading_uncertainty_deg": self.heading_uncertainty_deg,
            "confidence_level": self.confidence_level,
            "aspect_ratio_confidence": self.aspect_ratio_confidence,
        })


def calibrate_spatial_uncertainty(
    pixel_spacing_m: float = 10.0,
    length_m: float | None = None,
    beam_m: float | None = None,
    ais_ellipse: dict[str, Any] | None = None,
    ais_semi_major_m: float | None = None,
    ais_semi_minor_m: float | None = None,
    ais_orientation_deg: float | None = None,
    radar_snr_db: float | None = None,
    confidence: float | None = None,
    **kwargs: Any,
) -> SpatialUncertainty:
    """Calculate calibrated spatial uncertainty (CEP and 95% error ellipse) for a contact.

    Combines radar sensor pixel quantization error with kinematic AIS covariance when present.
    """
    ps = max(1.0, float(pixel_spacing_m))
    snr_factor = 1.0
    if radar_snr_db is not None:
        snr_factor = max(0.65, 1.0 - (float(radar_snr_db) - 10.0) * 0.02)

    sigma_radar_range = max(4.0, ps * 1.0 * snr_factor)
    sigma_radar_azimuth = max(5.0, ps * 1.3 * snr_factor)

    if ais_ellipse is None and (ais_semi_major_m is not None or ais_semi_minor_m is not None):
        ais_ellipse = {
            "semi_major_m": ais_semi_major_m or 0.0,
            "semi_minor_m": ais_semi_minor_m or 0.0,
            "orientation_deg": ais_orientation_deg or 0.0,
        }

    if ais_ellipse and isinstance(ais_ellipse, dict):
        ais_major = float(ais_ellipse.get("semi_major_axis_meters") or ais_ellipse.get("semi_major_m") or 0.0)
        ais_minor = float(ais_ellipse.get("semi_minor_axis_meters") or ais_ellipse.get("semi_minor_m") or 0.0)
        ais_ori = float(ais_ellipse.get("orientation_deg") or 0.0)

        # Combined root-sum-square error ellipse
        major = math.sqrt((sigma_radar_azimuth * 1.2)**2 + ais_major**2)
        minor = math.sqrt((sigma_radar_range * 1.1)**2 + ais_minor**2)
        orientation = ais_ori
    else:
        major = sigma_radar_azimuth * 1.5
        minor = sigma_radar_range * 1.2
        orientation = 0.0

    if minor > major:
        major, minor = minor, major

    # Bivariate normal CEP approximation (50% circular radius)
    cep = 0.562 * major + 0.589 * minor

    return SpatialUncertainty(
        cep_meters=round(cep, 1),
        semi_major_axis_meters=round(major, 1),
        semi_minor_axis_meters=round(minor, 1),
        orientation_deg=round(orientation, 1),
        confidence_level=0.95,
    )


def calibrate_dimension_uncertainty(
    length_m: float | None = None,
    beam_m: float | None = None,
    pixel_spacing_m: float = 10.0,
    angle_deg: float | None = None,
    heading_deg: float | None = None,
    confidence: float | None = None,
    radar_snr_db: float | None = None,
    **kwargs: Any,
) -> DimensionUncertainty:
    """Calculate calibrated physical dimension error bounds for a contact."""
    ps = max(1.0, float(pixel_spacing_m))
    l_val = max(1.0, float(length_m or 30.0))
    b_val = max(1.0, float(beam_m or 10.0))
    if angle_deg is None and heading_deg is not None:
        angle_deg = heading_deg

    # Length error: ~0.6 pixel resolution + 4% of vessel length due to side-lobes and radar blur
    l_unc = max(3.0, ps * 0.6 + (0.04 * l_val))
    # Beam error: ~0.4 pixel resolution + 5% of beam
    b_unc = max(2.0, ps * 0.4 + (0.05 * b_val))

    # Heading certainty is governed by aspect ratio (L/B)
    aspect = l_val / max(1.0, b_val)
    if aspect >= 4.0:
        h_unc = 5.0
        aspect_conf = 0.92
    elif aspect >= 2.5:
        h_unc = 8.0
        aspect_conf = 0.85
    elif aspect >= 1.5:
        h_unc = 12.0
        aspect_conf = 0.75
    else:
        h_unc = 20.0
        aspect_conf = 0.60

    return DimensionUncertainty(
        length_uncertainty_m=round(l_unc, 1),
        beam_uncertainty_m=round(b_unc, 1),
        heading_uncertainty_deg=round(h_unc, 1),
        confidence_level=0.95,
        aspect_ratio_confidence=aspect_conf,
    )


def calculate_association_likelihood(
    is_correlated: bool = True,
    match_type: str = "inside_box",
    distance_m: float = 0.0,
    tolerance_m: float = 250.0,
    speed_diff_knots: float | None = None,
    heading_diff_deg: float | None = None,
    radar_length_m: float | None = None,
    ais_length_m: float | None = None,
    *,
    dist_to_box_meters: float | None = None,
    tolerance_meters: float | None = None,
    is_inside_box: bool | None = None,
    spatial_distance_m: float | None = None,
    spatial_cep_m: float | None = None,
    speed_discrepancy_knots: float | None = None,
    course_discrepancy_deg: float | None = None,
    length_discrepancy_m: float | None = None,
    beam_discrepancy_m: float | None = None,
    sar_speed_knots: float | None = None,
    ais_speed_knots: float | None = None,
    sar_heading_deg: float | None = None,
    ais_heading_deg: float | None = None,
    sar_length_m: float | None = None,
    **kwargs: Any,
) -> float:
    """Calculate calibrated multi-factor association likelihood (0.0 to 1.0).

    Weights spatial proximity, kinematic velocity agreement, course alignment,
    and dimensional compatibility against hypothesis models.
    """
    if not is_correlated and spatial_distance_m is None:
        return 0.0

    if spatial_distance_m is not None:
        distance_m = spatial_distance_m
    elif dist_to_box_meters is not None:
        distance_m = dist_to_box_meters

    if spatial_cep_m is not None:
        tolerance_m = max(30.0, spatial_cep_m * 2.5)
    elif tolerance_meters is not None:
        tolerance_m = tolerance_meters

    if is_inside_box is not None:
        match_type = "inside_box" if is_inside_box else "outside_box"

    if speed_discrepancy_knots is not None:
        speed_diff_knots = speed_discrepancy_knots
    elif speed_diff_knots is None and sar_speed_knots is not None and ais_speed_knots is not None:
        try:
            speed_diff_knots = abs(float(sar_speed_knots) - float(ais_speed_knots))
        except (TypeError, ValueError):
            speed_diff_knots = None

    if course_discrepancy_deg is not None:
        heading_diff_deg = course_discrepancy_deg
    elif heading_diff_deg is None and sar_heading_deg is not None and ais_heading_deg is not None:
        try:
            h_diff = abs(float(sar_heading_deg) - float(ais_heading_deg)) % 360.0
            heading_diff_deg = min(h_diff, 360.0 - h_diff)
        except (TypeError, ValueError):
            heading_diff_deg = None

    if length_discrepancy_m is not None:
        radar_length_m = 50.0 + length_discrepancy_m
        ais_length_m = 50.0
    elif radar_length_m is None and sar_length_m is not None:
        radar_length_m = sar_length_m

    tol = max(20.0, float(tolerance_m))
    dist = max(0.0, float(distance_m))

    # Base prior by match category
    prior = 0.98 if match_type == "inside_box" else 0.75

    # 1. Spatial decay factor: Gaussian kernel over distance relative to tolerance
    spatial_sigma = max(35.0, tol * 0.45)
    f_spatial = math.exp(-0.5 * ((dist / spatial_sigma) ** 2))

    # 2. Kinematic velocity agreement factor
    f_speed = 1.0
    if speed_diff_knots is not None:
        spd_diff = abs(float(speed_diff_knots))
        f_speed = math.exp(-0.5 * ((min(spd_diff, 20.0) / 4.0) ** 2))

    # 3. Kinematic heading agreement factor
    f_heading = 1.0
    if heading_diff_deg is not None:
        hdg_diff = abs(float(heading_diff_deg)) % 360.0
        acute_diff = min(hdg_diff, 360.0 - hdg_diff)
        f_heading = math.exp(-0.5 * ((min(acute_diff, 90.0) / 25.0) ** 2))

    # 4. Dimensional consistency factor
    f_dim = 1.0
    if radar_length_m is not None and ais_length_m is not None:
        try:
            r_l = float(radar_length_m)
            a_l = float(ais_length_m)
            if r_l > 0 and a_l > 0:
                diff_l = abs(r_l - a_l)
                dim_sigma = max(12.0, 0.25 * a_l)
                f_dim = math.exp(-0.5 * ((diff_l / dim_sigma) ** 2))
        except (TypeError, ValueError):
            pass

    # Weighted multiplicative model
    score = prior * (f_spatial ** 0.45) * (f_speed ** 0.25) * (f_heading ** 0.20) * (f_dim ** 0.10)
    return round(max(0.10, min(0.99, score)), 3)


def generate_contact_reason_codes(
    detection_data: dict[str, Any] | Any = None,
    is_correlated: bool = False,
    is_dark: bool = False,
    is_solas: bool = False,
    is_spoofed: bool = False,
    match_type: str = "uncorrelated",
    *,
    is_inside_box: bool | None = None,
    association_likelihood: float | None = None,
    wake_detected: bool | None = None,
    has_wake: bool | None = None,
    is_dark_suspect: bool | None = None,
    solas_carriage_expected: bool | None = None,
    confidence: float | None = None,
    radar_snr_db: float | None = None,
    aspect_ratio: float | None = None,
    dimension_match: bool | None = None,
    optical_status: str | None = None,
    temporal_status: str | None = None,
    is_speed_spoofed: bool | None = None,
    is_course_spoofed: bool | None = None,
    **kwargs: Any,
) -> list[str]:
    """Generate standardized tactical surveillance classification codes for a contact."""
    codes: list[str] = []

    if is_inside_box is not None:
        match_type = "inside_box" if is_inside_box else ("outside_box" if is_correlated else "uncorrelated")
    if solas_carriage_expected is not None:
        is_solas = solas_carriage_expected

    def get_val(key: str, default: Any = None) -> Any:
        if detection_data is None:
            return default
        if isinstance(detection_data, dict):
            return detection_data.get(key, default)
        return getattr(detection_data, key, default)

    if radar_snr_db is not None:
        if radar_snr_db >= 14.0:
            codes.append(REASON_RADAR_STRONG_REFLECTOR)
        elif radar_snr_db < 8.0:
            codes.append(REASON_RADAR_WEAK_SIGNAL)

    conf = confidence if confidence is not None else get_val("confidence")
    if conf is not None and radar_snr_db is None:
        try:
            conf_num = float(conf)
            if conf_num >= 0.65:
                codes.append(REASON_RADAR_STRONG_REFLECTOR)
            elif conf_num < 0.45:
                codes.append(REASON_RADAR_WEAK_SIGNAL)
        except (TypeError, ValueError):
            pass

    if aspect_ratio is not None:
        if float(aspect_ratio) >= 3.0:
            codes.append(REASON_RADAR_HIGH_ASPECT)
    else:
        length = get_val("length")
        beam = get_val("beam")
        if length and beam:
            try:
                if float(length) / max(1.0, float(beam)) >= 3.0:
                    codes.append(REASON_RADAR_HIGH_ASPECT)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    wake = has_wake if has_wake is not None else (wake_detected if wake_detected is not None else get_val("wake_detected"))
    if wake:
        codes.append(REASON_WAKE_CONFIRMED)

    # AIS correlation
    if is_correlated:
        if match_type == "inside_box":
            codes.append(REASON_AIS_KINEMATIC_MATCH)
        else:
            codes.append(REASON_AIS_BUFFER_ASSOCIATION)
    else:
        codes.append(REASON_DARK_VESSEL_SUSPECT)
        length = get_val("length")
        if is_solas or (length is not None and float(length) >= 45.0):
            codes.append(REASON_SOLAS_TRANSPONDER_OFF)
        codes.append(REASON_NO_AIS_BROADCAST)
    if is_dark_suspect and REASON_DARK_VESSEL_SUSPECT not in codes:
        codes.append(REASON_DARK_VESSEL_SUSPECT)

    # Spoofing
    spd_spf = is_speed_spoofed if is_speed_spoofed is not None else get_val("is_speed_spoofed")
    crs_spf = is_course_spoofed if is_course_spoofed is not None else get_val("is_course_spoofed")
    if spd_spf:
        codes.append(REASON_SPEED_SPOOFING_DETECTED)
    if crs_spf:
        codes.append(REASON_COURSE_SPOOFING_DETECTED)

    # Dimension comparison when correlated
    if dimension_match is True:
        codes.append(REASON_DIMENSION_CONSISTENT)
    elif dimension_match is False:
        codes.append(REASON_DIMENSION_MISMATCH)
    else:
        length = get_val("length")
        ais_data = get_val("correlated_ais")
        if is_correlated and isinstance(ais_data, dict) and length:
            ais_l = ais_data.get("length")
            if ais_l is not None:
                try:
                    diff = abs(float(length) - float(ais_l))
                    if diff <= max(15.0, 0.25 * float(ais_l)):
                        codes.append(REASON_DIMENSION_CONSISTENT)
                    elif diff > max(30.0, 0.40 * float(ais_l)):
                        codes.append(REASON_DIMENSION_MISMATCH)
                except (TypeError, ValueError):
                    pass

    # Optical & Temporal
    opt_status = optical_status or get_val("optical_status")
    if opt_status == "CONFIRMED_VESSEL":
        codes.append(REASON_OPTICAL_CONFIRMED)
    elif opt_status == "LAND_FALSE_ALARM":
        codes.append(REASON_OPTICAL_FALSE_ALARM)

    temp_status = temporal_status or get_val("temporal_change_type")
    if temp_status == "PERSISTENT":
        codes.append(REASON_PERSISTENT_STRUCTURE)

    # Return deduplicated codes preserving order
    seen: set[str] = set()
    result: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def calibrate_contact_uncertainty(
    detection: Any,
    is_correlated: bool = False,
    match_type: str = "uncorrelated",
    distance_m: float = 0.0,
    tolerance_m: float = 250.0,
    ais_data: dict[str, Any] | None = None,
    pixel_spacing_m: float = 10.0,
    is_dark: bool = False,
    is_solas: bool = False,
    is_spoofed: bool = False,
) -> dict[str, Any]:
    """Convenience pipeline function to compute all four uncertainty dimensions for a contact."""
    def get_attr(key: str, default: Any = None) -> Any:
        if isinstance(detection, dict):
            return detection.get(key, default)
        return getattr(detection, key, default)

    l_m = get_attr("length")
    b_m = get_attr("beam")
    ang = get_attr("angle")

    ais_ellipse = None
    if ais_data and isinstance(ais_data, dict):
        ais_ellipse = ais_data.get("uncertainty_ellipse")

    spatial_unc = calibrate_spatial_uncertainty(
        pixel_spacing_m=pixel_spacing_m,
        length_m=l_m,
        beam_m=b_m,
        ais_ellipse=ais_ellipse,
    )

    dim_unc = calibrate_dimension_uncertainty(
        length_m=l_m,
        beam_m=b_m,
        pixel_spacing_m=pixel_spacing_m,
        angle_deg=ang,
    )

    spd_diff = get_attr("speed_discrepancy_knots")
    hdg_diff = get_attr("heading_discrepancy_deg")
    ais_len = ais_data.get("length") if isinstance(ais_data, dict) else None

    assoc_lik = calculate_association_likelihood(
        is_correlated=is_correlated,
        match_type=match_type,
        distance_m=distance_m,
        tolerance_m=tolerance_m,
        speed_diff_knots=spd_diff,
        heading_diff_deg=hdg_diff,
        radar_length_m=l_m,
        ais_length_m=ais_len,
    )

    reason_codes = generate_contact_reason_codes(
        detection_data=detection,
        is_correlated=is_correlated,
        is_dark=is_dark,
        is_solas=is_solas,
        is_spoofed=is_spoofed,
        match_type=match_type,
    )

    return {
        "spatial_uncertainty": spatial_unc,
        "dimension_uncertainty": dim_unc,
        "association_likelihood": assoc_lik,
        "reason_codes": reason_codes,
    }
