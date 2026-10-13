"""Use case to generate best upcoming acquisition opportunity recommendations."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.use_cases.get_aoi_revisit_calendar import GetAoiRevisitCalendar
from sentinel_analysis.domain.acquisition_suitability import OpportunityTier


class RecommendAcquisitionOpportunities:
    """Ranks upcoming satellite passes and recommends optimal primary, backup, and contingency opportunities."""

    def __init__(
        self,
        revisit_calendar: GetAoiRevisitCalendar,
        aoi_repository: AreaOfInterestRepository,
    ) -> None:
        self._calendar = revisit_calendar
        self._aoi_repository = aoi_repository

    def execute(
        self,
        aoi_id: int,
        horizon_hours: int = 72,
        target_vessel_size_m: float = 30.0,
        cost_preference: str = "BALANCED",  # "FREE_ONLY", "BALANCED", "TACTICAL_PRIORITY"
        api_key: str = "",
    ) -> dict[str, Any]:
        aoi = self._aoi_repository.get(aoi_id)
        if aoi is None:
            raise ValueError(f"Area of Interest with ID {aoi_id} not found")

        horizon_h = max(6, min(336, int(horizon_hours)))
        days_ahead = max(1, math.ceil(horizon_h / 24.0))

        # Get calendar opportunities
        calendar_res = self._calendar.execute(
            aoi_id=aoi_id,
            days_ahead=days_ahead,
            api_key=api_key,
        )

        raw_events = calendar_res.get("events", [])
        # Filter within horizon_h
        horizon_seconds = horizon_h * 3600
        eligible_events = [
            e for e in raw_events
            if 0 < e.get("seconds_until_pass", 0) <= horizon_seconds
        ]

        if not eligible_events:
            return {
                "aoi_id": aoi.id,
                "aoi_name": aoi.name,
                "bbox": aoi.bbox.as_list(),
                "horizon_hours": horizon_h,
                "target_vessel_size_m": target_vessel_size_m,
                "cost_preference": cost_preference,
                "primary_opportunity": None,
                "backup_opportunity": None,
                "contingency_opportunity": None,
                "tactical_briefing": f"No satellite passes predicted for '{aoi.name}' within the next {horizon_h} hours.",
                "ranked_opportunities": [],
            }

        # Apply scoring adjustments based on cost preference and target vessel size
        scored_candidates: list[dict[str, Any]] = []
        for e in eligible_events:
            score = float(e.get("suitability_score", 50.0))
            is_comm = e.get("is_commercial", False)
            res_m = float(e.get("resolution_m", 10.0))

            # Operational preference penalties/bonuses
            if cost_preference == "FREE_ONLY" and is_comm:
                score -= 30.0
            elif cost_preference == "TACTICAL_PRIORITY" and res_m <= 1.0:
                score += 8.0  # Boost sub-meter commercial tactical capabilities

            if target_vessel_size_m < 20.0 and res_m > 10.0:
                score -= 15.0  # Penalty for coarse resolution on small skiff targets

            candidate = dict(e)
            candidate["operational_score"] = round(max(0.0, min(100.0, score)), 1)
            scored_candidates.append(candidate)

        # Sort by operational score descending, then seconds until pass ascending
        scored_candidates.sort(
            key=lambda c: (c["operational_score"], -c["seconds_until_pass"]),
            reverse=True,
        )

        primary = scored_candidates[0]
        primary["recommendation_tier"] = OpportunityTier.PRIMARY_RECOMMENDED.value

        # Select backup (secondary pass, ideally different time or pass)
        backup: Optional[dict[str, Any]] = None
        for c in scored_candidates[1:]:
            if c["event_id"] != primary["event_id"]:
                backup = dict(c)
                backup["recommendation_tier"] = OpportunityTier.BACKUP_OPPORTUNITY.value
                break

        # Select contingency (different modality or tactical alternative)
        contingency: Optional[dict[str, Any]] = None
        for c in scored_candidates:
            if c["event_id"] not in (primary["event_id"], backup.get("event_id") if backup else None):
                # Prefer different modality or commercial vs free
                if c.get("modality") != primary.get("modality") or c.get("is_commercial") != primary.get("is_commercial"):
                    contingency = dict(c)
                    contingency["recommendation_tier"] = OpportunityTier.CONTINGENCY_OPPORTUNITY.value
                    break

        if contingency is None and len(scored_candidates) > 2:
            contingency = dict(scored_candidates[2])
            contingency["recommendation_tier"] = OpportunityTier.CONTINGENCY_OPPORTUNITY.value

        # Build tactical briefing
        briefing = self._build_tactical_briefing(
            aoi_name=aoi.name,
            horizon_h=horizon_h,
            primary=primary,
            backup=backup,
            contingency=contingency,
            target_vessel_size_m=target_vessel_size_m,
            cost_preference=cost_preference,
        )

        return {
            "aoi_id": aoi.id,
            "aoi_name": aoi.name,
            "bbox": aoi.bbox.as_list(),
            "horizon_hours": horizon_h,
            "target_vessel_size_m": target_vessel_size_m,
            "cost_preference": cost_preference,
            "primary_opportunity": primary,
            "backup_opportunity": backup,
            "contingency_opportunity": contingency,
            "tactical_briefing": briefing,
            "ranked_opportunities": scored_candidates,
        }

    def _build_tactical_briefing(
        self,
        aoi_name: str,
        horizon_h: int,
        primary: dict[str, Any],
        backup: Optional[dict[str, Any]],
        contingency: Optional[dict[str, Any]],
        target_vessel_size_m: float,
        cost_preference: str,
    ) -> str:
        p_hrs = primary["seconds_until_pass"] / 3600.0
        lines = [
            f"### Tactical Acquisition Briefing: {aoi_name}",
            f"**Evaluation Horizon:** Next {horizon_h} hours | **Target Size:** {target_vessel_size_m:.0f}m | **Strategy:** {cost_preference}",
            "",
            f"**PRIMARY RECOMMENDATION: {primary['provider_name']} ({primary.get('satellite', 'Spacecraft')})**",
            f"- **Flypast Timing:** in {p_hrs:.1f} hours ({primary['pass_time']} UTC)",
            f"- **Suitability Score:** {primary['suitability_score']:.1f}/100 ({primary['suitability_grade']})",
            f"- **Sensor Parameters:** {primary['modality']} | Resolution: {primary['resolution_m']}m | Orbit: {primary['orbit_direction']} | Swath: {primary['swath_width_km']:.0f}km",
            f"- **Tactical Rationale:** {primary['tactical_summary']}",
        ]

        if backup:
            b_hrs = backup["seconds_until_pass"] / 3600.0
            lines.extend([
                "",
                f"**BACKUP OPPORTUNITY: {backup['provider_name']} ({backup.get('satellite', 'Spacecraft')})**",
                f"- **Flypast Timing:** in {b_hrs:.1f} hours ({backup['pass_time']} UTC)",
                f"- **Suitability Score:** {backup['suitability_score']:.1f}/100 ({backup['suitability_grade']})",
                f"- **Sensor Parameters:** {backup['modality']} | Resolution: {backup['resolution_m']}m | Cost: {backup['cost_tier']}",
            ])

        if contingency:
            c_hrs = contingency["seconds_until_pass"] / 3600.0
            lines.extend([
                "",
                f"**CONTINGENCY OPTION: {contingency['provider_name']}**",
                f"- **Flypast Timing:** in {c_hrs:.1f} hours ({contingency['pass_time']} UTC)",
                f"- **Suitability Score:** {contingency['suitability_score']:.1f}/100 ({contingency['suitability_grade']})",
                f"- **Modality / Role:** {contingency['modality']} ({contingency['cost_tier']})",
            ])

        return "\n".join(lines)
