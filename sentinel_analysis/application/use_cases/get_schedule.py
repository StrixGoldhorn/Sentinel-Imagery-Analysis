"""Use case to aggregate and forecast upcoming satellite passes and AIS scrape windows."""

import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.domain.entities import AreaOfInterest


class GetUpcomingScrapes:
    """Collects and organizes planned satellite flypasts and AIS scrape windows across all AOIs."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        pass_predictor: PassPredictor,
    ) -> None:
        self._aoi_repository = aoi_repository
        self._predictor = pass_predictor

    def execute(
        self,
        api_key: str,
        auto_capture_only: bool = False,
        aoi_id: Optional[int] = None,
        days_ahead: int = 14,
    ) -> dict[str, Any]:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Satellite prediction API key is required")
        api_key = api_key.strip()
        now = datetime.now(timezone.utc)
        max_future = now + timedelta(days=max(1, int(days_ahead)))

        all_aois = list(self._aoi_repository.list())
        total_aois = len(all_aois)
        auto_capture_count = sum(1 for a in all_aois if getattr(a, "auto_capture_enabled", False))

        target_aois = all_aois
        if aoi_id is not None:
            target_aois = [a for a in target_aois if a.id == aoi_id]
        if auto_capture_only:
            target_aois = [a for a in target_aois if getattr(a, "auto_capture_enabled", False)]

        aoi_predictions_map: dict[int, list[dict[str, Any]]] = {}
        uncached_aois: list[AreaOfInterest] = []

        # 1. First attempt to load valid forecasts from database cache (sub-millisecond)
        has_cache_getter = hasattr(self._aoi_repository, "get_cached_forecast")
        for aoi in target_aois:
            if has_cache_getter and aoi.id is not None:
                try:
                    cached = self._aoi_repository.get_cached_forecast(aoi.id)
                    if cached and cached.get("predictions"):
                        aoi_predictions_map[aoi.id] = cached.get("predictions", [])
                        continue
                except Exception:
                    pass
            uncached_aois.append(aoi)

        # 2. Concurrently fetch predictions for uncached AOIs to minimize latency
        if uncached_aois:
            def _fetch_for_aoi(target: AreaOfInterest) -> tuple[int, list[dict[str, Any]]]:
                try:
                    preds = self._predictor.predict(target.bbox, api_key)
                    # Automatically populate SQLite cache if available
                    if target.id is not None and hasattr(self._aoi_repository, "save_cached_forecast"):
                        try:
                            self._aoi_repository.save_cached_forecast(
                                aoi_id=target.id,
                                forecast_data={"predictions": preds},
                                ttl_seconds=10800,
                            )
                        except Exception:
                            pass
                    return (target.id, preds)
                except Exception:
                    return (target.id, [])

            max_workers = min(6, max(1, len(uncached_aois)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_fetch_for_aoi, a) for a in uncached_aois]
                for fut in concurrent.futures.as_completed(futures):
                    a_id, preds = fut.result()
                    aoi_predictions_map[a_id] = preds

        events: list[dict[str, Any]] = []

        for aoi in target_aois:
            raw_predictions = aoi_predictions_map.get(aoi.id, [])

            for pred in raw_predictions:
                pass_time_raw = pred.get("time")
                if not pass_time_raw:
                    continue
                try:
                    pass_dt = datetime.fromisoformat(str(pass_time_raw).replace("Z", "+00:00"))
                    if pass_dt.utcoffset() is None:
                        pass_dt = pass_dt.replace(tzinfo=timezone.utc)
                    pass_dt = pass_dt.astimezone(timezone.utc)
                except (ValueError, TypeError):
                    continue

                # Filter within window: at least 5m before now and within max_future
                if pass_dt < (now - timedelta(minutes=5)) or pass_dt > max_future:
                    continue

                window_start = pass_dt - timedelta(minutes=5)
                window_end = pass_dt + timedelta(minutes=5)
                is_active = window_start <= now <= window_end
                seconds_until = (pass_dt - now).total_seconds()
                is_auto = getattr(aoi, "auto_capture_enabled", False)

                if is_active:
                    status = "FLYPAST_ACTIVE"
                elif is_auto:
                    status = "SCHEDULED"
                else:
                    status = "PREDICTED_ONLY"

                src = pred.get("source")
                contrib = pred.get("contribution")
                if contrib:
                    contrib_norm = str(contrib).strip().lower()
                elif src == "COMBINED":
                    contrib_norm = "both"
                elif src == "HISTORICAL_MISSION":
                    contrib_norm = "historical"
                elif src == "N2YO":
                    contrib_norm = "n2yo"
                else:
                    contrib_norm = "both"

                # For planned scrapes, only plan for those which are either historical, or both.
                # Do not plan scrape for n2yo-only predictions.
                if contrib_norm == "n2yo" or (src == "N2YO" and contrib_norm != "both"):
                    continue

                contrib = contrib_norm
                if not src:
                    src = "COMBINED" if contrib == "both" else "HISTORICAL_MISSION"

                contrib_label = pred.get("contribution_label") or (
                    "Both (N2YO + Historical)"
                    if contrib == "both"
                    else "Historical Repeat Cycle Only"
                )
                contrib_detail = pred.get("contribution_detail") or pred.get("historical_match") or (
                    "Cross-validated: N2YO tracking confirmed by Sentinel-1 repeat cycle"
                    if contrib == "both"
                    else "Extrapolated from Sentinel-1 12-day repeat cycle"
                )

                events.append({
                    "aoi_id": aoi.id,
                    "aoi_name": aoi.name,
                    "bbox": aoi.bbox.as_list(),
                    "auto_capture_enabled": is_auto,
                    "pass_time": pass_dt.isoformat(),
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "satellite": pred.get("satellite") or "Sentinel-1",
                    "orbit_direction": pred.get("orbit_direction"),
                    "relative_orbit": pred.get("relative_orbit"),
                    "confidence_score": pred.get("confidence_score"),
                    "max_elevation": pred.get("max_elevation"),
                    "source": src,
                    "contribution": contrib,
                    "contribution_label": contrib_label,
                    "contribution_detail": contrib_detail,
                    "historical_match": pred.get("historical_match"),
                    "swath_mode": pred.get("swath_mode"),
                    "status": status,
                    "is_active": is_active,
                    "seconds_until_pass": int(seconds_until),
                })

        # Sort chronologically
        events.sort(key=lambda e: e["pass_time"])

        upcoming_24h = sum(1 for e in events if 0 <= e["seconds_until_pass"] <= 86400)
        upcoming_7d = sum(1 for e in events if 0 <= e["seconds_until_pass"] <= 7 * 86400)
        active_flypasts = sum(1 for e in events if e["is_active"])
        cross_validated_count = sum(1 for e in events if e.get("contribution") == "both")
        n2yo_only_count = sum(1 for e in events if e.get("contribution") == "n2yo")
        historical_only_count = sum(1 for e in events if e.get("contribution") == "historical")

        return {
            "events": events,
            "metrics": {
                "total_aois": total_aois,
                "auto_capture_count": auto_capture_count,
                "total_upcoming_scrapes": len(events),
                "upcoming_24h_count": upcoming_24h,
                "upcoming_7d_count": upcoming_7d,
                "active_flypasts_count": active_flypasts,
                "cross_validated_count": cross_validated_count,
                "n2yo_only_count": n2yo_only_count,
                "historical_only_count": historical_only_count,
            },
            "generated_at": now.isoformat(),
        }
