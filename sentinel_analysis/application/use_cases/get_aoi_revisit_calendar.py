"""Use case to compute coverage and revisit calendar for Areas of Interest (AOIs)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.application.use_cases.evaluate_acquisition_suitability import (
    EvaluateAcquisitionSuitability,
)
from sentinel_analysis.domain.acquisition_suitability import ModalityType
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.domain.provider_catalog import ProviderCatalog


def _estimate_elevation_from_angle(incidence_angle_deg: float) -> float:
    return max(10.0, min(90.0, 90.0 - incidence_angle_deg))


class GetAoiRevisitCalendar:
    """Computes multi-provider coverage and revisit calendar matrices for AOIs."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        hybrid_predictor: PassPredictor,
        evaluate_suitability: EvaluateAcquisitionSuitability,
        settings_repo: Optional[Any] = None,
    ) -> None:
        self._aoi_repository = aoi_repository
        self._predictor = hybrid_predictor
        self._evaluator = evaluate_suitability
        self._settings_repo = settings_repo

    def execute(
        self,
        aoi_id: Optional[int] = None,
        days_ahead: int = 14,
        api_key: str = "",
        providers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        horizon = max(1, min(60, int(days_ahead)))
        end_time = now + timedelta(days=horizon)

        target_providers = [p.lower().strip() for p in (providers or ["sentinel-1", "sentinel-2", "umbra"])]

        all_aois = list(self._aoi_repository.list())
        if aoi_id is not None:
            active_aois = [a for a in all_aois if a.id == aoi_id]
        else:
            active_aois = all_aois

        aoi_calendars: list[dict[str, Any]] = []
        all_events: list[dict[str, Any]] = []

        for aoi in active_aois:
            aoi_events = self._generate_aoi_passes(aoi, now, horizon, api_key, target_providers)
            all_events.extend(aoi_events)

            # Compute revisit intervals for this AOI
            revisit_metrics = self._calculate_revisit_intervals(aoi_events)
            daily_matrix = self._build_daily_matrix(aoi_events, now, horizon)

            aoi_calendars.append({
                "aoi_id": aoi.id,
                "aoi_name": aoi.name,
                "bbox": aoi.bbox.as_list(),
                "total_opportunities": len(aoi_events),
                "revisit_metrics": revisit_metrics,
                "daily_matrix": daily_matrix,
                "events": aoi_events,
            })

        # Global aggregate metrics across all processed AOIs
        all_events.sort(key=lambda e: e["pass_time"])
        global_revisit = self._calculate_revisit_intervals(all_events)
        global_daily = self._build_daily_matrix(all_events, now, horizon)

        return {
            "horizon_days": horizon,
            "start_time": now.isoformat(),
            "end_time": end_time.isoformat(),
            "providers_evaluated": target_providers,
            "total_aois": len(active_aois),
            "total_events": len(all_events),
            "revisit_metrics": global_revisit,
            "daily_matrix": global_daily,
            "aoi_calendars": aoi_calendars,
            "events": all_events,
        }

    def _generate_aoi_passes(
        self,
        aoi: AreaOfInterest,
        now: datetime,
        days_ahead: int,
        api_key: str,
        providers: list[str],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        bbox = aoi.bbox
        center_lat = (bbox.min_latitude + bbox.max_latitude) / 2.0
        center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0

        # 1. Sentinel-1 passes
        if "sentinel-1" in providers:
            s1_raw_passes: list[dict[str, Any]] = []
            try:
                # Query hybrid predictor
                if hasattr(self._predictor, "predict_from_history"):
                    s1_raw_passes = self._predictor.predict_from_history(bbox, days_ahead=days_ahead, limit=30)
                elif hasattr(self._predictor, "predict"):
                    s1_raw_passes = self._predictor.predict(bbox, api_key=api_key or "")
            except Exception:
                pass

            # If no passes returned from predictor, generate synthetic systematic orbital passes for S1
            if not s1_raw_passes:
                s1_raw_passes = self._generate_synthetic_s1_passes(bbox, now, days_ahead)

            for p in s1_raw_passes:
                p_dt = self._parse_datetime(p.get("time"))
                if not p_dt or p_dt < now or p_dt > now + timedelta(days=days_ahead):
                    continue

                orbit_dir = p.get("orbit_direction") or "DESCENDING"
                elev = float(p.get("max_elevation") or 55.0)
                incidence = max(20.0, min(50.0, 90.0 - elev))
                cov_pct = 100.0  # Sentinel-1 250km IW swath fully covers standard AOI

                assessment = self._evaluator.execute(
                    bbox=bbox,
                    resolution_m=10.0,
                    aoi_coverage_pct=cov_pct,
                    incidence_angle_deg=incidence,
                    orbit_direction=orbit_dir,
                    modality=ModalityType.SAR,
                    pass_time=p_dt,
                )

                events.append({
                    "event_id": f"s1_{aoi.id}_{p_dt.strftime('%Y%m%d%H%M')}",
                    "aoi_id": aoi.id,
                    "aoi_name": aoi.name,
                    "provider_id": "sentinel-1",
                    "provider_name": "Sentinel-1 C-SAR",
                    "satellite": p.get("satellite") or "Sentinel-1A",
                    "modality": "SAR",
                    "resolution_m": 10.0,
                    "swath_width_km": 250.0,
                    "aoi_coverage_pct": cov_pct,
                    "orbit_direction": orbit_dir,
                    "relative_orbit": p.get("relative_orbit"),
                    "pass_time": p_dt.isoformat(),
                    "seconds_until_pass": int((p_dt - now).total_seconds()),
                    "max_elevation": round(elev, 1),
                    "incidence_angle_deg": round(incidence, 1),
                    "is_commercial": False,
                    "cost_tier": "FREE_AND_OPEN",
                    "suitability_score": assessment.composite_score,
                    "suitability_grade": assessment.grade,
                    "factors": assessment.factors.to_dict(),
                    "strengths": assessment.strengths,
                    "limitations": assessment.limitations,
                    "tactical_summary": assessment.tactical_summary,
                })

        # 2. Sentinel-2 Optical passes (sun-synchronous 10:30 local solar time, 5-day cycle)
        if "sentinel-2" in providers:
            s2_passes = self._generate_sentinel2_passes(bbox, now, days_ahead)
            for p in s2_passes:
                p_dt = p["time"]
                orbit_dir = "DESCENDING"
                cov_pct = 100.0  # 290km swath
                incidence = 12.0  # Nadir-pointing optical sensor

                assessment = self._evaluator.execute(
                    bbox=bbox,
                    resolution_m=10.0,
                    aoi_coverage_pct=cov_pct,
                    incidence_angle_deg=incidence,
                    orbit_direction=orbit_dir,
                    modality=ModalityType.OPTICAL,
                    pass_time=p_dt,
                    is_daylight=True,
                )

                events.append({
                    "event_id": f"s2_{aoi.id}_{p_dt.strftime('%Y%m%d%H%M')}",
                    "aoi_id": aoi.id,
                    "aoi_name": aoi.name,
                    "provider_id": "sentinel-2",
                    "provider_name": "Sentinel-2 MSI",
                    "satellite": p["satellite"],
                    "modality": "OPTICAL",
                    "resolution_m": 10.0,
                    "swath_width_km": 290.0,
                    "aoi_coverage_pct": cov_pct,
                    "orbit_direction": orbit_dir,
                    "relative_orbit": p.get("relative_orbit", 1),
                    "pass_time": p_dt.isoformat(),
                    "seconds_until_pass": int((p_dt - now).total_seconds()),
                    "max_elevation": 80.0,
                    "incidence_angle_deg": 12.0,
                    "is_commercial": False,
                    "cost_tier": "FREE_AND_OPEN",
                    "suitability_score": assessment.composite_score,
                    "suitability_grade": assessment.grade,
                    "factors": assessment.factors.to_dict(),
                    "strengths": assessment.strengths,
                    "limitations": assessment.limitations,
                    "tactical_summary": assessment.tactical_summary,
                })

        # 3. Umbra Commercial High-Res SAR passes (agile commercial windows)
        if "umbra" in providers:
            umbra_passes = self._generate_umbra_passes(bbox, now, days_ahead)
            for p in umbra_passes:
                p_dt = p["time"]
                orbit_dir = p["orbit_direction"]
                elev = p["elevation"]
                incidence = 90.0 - elev
                cov_pct = 50.0  # Spotlight targeted coverage

                assessment = self._evaluator.execute(
                    bbox=bbox,
                    resolution_m=0.25,
                    aoi_coverage_pct=cov_pct,
                    incidence_angle_deg=incidence,
                    orbit_direction=orbit_dir,
                    modality=ModalityType.SAR,
                    pass_time=p_dt,
                )

                events.append({
                    "event_id": f"umbra_{aoi.id}_{p_dt.strftime('%Y%m%d%H%M')}",
                    "aoi_id": aoi.id,
                    "aoi_name": aoi.name,
                    "provider_id": "umbra",
                    "provider_name": "Umbra High-Res SAR",
                    "satellite": p["satellite"],
                    "modality": "SAR",
                    "resolution_m": 0.25,
                    "swath_width_km": 15.0,
                    "aoi_coverage_pct": cov_pct,
                    "orbit_direction": orbit_dir,
                    "relative_orbit": None,
                    "pass_time": p_dt.isoformat(),
                    "seconds_until_pass": int((p_dt - now).total_seconds()),
                    "max_elevation": round(elev, 1),
                    "incidence_angle_deg": round(incidence, 1),
                    "is_commercial": True,
                    "cost_tier": "COMMERCIAL_PREMIUM",
                    "suitability_score": assessment.composite_score,
                    "suitability_grade": assessment.grade,
                    "factors": assessment.factors.to_dict(),
                    "strengths": assessment.strengths,
                    "limitations": assessment.limitations,
                    "tactical_summary": assessment.tactical_summary,
                })

        events.sort(key=lambda e: e["pass_time"])
        return events

    def _generate_synthetic_s1_passes(
        self,
        bbox: BoundingBox,
        now: datetime,
        days_ahead: int,
    ) -> list[dict[str, Any]]:
        """Generate orbital repeat passes for Sentinel-1 (6-day repeat cycle per spacecraft)."""
        passes: list[dict[str, Any]] = []
        center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0
        # Typical descending equatorial crossing ~06:00 UTC, ascending ~18:00 UTC
        base_desc_hour = (6.0 - center_lon / 15.0) % 24.0
        base_asc_hour = (18.0 - center_lon / 15.0) % 24.0

        for day in range(days_ahead):
            # Descending pass every 3 days alternating S1A and S1C
            if day % 3 == 0:
                p_desc = now.replace(minute=15, second=0, microsecond=0) + timedelta(
                    days=day, hours=int(base_desc_hour) - now.hour
                )
                if p_desc > now:
                    passes.append({
                        "time": p_desc.isoformat(),
                        "satellite": "Sentinel-1A" if (day // 3) % 2 == 0 else "Sentinel-1C",
                        "orbit_direction": "DESCENDING",
                        "max_elevation": 58.0,
                        "relative_orbit": 118,
                        "swath_mode": "IW",
                    })
            if day % 4 == 1:
                p_asc = now.replace(minute=45, second=0, microsecond=0) + timedelta(
                    days=day, hours=int(base_asc_hour) - now.hour
                )
                if p_asc > now:
                    passes.append({
                        "time": p_asc.isoformat(),
                        "satellite": "Sentinel-1A",
                        "orbit_direction": "ASCENDING",
                        "max_elevation": 64.0,
                        "relative_orbit": 45,
                        "swath_mode": "IW",
                    })
        return passes

    def _generate_sentinel2_passes(
        self,
        bbox: BoundingBox,
        now: datetime,
        days_ahead: int,
    ) -> list[dict[str, Any]]:
        """Sentinel-2 daytime pass (approx 10:30 local solar time, every 5 days per satellite)."""
        passes: list[dict[str, Any]] = []
        center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0
        # 10:30 Local Solar Time in UTC: UTC = 10.5 - lon / 15
        s2_utc_hour = (10.5 - center_lon / 15.0) % 24.0
        hour_int = int(s2_utc_hour)
        minute_int = int((s2_utc_hour - hour_int) * 60)

        for day in range(days_ahead):
            if day % 5 in (0, 3):  # S2A and S2B staggered 5-day cycle
                dt = (now + timedelta(days=day)).replace(
                    hour=hour_int, minute=minute_int, second=0, microsecond=0
                )
                if dt > now:
                    passes.append({
                        "time": dt,
                        "satellite": "Sentinel-2A" if day % 5 == 0 else "Sentinel-2B",
                        "relative_orbit": 72 if day % 5 == 0 else 115,
                    })
        return passes

    def _generate_umbra_passes(
        self,
        bbox: BoundingBox,
        now: datetime,
        days_ahead: int,
    ) -> list[dict[str, Any]]:
        """Umbra commercial constellation agile tasking opportunities (morning & evening tracks)."""
        passes: list[dict[str, Any]] = []
        center_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0
        base_h = int((8.0 - center_lon / 15.0) % 24.0)

        for day in range(days_ahead):
            # Morning pass
            m_dt = (now + timedelta(days=day)).replace(hour=base_h, minute=20, second=0, microsecond=0)
            if m_dt > now:
                passes.append({
                    "time": m_dt,
                    "satellite": "Umbra-04" if day % 2 == 0 else "Umbra-06",
                    "orbit_direction": "DESCENDING",
                    "elevation": 62.0,
                })
            # Evening pass every 2 days
            if day % 2 == 1:
                e_dt = (now + timedelta(days=day)).replace(hour=(base_h + 12) % 24, minute=40, second=0, microsecond=0)
                if e_dt > now:
                    passes.append({
                        "time": e_dt,
                        "satellite": "Umbra-05",
                        "orbit_direction": "ASCENDING",
                        "elevation": 54.0,
                    })
        return passes

    def _calculate_revisit_intervals(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate statistics on revisit intervals between consecutive satellite passes."""
        if not events:
            return {
                "total_passes": 0,
                "average_revisit_hours": None,
                "min_revisit_hours": None,
                "max_revisit_hours": None,
                "sar_pass_count": 0,
                "optical_pass_count": 0,
            }

        sorted_events = sorted(events, key=lambda e: e["pass_time"])
        intervals_h: list[float] = []

        for i in range(1, len(sorted_events)):
            t1 = self._parse_datetime(sorted_events[i - 1]["pass_time"])
            t2 = self._parse_datetime(sorted_events[i]["pass_time"])
            if t1 and t2:
                diff_h = (t2 - t1).total_seconds() / 3600.0
                if diff_h > 0:
                    intervals_h.append(round(diff_h, 1))

        avg_h = round(sum(intervals_h) / len(intervals_h), 1) if intervals_h else None
        min_h = min(intervals_h) if intervals_h else None
        max_h = max(intervals_h) if intervals_h else None

        sar_cnt = sum(1 for e in sorted_events if e.get("modality") == "SAR")
        opt_cnt = sum(1 for e in sorted_events if e.get("modality") == "OPTICAL")

        return {
            "total_passes": len(sorted_events),
            "average_revisit_hours": avg_h,
            "min_revisit_hours": min_h,
            "max_revisit_hours": max_h,
            "sar_pass_count": sar_cnt,
            "optical_pass_count": opt_cnt,
        }

    def _build_daily_matrix(
        self,
        events: list[dict[str, Any]],
        start_time: datetime,
        horizon_days: int,
    ) -> list[dict[str, Any]]:
        """Construct a calendar grid indexed by day with pass counts and suitability scores."""
        day_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in events:
            p_dt = self._parse_datetime(e["pass_time"])
            if p_dt:
                date_str = p_dt.strftime("%Y-%m-%d")
                day_map[date_str].append(e)

        days: list[dict[str, Any]] = []
        for i in range(horizon_days):
            curr_date = (start_time + timedelta(days=i)).strftime("%Y-%m-%d")
            dt_obj = start_time + timedelta(days=i)
            day_events = day_map.get(curr_date, [])

            scores = [e["suitability_score"] for e in day_events if "suitability_score" in e]
            top_score = max(scores) if scores else None
            top_event = max(day_events, key=lambda e: e.get("suitability_score", 0.0)) if day_events else None
            providers = sorted(list({e["provider_id"] for e in day_events}))

            days.append({
                "date": curr_date,
                "day_of_week": dt_obj.strftime("%A"),
                "pass_count": len(day_events),
                "highest_suitability": top_score,
                "top_opportunity_id": top_event.get("event_id") if top_event else None,
                "providers": providers,
                "events": day_events,
            })

        return days

    def _parse_datetime(self, val: Any) -> Optional[datetime]:
        if isinstance(val, datetime):
            return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        if not val or not isinstance(val, str):
            return None
        try:
            cleaned = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
