"""Domain models, physical scoring formulas, and suitability assessment for satellite acquisitions.

Evaluates:
1. Spatial resolution suitability for maritime vessel detection.
2. Swath coverage percentage over the Area of Interest (AOI).
3. Radar/optical incidence angle geometry (clutter vs corner reflection).
4. Orbit direction (ascending vs descending) and track geometry.
5. Metocean conditions: sea surface wind speed and wave height (Hs).
6. Optical cloud cover probability and solar daylight constraints.
7. Composite scoring and factor decomposition for tactical dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from sentinel_analysis.domain.entities import BoundingBox


class ModalityType(str, Enum):
    """Sensor acquisition modality."""

    SAR = "SAR"
    OPTICAL = "OPTICAL"
    HYPERSPECTRAL = "HYPERSPECTRAL"
    RF = "RF"


class OpportunityTier(str, Enum):
    """Operational recommendation tier for an acquisition opportunity."""

    PRIMARY_RECOMMENDED = "PRIMARY_RECOMMENDED"
    BACKUP_OPPORTUNITY = "BACKUP_OPPORTUNITY"
    CONTINGENCY_OPPORTUNITY = "CONTINGENCY_OPPORTUNITY"
    SUB_OPTIMAL = "SUB_OPTIMAL"


@dataclass(frozen=True)
class MetoceanCondition:
    """Atmospheric and marine sea-state conditions over the AOI at acquisition time."""

    wind_speed_mps: float = 6.0  # meters per second
    wind_direction_deg: float = 90.0  # degrees True
    wave_height_m: float = 1.2  # significant wave height Hs (meters)
    wave_period_s: float = 7.5  # peak wave period (seconds)
    cloud_cover_pct: float = 20.0  # total cloud cover percentage (0-100)
    sea_surface_temp_c: float = 28.0  # sea surface temperature in Celsius

    @property
    def wind_speed_knots(self) -> float:
        return self.wind_speed_mps * 1.94384

    def to_dict(self) -> dict[str, Any]:
        return {
            "wind_speed_mps": round(self.wind_speed_mps, 1),
            "wind_speed_knots": round(self.wind_speed_knots, 1),
            "wind_direction_deg": round(self.wind_direction_deg, 1),
            "wave_height_m": round(self.wave_height_m, 2),
            "wave_period_s": round(self.wave_period_s, 1),
            "cloud_cover_pct": round(self.cloud_cover_pct, 1),
            "sea_surface_temp_c": round(self.sea_surface_temp_c, 1),
        }


@dataclass(frozen=True)
class SuitabilityFactors:
    """Decomposed suitability factor scores (each normalized 0.0 to 100.0)."""

    resolution_score: float
    coverage_score: float
    incidence_angle_score: float
    orbit_direction_score: float
    wind_score: float
    wave_score: float
    cloud_score: float
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "resolution": 0.20,
            "coverage": 0.20,
            "incidence_angle": 0.15,
            "orbit_direction": 0.10,
            "wind": 0.12,
            "wave": 0.08,
            "cloud": 0.15,
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution": round(self.resolution_score, 1),
            "coverage": round(self.coverage_score, 1),
            "incidence_angle": round(self.incidence_angle_score, 1),
            "orbit_direction": round(self.orbit_direction_score, 1),
            "wind": round(self.wind_score, 1),
            "wave": round(self.wave_score, 1),
            "cloud": round(self.cloud_score, 1),
            "weights": self.weights,
        }


@dataclass(frozen=True)
class SuitabilityAssessment:
    """Complete decomposed suitability assessment and tactical recommendation."""

    composite_score: float  # 0.0 to 100.0
    grade: str  # OPTIMAL, EXCELLENT, ACCEPTABLE, MARGINAL, POOR
    factors: SuitabilityFactors
    strengths: list[str]
    limitations: list[str]
    tactical_summary: str

    def to_dict(self) -> dict[str, Any]:
        factors_dict = self.factors.to_dict()
        return {
            "composite_score": round(self.composite_score, 1),
            "grade": self.grade,
            "factors": factors_dict,
            "factor_scores": factors_dict,
            "strengths": self.strengths,
            "limitations": self.limitations,
            "tactical_summary": self.tactical_summary,
        }


# =============================================================================
# SCORING ALGORITHMS
# =============================================================================

def calculate_resolution_score(resolution_m: float, modality: ModalityType) -> float:
    """Calculate resolution suitability score based on ground sampling distance.

    Sub-meter (< 1m, e.g. Umbra spotlight) gives maximum target identification (100).
    5m - 10m (Sentinel-1 IW, Sentinel-2) provides high detection capability (80-90).
    20m - 40m (Sentinel-1 EW) resolves larger vessels only (50-65).
    > 50m degrades significantly.
    """
    res = max(0.1, float(resolution_m))
    if res <= 0.5:
        return 100.0
    elif res <= 1.0:
        return 95.0
    elif res <= 3.0:
        return 90.0
    elif res <= 10.0:
        # 1.0m -> 95, 10.0m -> 80
        return 95.0 - (res - 1.0) / 9.0 * 15.0
    elif res <= 25.0:
        # 10.0m -> 80, 25.0m -> 60
        return 80.0 - (res - 10.0) / 15.0 * 20.0
    elif res <= 50.0:
        # 25.0m -> 60, 50.0m -> 40
        return 60.0 - (res - 25.0) / 25.0 * 20.0
    else:
        return max(15.0, 40.0 - (res - 50.0) / 50.0 * 25.0)


def calculate_coverage_score(aoi_coverage_pct: float) -> float:
    """Calculate coverage suitability based on percentage of AOI enclosed in swath footprint."""
    pct = max(0.0, min(100.0, float(aoi_coverage_pct)))
    if pct >= 95.0:
        return 100.0
    elif pct >= 75.0:
        # 75% -> 85, 95% -> 100
        return 85.0 + (pct - 75.0) / 20.0 * 15.0
    elif pct >= 50.0:
        # 50% -> 65, 75% -> 85
        return 65.0 + (pct - 50.0) / 25.0 * 20.0
    elif pct >= 25.0:
        # 25% -> 40, 50% -> 65
        return 40.0 + (pct - 25.0) / 25.0 * 25.0
    else:
        # 0% -> 10, 25% -> 40
        return 10.0 + (pct / 25.0) * 30.0


def calculate_incidence_angle_score(incidence_angle_deg: float, modality: ModalityType) -> float:
    """Calculate incidence angle suitability.

    For Maritime SAR:
    - Optimal: 30 to 42 deg (high corner reflection from vessel hull/superstructure,
      while sea surface reflects specularly away from radar, maximizing target-to-clutter ratio).
    - Steep (< 25 deg): high ocean clutter floor reduces vessel contrast.
    - Shallow (> 46 deg): lower radar SNR and atmospheric attenuation.

    For Optical:
    - Near-nadir (< 15 deg) is optimal.
    - Oblique (> 30 deg) causes perspective distortion and longer atmospheric path.
    """
    angle = max(0.0, min(85.0, float(incidence_angle_deg)))

    if modality == ModalityType.SAR:
        if 31.0 <= angle <= 41.0:
            return 100.0
        elif 27.0 <= angle < 31.0:
            # 27 deg -> 85, 31 deg -> 100
            return 85.0 + (angle - 27.0) / 4.0 * 15.0
        elif 41.0 < angle <= 45.0:
            # 41 deg -> 100, 45 deg -> 85
            return 100.0 - (angle - 41.0) / 4.0 * 15.0
        elif 20.0 <= angle < 27.0:
            # Steep incidence: 20 deg -> 55, 27 deg -> 85
            return 55.0 + (angle - 20.0) / 7.0 * 30.0
        elif 45.0 < angle <= 52.0:
            # Shallow incidence: 45 deg -> 85, 52 deg -> 50
            return 85.0 - (angle - 45.0) / 7.0 * 35.0
        elif angle < 20.0:
            return max(20.0, 55.0 - (20.0 - angle) * 3.0)
        else:
            return max(15.0, 50.0 - (angle - 52.0) * 3.0)
    else:
        # Optical sensor
        if angle <= 15.0:
            return 100.0
        elif angle <= 25.0:
            return 90.0 - (angle - 15.0) * 1.5
        elif angle <= 35.0:
            return 75.0 - (angle - 25.0) * 2.5
        else:
            return max(10.0, 50.0 - (angle - 35.0) * 3.0)


def calculate_orbit_direction_score(
    orbit_direction: str,
    aoi_bbox: Optional[BoundingBox] = None,
) -> float:
    """Calculate orbit direction suitability.

    Both Ascending (dusk/night or late afternoon) and Descending (dawn/morning) are viable.
    If AOI has predominantly East-West or North-South coastal topography, ascending/descending
    pass orientation provides distinct look geometries. Default high baseline (90-95).
    """
    direction = (orbit_direction or "DESCENDING").upper().strip()
    if direction in ("ASCENDING", "DESCENDING"):
        return 95.0
    return 80.0


def calculate_wind_score(wind_speed_mps: float, modality: ModalityType) -> float:
    """Calculate wind suitability score for maritime target detection.

    For SAR:
    - Ideal: 3.0 to 8.5 m/s (6-16 knots). Provides regular capillary wave pattern with
      predictable K-distribution or Weibull clutter model. Vessels stand out cleanly.
    - Dead calm (< 2.0 m/s): Surface is mirror-like, but natural biogenic slicks / surfactant
      films cause dark radar lookalikes and contrast ambiguities (Score 70-75).
    - High wind (11.0 to 16.0 m/s / Beaufort 6-7): Whitecaps, sea spray, and breaking waves
      elevate radar cross-section of sea clutter, drowning small skiffs (Score 40-60).
    - Gale / Storm (> 17.0 m/s): Severe clutter saturation (Score < 30).

    For Optical:
    - Moderate winds have minimal direct effect; extreme wind causes whitecaps/sun glitter.
    """
    w = max(0.0, float(wind_speed_mps))

    if modality == ModalityType.SAR:
        if 3.5 <= w <= 8.5:
            return 100.0
        elif 2.0 <= w < 3.5:
            # 2.0 -> 78, 3.5 -> 100
            return 78.0 + (w - 2.0) / 1.5 * 22.0
        elif w < 2.0:
            # Dead calm slick risk
            return max(50.0, 60.0 + w * 9.0)
        elif 8.5 < w <= 13.0:
            # 8.5 -> 100, 13.0 -> 65
            return 100.0 - (w - 8.5) / 4.5 * 35.0
        elif 13.0 < w <= 18.0:
            # 13.0 -> 65, 18.0 -> 35
            return 65.0 - (w - 13.0) / 5.0 * 30.0
        else:
            return max(10.0, 35.0 - (w - 18.0) * 3.0)
    else:
        # Optical
        if w <= 10.0:
            return 95.0
        elif w <= 15.0:
            return 85.0
        else:
            return max(40.0, 85.0 - (w - 15.0) * 4.0)


def calculate_wave_score(wave_height_m: float, modality: ModalityType) -> float:
    """Calculate wave height (Hs) suitability score.

    High significant wave height elevates SAR clutter baseline and creates shadow masking
    in wave troughs for small vessels.
    - Calm / Slight (Hs <= 1.2 m): 100.0
    - Moderate (1.2 < Hs <= 2.2 m): 85-95
    - Rough (2.2 < Hs <= 3.5 m): 60-80
    - Very Rough (3.5 < Hs <= 5.0 m): 35-55
    - High / Phenomenal (Hs > 5.0 m): < 25
    """
    hs = max(0.0, float(wave_height_m))

    if hs <= 1.2:
        return 100.0
    elif hs <= 2.2:
        return 100.0 - (hs - 1.2) / 1.0 * 15.0
    elif hs <= 3.5:
        return 85.0 - (hs - 2.2) / 1.3 * 25.0
    elif hs <= 5.0:
        return 60.0 - (hs - 3.5) / 1.5 * 30.0
    else:
        return max(10.0, 30.0 - (hs - 5.0) * 5.0)


def calculate_cloud_score(
    cloud_cover_pct: float,
    modality: ModalityType,
    is_daylight: bool = True,
) -> float:
    """Calculate optical cloud cover & illumination score.

    For SAR:
    - Cloud cover has ZERO negative impact (100.0). Radar microwaves (C-band, X-band)
      penetrate clouds, haze, smoke, rain, and operate 24/7 day and night.

    For Optical:
    - Highly cloud sensitive.
    - 0-10% clouds: 95-100
    - 10-30% clouds: 70-85
    - 30-60% clouds: 35-65
    - > 60% clouds: 5-25 (obscures ocean surface)
    - If night (not daylight): Optical score is 0.0.
    """
    if modality == ModalityType.SAR:
        # All-weather radar penetration
        return 100.0

    # Optical sensor
    if not is_daylight:
        return 0.0  # Optical requires sunlight

    clouds = max(0.0, min(100.0, float(cloud_cover_pct)))
    if clouds <= 10.0:
        return 100.0 - clouds * 0.5
    elif clouds <= 30.0:
        # 10% -> 95, 30% -> 75
        return 95.0 - (clouds - 10.0) / 20.0 * 20.0
    elif clouds <= 60.0:
        # 30% -> 75, 60% -> 35
        return 75.0 - (clouds - 30.0) / 30.0 * 40.0
    else:
        # 60% -> 35, 100% -> 5
        return max(5.0, 35.0 - (clouds - 60.0) / 40.0 * 30.0)


def score_acquisition_opportunity(
    resolution_m: float,
    aoi_coverage_pct: float,
    incidence_angle_deg: float,
    orbit_direction: str,
    modality: ModalityType,
    metocean: MetoceanCondition,
    is_daylight: bool = True,
    aoi_bbox: Optional[BoundingBox] = None,
) -> SuitabilityAssessment:
    """Compute the multi-factor acquisition suitability score and generate tactical assessment."""
    res_score = calculate_resolution_score(resolution_m, modality)
    cov_score = calculate_coverage_score(aoi_coverage_pct)
    inc_score = calculate_incidence_angle_score(incidence_angle_deg, modality)
    orb_score = calculate_orbit_direction_score(orbit_direction, aoi_bbox)
    wnd_score = calculate_wind_score(metocean.wind_speed_mps, modality)
    wav_score = calculate_wave_score(metocean.wave_height_m, modality)
    cld_score = calculate_cloud_score(metocean.cloud_cover_pct, modality, is_daylight)

    # Weights adapted based on modality
    if modality == ModalityType.SAR:
        weights = {
            "resolution": 0.22,
            "coverage": 0.22,
            "incidence_angle": 0.18,
            "orbit_direction": 0.08,
            "wind": 0.16,
            "wave": 0.14,
            "cloud": 0.00,  # Cloud irrelevant for SAR
        }
    else:
        # Optical: cloud and daylight dominate feasibility
        weights = {
            "resolution": 0.18,
            "coverage": 0.18,
            "incidence_angle": 0.08,
            "orbit_direction": 0.06,
            "wind": 0.08,
            "wave": 0.07,
            "cloud": 0.35,  # Dominant factor for optical
        }

    factors = SuitabilityFactors(
        resolution_score=res_score,
        coverage_score=cov_score,
        incidence_angle_score=inc_score,
        orbit_direction_score=orb_score,
        wind_score=wnd_score,
        wave_score=wav_score,
        cloud_score=cld_score,
        weights=weights,
    )

    # Calculate weighted composite
    composite = (
        res_score * weights["resolution"]
        + cov_score * weights["coverage"]
        + inc_score * weights["incidence_angle"]
        + orb_score * weights["orbit_direction"]
        + wnd_score * weights["wind"]
        + wav_score * weights["wave"]
        + cld_score * weights["cloud"]
    )
    composite = max(0.0, min(100.0, composite))

    # Optical constraints: Nighttime or severe cloud obscuration
    if modality == ModalityType.OPTICAL:
        if not is_daylight:
            composite = 0.0
        elif metocean.cloud_cover_pct > 30.0:
            obscuration_dampening = max(0.05, 1.0 - (metocean.cloud_cover_pct - 30.0) / 70.0 * 0.75)
            composite *= obscuration_dampening

    # Determine Grade
    if composite >= 88.0:
        grade = "OPTIMAL"
    elif composite >= 76.0:
        grade = "EXCELLENT"
    elif composite >= 62.0:
        grade = "ACCEPTABLE"
    elif composite >= 45.0:
        grade = "MARGINAL"
    else:
        grade = "POOR"

    # Analyze strengths & limitations
    strengths: list[str] = []
    limitations: list[str] = []

    if modality == ModalityType.SAR:
        strengths.append("All-weather cloud penetration guarantees observation through overcast skies")
        if 31.0 <= incidence_angle_deg <= 41.0:
            strengths.append(f"Optimal radar incidence angle ({incidence_angle_deg:.1f}°) maximizes vessel hull corner reflection")
        if 3.0 <= metocean.wind_speed_mps <= 8.5:
            strengths.append(f"Moderate wind ({metocean.wind_speed_knots:.1f} kts) provides uniform sea clutter background")
    else:
        if is_daylight and metocean.cloud_cover_pct <= 15.0:
            strengths.append(f"Clear optical conditions ({metocean.cloud_cover_pct:.0f}% clouds) enable direct visual & multispectral identification")
        elif not is_daylight:
            limitations.append("Optical observation impossible during nighttime pass (solar elevation <= 0°)")
        elif metocean.cloud_cover_pct > 50.0:
            limitations.append(f"Heavy optical cloud cover ({metocean.cloud_cover_pct:.0f}%) will obscure maritime targets")

    if aoi_coverage_pct >= 90.0:
        strengths.append(f"Full AOI spatial coverage ({aoi_coverage_pct:.0f}% of bounding box enclosed)")
    elif aoi_coverage_pct < 60.0:
        limitations.append(f"Partial AOI coverage ({aoi_coverage_pct:.0f}%); edge targets may fall outside swath")

    if resolution_m <= 1.0:
        strengths.append(f"Sub-meter resolution ({resolution_m:.2f}m) allows fine dark vessel structural classification")
    elif resolution_m >= 25.0:
        limitations.append(f"Coarse resolution ({resolution_m:.0f}m) limits detection to vessels > 60m in length")

    if metocean.wave_height_m > 3.0:
        limitations.append(f"High sea state (Hs={metocean.wave_height_m:.1f}m) may produce false alarms or wave trough shadowing")
    if metocean.wind_speed_mps > 14.0:
        limitations.append(f"High surface wind ({metocean.wind_speed_knots:.1f} kts) elevates sea clutter floor")

    # Tactical Summary synthesis
    summary = (
        f"{grade} suitability ({composite:.1f}/100) for {modality.value} maritime surveillance. "
        f"Resolution: {resolution_m:.1f}m, AOI Coverage: {aoi_coverage_pct:.0f}%, "
        f"Incidence: {incidence_angle_deg:.1f}°, Sea State: Hs={metocean.wave_height_m:.1f}m, "
        f"Wind: {metocean.wind_speed_knots:.1f} kts, Clouds: {metocean.cloud_cover_pct:.0f}%."
    )

    return SuitabilityAssessment(
        composite_score=composite,
        grade=grade,
        factors=factors,
        strengths=strengths,
        limitations=limitations,
        tactical_summary=summary,
    )
