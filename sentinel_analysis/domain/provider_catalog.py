"""Multi-satellite provider catalog and comparative intelligence engine.

Supports side-by-side trade-off analysis across:
1. Sentinel-1 (ESA / Copernicus C-band SAR)
2. Sentinel-2 (ESA / Copernicus Multispectral Optical)
3. Umbra (Commercial High-Resolution X-band SAR)
4. Capella Space (Commercial Sub-meter X-band SAR)
5. ICEYE (Commercial Rapid-Revisit X-band SAR)
6. PlanetScope (Commercial High-Cadence Optical)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sentinel_analysis.domain.acquisition_suitability import (
    MetoceanCondition,
    ModalityType,
    score_acquisition_opportunity,
)
from sentinel_analysis.domain.entities import BoundingBox


@dataclass(frozen=True)
class ProviderProfile:
    """Technical and operational specifications of an Earth Observation satellite provider."""

    provider_id: str
    name: str
    constellation: str
    operator: str
    modality: ModalityType
    frequency_or_spectral: str
    standard_resolution_m: float
    high_resolution_m: float
    swath_width_km: float
    spotlight_swath_width_km: Optional[float]
    is_commercial: bool
    cost_tier: str  # FREE_AND_OPEN, COMMERCIAL_STANDARD, COMMERCIAL_PREMIUM
    day_night_capable: bool
    all_weather_capable: bool
    typical_latency_hours: float
    tasking_lead_time_hours: float
    revisit_rate_description: str
    best_use_case: str
    key_strengths: list[str]
    key_tradeoffs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "constellation": self.constellation,
            "operator": self.operator,
            "modality": self.modality.value,
            "frequency_or_spectral": self.frequency_or_spectral,
            "standard_resolution_m": self.standard_resolution_m,
            "high_resolution_m": self.high_resolution_m,
            "swath_width_km": self.swath_width_km,
            "spotlight_swath_width_km": self.spotlight_swath_width_km,
            "is_commercial": self.is_commercial,
            "cost_tier": self.cost_tier,
            "day_night_capable": self.day_night_capable,
            "all_weather_capable": self.all_weather_capable,
            "typical_latency_hours": self.typical_latency_hours,
            "tasking_lead_time_hours": self.tasking_lead_time_hours,
            "revisit_rate_description": self.revisit_rate_description,
            "best_use_case": self.best_use_case,
            "key_strengths": self.key_strengths,
            "key_tradeoffs": self.key_tradeoffs,
        }


@dataclass(frozen=True)
class ProviderComparisonEntry:
    """Evaluated comparison row for a provider given target operational conditions."""

    provider: ProviderProfile
    estimated_suitability_score: float
    suitability_grade: str
    cloud_resilience: str
    dark_vessel_detection_fidelity: str
    coverage_feasibility: str
    tasking_flexibility: str
    estimated_cost_level: str
    dispatch_recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.to_dict(),
            "estimated_suitability_score": round(self.estimated_suitability_score, 1),
            "suitability_grade": self.suitability_grade,
            "cloud_resilience": self.cloud_resilience,
            "dark_vessel_detection_fidelity": self.dark_vessel_detection_fidelity,
            "coverage_feasibility": self.coverage_feasibility,
            "tasking_flexibility": self.tasking_flexibility,
            "estimated_cost_level": self.estimated_cost_level,
            "dispatch_recommendation": self.dispatch_recommendation,
        }


# Built-in Provider Catalog Definitions
_PROVIDERS: dict[str, ProviderProfile] = {
    "sentinel-1": ProviderProfile(
        provider_id="sentinel-1",
        name="Sentinel-1 C-SAR",
        constellation="Copernicus Sentinel-1 (S1A, S1C)",
        operator="European Space Agency (ESA) / Copernicus",
        modality=ModalityType.SAR,
        frequency_or_spectral="C-band (5.405 GHz)",
        standard_resolution_m=10.0,
        high_resolution_m=5.0,
        swath_width_km=250.0,
        spotlight_swath_width_km=None,
        is_commercial=False,
        cost_tier="FREE_AND_OPEN",
        day_night_capable=True,
        all_weather_capable=True,
        typical_latency_hours=2.5,
        tasking_lead_time_hours=0.0,  # Systematic pre-planned acquisition
        revisit_rate_description="6-12 days per satellite (3-6 days dual constellation)",
        best_use_case="Broad maritime wide-area surveillance, EEZ patrols, persistent long-term change detection.",
        key_strengths=[
            "100% Free & Open data access",
            "Huge 250km Interferometric Wide (IW) swath covers vast open ocean areas",
            "Reliable all-weather, day/night radar penetration through monsoon clouds and typhoons",
            "Dual-polarization (VV + VH) enables robust vessel/clutter separation",
        ],
        key_tradeoffs=[
            "Fixed orbital acquisition schedule (no on-demand agile spotlight tasking)",
            "10m resolution cannot classify small skiffs (< 20m) or resolve fine deck equipment",
        ],
    ),
    "sentinel-2": ProviderProfile(
        provider_id="sentinel-2",
        name="Sentinel-2 MSI Optical",
        constellation="Copernicus Sentinel-2 (S2A, S2B)",
        operator="European Space Agency (ESA) / Copernicus",
        modality=ModalityType.OPTICAL,
        frequency_or_spectral="13 Multispectral Bands (VNIR/SWIR, 443nm - 2190nm)",
        standard_resolution_m=10.0,
        high_resolution_m=10.0,
        swath_width_km=290.0,
        spotlight_swath_width_km=None,
        is_commercial=False,
        cost_tier="FREE_AND_OPEN",
        day_night_capable=False,
        all_weather_capable=False,
        typical_latency_hours=3.0,
        tasking_lead_time_hours=0.0,
        revisit_rate_description="5 days constellation revisit at equator",
        best_use_case="Visual vessel validation, ship color/wake confirmation, NDWI reef/land false alarm rejection.",
        key_strengths=[
            "100% Free & Open multispectral data",
            "Direct true-color visual interpretation (RGB) and NDWI water indices",
            "Wide 290km swath width covering whole coastal archipelagos",
            "Complementary cross-validation when paired with SAR radar detections",
        ],
        key_tradeoffs=[
            "Strictly daytime only (inoperable at night)",
            "Heavily obscured by cloud cover, haze, fog, and dense rain",
        ],
    ),
    "umbra": ProviderProfile(
        provider_id="umbra",
        name="Umbra High-Res SAR",
        constellation="Umbra Space Microsatellite SAR",
        operator="Umbra Space (USA)",
        modality=ModalityType.SAR,
        frequency_or_spectral="X-band (9.6 GHz)",
        standard_resolution_m=1.0,
        high_resolution_m=0.25,
        swath_width_km=15.0,
        spotlight_swath_width_km=5.0,
        is_commercial=True,
        cost_tier="COMMERCIAL_PREMIUM",
        day_night_capable=True,
        all_weather_capable=True,
        typical_latency_hours=1.0,
        tasking_lead_time_hours=4.0,  # Rapid agile on-demand tasking
        revisit_rate_description="High-frequency agile tasking on demand (< 4 hours window)",
        best_use_case="Tactical targeted dark vessel interdiction, naval class structural identification, sub-meter deck/crane inspection.",
        key_strengths=[
            "Extreme sub-meter spatial resolution (down to 0.25m spotlight)",
            "Can distinguish small RHIBs, speedboats, and illegal transshipment equipment (fenders/hoses)",
            "Agile on-demand commercial tasking with < 4-hour lead time",
            "All-weather day/night X-band radar penetration",
        ],
        key_tradeoffs=[
            "Narrow spotlight swath footprint (5x5km to 15x15km)",
            "Commercial tasking cost per scene",
            "Requires point target coordinates rather than whole-basin sweeping",
        ],
    ),
    "capella": ProviderProfile(
        provider_id="capella",
        name="Capella Space SAR",
        constellation="Capella Whitney Constellation",
        operator="Capella Space (USA)",
        modality=ModalityType.SAR,
        frequency_or_spectral="X-band (9.65 GHz)",
        standard_resolution_m=1.2,
        high_resolution_m=0.5,
        swath_width_km=20.0,
        spotlight_swath_width_km=5.0,
        is_commercial=True,
        cost_tier="COMMERCIAL_PREMIUM",
        day_night_capable=True,
        all_weather_capable=True,
        typical_latency_hours=1.5,
        tasking_lead_time_hours=6.0,
        revisit_rate_description="Rapid revisit (~6 hours) across middle latitudes",
        best_use_case="Targeted port infrastructure monitoring, anchor loitering, covert naval movement surveillance.",
        key_strengths=[
            "High resolution (0.5m spotlight / 1.2m stripmap)",
            "Flexible commercial API tasking with automated scheduling",
            "Day/night and all-weather operational availability",
        ],
        key_tradeoffs=[
            "Commercial per-scene imagery cost",
            "Smaller swath width relative to Sentinel-1",
        ],
    ),
    "iceye": ProviderProfile(
        provider_id="iceye",
        name="ICEYE Microsat SAR",
        constellation="ICEYE Constellation (30+ satellites)",
        operator="ICEYE (Finland)",
        modality=ModalityType.SAR,
        frequency_or_spectral="X-band (9.65 GHz)",
        standard_resolution_m=3.0,
        high_resolution_m=0.5,
        swath_width_km=30.0,
        spotlight_swath_width_km=5.0,
        is_commercial=True,
        cost_tier="COMMERCIAL_STANDARD",
        day_night_capable=True,
        all_weather_capable=True,
        typical_latency_hours=2.0,
        tasking_lead_time_hours=8.0,
        revisit_rate_description="Dense constellation provides multiple daily revisit opportunities",
        best_use_case="Persistent maritime surveillance, vessel tracking over chokepoints, high revisit rate.",
        key_strengths=[
            "Largest commercial SAR constellation (30+ active spacecraft)",
            "Frequent revisit opportunities throughout 24-hour cycle",
            "Flexible imaging modes from 0.5m Spotlight to 30km Stripmap",
        ],
        key_tradeoffs=[
            "Commercial licensing required",
            "Stripmap resolution (3m) is intermediate between S1 and Umbra",
        ],
    ),
    "planetscope": ProviderProfile(
        provider_id="planetscope",
        name="PlanetScope Dove Optical",
        constellation="PlanetScope Flock (200+ Doves)",
        operator="Planet Labs (USA)",
        modality=ModalityType.OPTICAL,
        frequency_or_spectral="8 Multispectral Bands (VNIR, 440nm - 885nm)",
        standard_resolution_m=3.7,
        high_resolution_m=3.0,
        swath_width_km=32.5,
        spotlight_swath_width_km=None,
        is_commercial=True,
        cost_tier="COMMERCIAL_STANDARD",
        day_night_capable=False,
        all_weather_capable=False,
        typical_latency_hours=8.0,
        tasking_lead_time_hours=12.0,
        revisit_rate_description="Near-daily global land and coastal coverage",
        best_use_case="High-cadence coastal tracking, harbor berth occupancy, daytime visual verification.",
        key_strengths=[
            "Daily imaging cadence across most coastal zones",
            "Higher resolution (3m) than Sentinel-2 (10m)",
            "8-band multispectral color provides rich visual details",
        ],
        key_tradeoffs=[
            "Optical only: blocked by clouds, fog, and night",
            "Commercial subscription required",
        ],
    ),
}


class ProviderCatalog:
    """Catalog repository and multi-provider decision support."""

    @classmethod
    def list_all(cls) -> list[ProviderProfile]:
        return list(_PROVIDERS.values())

    @classmethod
    def get_provider(cls, provider_id: str) -> Optional[ProviderProfile]:
        norm = (provider_id or "").strip().lower()
        return _PROVIDERS.get(norm)

    @classmethod
    def compare_providers(
        cls,
        metocean: MetoceanCondition,
        aoi_bbox: Optional[BoundingBox] = None,
        is_daylight: bool = True,
        target_vessel_size_m: float = 30.0,
    ) -> list[ProviderComparisonEntry]:
        """Perform comprehensive side-by-side trade-off comparison across all providers."""
        entries: list[ProviderComparisonEntry] = []

        for p in _PROVIDERS.values():
            # Estimate coverage for standard AOI (assume 100% for wide-swath S1/S2, 40% for narrow spotlight)
            if p.swath_width_km >= 200.0:
                est_cov = 100.0
                cov_desc = "Full AOI wide-swath coverage"
            elif p.swath_width_km >= 25.0:
                est_cov = 80.0
                cov_desc = "Broad corridor coverage (multiple scenes for large AOIs)"
            else:
                est_cov = 45.0
                cov_desc = "Spotlight footprint (pinpoint coordinate tasking required)"

            # Run suitability scoring engine
            assessment = score_acquisition_opportunity(
                resolution_m=p.high_resolution_m,
                aoi_coverage_pct=est_cov,
                incidence_angle_deg=35.0,  # nominal average
                orbit_direction="DESCENDING",
                modality=p.modality,
                metocean=metocean,
                is_daylight=is_daylight,
                aoi_bbox=aoi_bbox,
            )

            # Cloud resilience description
            if p.all_weather_capable:
                cloud_desc = "Immune to clouds, rain, and fog (All-Weather SAR)"
            else:
                if metocean.cloud_cover_pct <= 15.0:
                    cloud_desc = f"Optimal ({metocean.cloud_cover_pct:.0f}% cloud forecast)"
                elif metocean.cloud_cover_pct <= 45.0:
                    cloud_desc = f"Moderate risk ({metocean.cloud_cover_pct:.0f}% cloud forecast)"
                else:
                    cloud_desc = f"Severe obscuration ({metocean.cloud_cover_pct:.0f}% cloud forecast)"

            # Dark vessel detection fidelity
            if p.high_resolution_m <= 1.0:
                fidelity = "High: Resolves skiffs (<20m), superstructure, and transshipment rigging"
            elif p.high_resolution_m <= 5.0:
                fidelity = "Medium-High: Resolves coastal trawlers, tugs, and merchant ships"
            elif p.high_resolution_m <= 10.0:
                fidelity = "Standard: Detects vessels > 30m; small craft near clutter limit"
            else:
                fidelity = "Low: Only detects large tankers and container ships > 80m"

            # Tasking flexibility
            if not p.is_commercial:
                task_flex = "Systematic Pre-Scheduled Pass (zero tasking overhead, free)"
            elif p.tasking_lead_time_hours <= 4.0:
                task_flex = f"Agile On-Demand Tasking ({p.tasking_lead_time_hours:.0f}h lead time)"
            else:
                task_flex = f"Commercial Tasking ({p.tasking_lead_time_hours:.0f}h lead time)"

            # Recommendation synthesis
            if p.modality == ModalityType.OPTICAL and not is_daylight:
                rec = "INELIGIBLE: Inoperable during nighttime pass. Select SAR provider."
            elif p.modality == ModalityType.OPTICAL and metocean.cloud_cover_pct > 55.0:
                rec = "UNFAVORABLE: Heavy cloud cover forecast. Prefer Sentinel-1 or Umbra SAR."
            elif not p.is_commercial:
                rec = "RECOMMENDED (PRIMARY): Free wide-area systematic monitoring baseline."
            elif p.provider_id == "umbra":
                rec = "RECOMMENDED (TACTICAL): Deploy for high-priority dark target verification."
            else:
                rec = "VIABLE ALTERNATIVE: Commercial backup when revisit timing matches priority."

            entries.append(
                ProviderComparisonEntry(
                    provider=p,
                    estimated_suitability_score=assessment.composite_score,
                    suitability_grade=assessment.grade,
                    cloud_resilience=cloud_desc,
                    dark_vessel_detection_fidelity=fidelity,
                    coverage_feasibility=cov_desc,
                    tasking_flexibility=task_flex,
                    estimated_cost_level=p.cost_tier,
                    dispatch_recommendation=rec,
                )
            )

        # Sort entries by estimated suitability score descending
        entries.sort(key=lambda e: e.estimated_suitability_score, reverse=True)
        return entries
