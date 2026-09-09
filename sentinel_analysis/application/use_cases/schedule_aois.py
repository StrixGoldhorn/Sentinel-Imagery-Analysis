"""Evaluate active AOIs, update satellite pass forecasts, and trigger automated scans / AIS scrapes / post-pass ingestion."""

import logging
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.application.use_cases.ingest_post_pass_imagery import IngestPostPassImagery
from sentinel_analysis.application.use_cases.trigger_automatic_ais import (
    TriggerAutomaticAISScrape,
    is_historical_prediction,
)
from sentinel_analysis.application.shutdown import shutdown_coordinator
from sentinel_analysis.domain.satellite import DEFAULT_ENABLED_SATELLITES


class CheckAndScheduleAOIs:
    """Evaluate active AOIs, update satellite pass forecasts, trigger automated AIS scrapes, and register post-pass ingestion jobs."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        pass_predictor: PassPredictor,
        create_scan: Optional[CreateScan] = None,
        ingest_ais: Optional[IngestAIS] = None,
        post_pass_repository: Optional[PostPassIngestionRepository] = None,
        ingest_post_pass: Optional[IngestPostPassImagery] = None,
        settings_repo: Optional[Any] = None,
        pass_monitor: Optional[Any] = None,
        automatic_scrape: Optional[TriggerAutomaticAISScrape] = None,
    ) -> None:
        self._aois = aoi_repository
        self._predictor = pass_predictor
        self._create_scan = create_scan
        self._ingest_ais = ingest_ais
        self._post_pass_repo = post_pass_repository
        self._ingest_post_pass = ingest_post_pass
        self._settings_repo = settings_repo
        self._pass_monitor = pass_monitor
        self._automatic_scrape = automatic_scrape
        if self._automatic_scrape is None and ingest_ais is not None and post_pass_repository is not None:
            self._automatic_scrape = TriggerAutomaticAISScrape(ingest_ais, post_pass_repository)

    @property
    def pass_monitor(self) -> Optional[Any]:
        return self._pass_monitor

    def get_enabled_satellites(self) -> list[str] | None:
        if self._settings_repo is not None:
            try:
                val = self._settings_repo.get("enabled_satellites")
                if isinstance(val, list):
                    return [str(s).strip() for s in val if str(s).strip()]
                elif isinstance(val, str) and val.strip():
                    return [s.strip() for s in val.split(",") if s.strip()]
            except Exception:
                pass
            return DEFAULT_ENABLED_SATELLITES.copy()
        return None

    def execute(self, api_key: str, check_post_pass: bool = False) -> list[dict[str, Any]]:
        if not isinstance(api_key, str):
            raise ValueError("Satellite prediction API key must be a string")
        if shutdown_coordinator.is_shutting_down:
            return []
        api_key = api_key.strip()
        now = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []
        enabled_satellites = self.get_enabled_satellites()
        active_sats = set(enabled_satellites) if enabled_satellites is not None else None
        if self._pass_monitor is not None and hasattr(self._pass_monitor, "reconcile_enabled_satellites"):
            self._pass_monitor.reconcile_enabled_satellites(enabled_satellites)

        for aoi in self._aois.list():
            if shutdown_coordinator.is_shutting_down:
                logger.info("AOI scheduling loop aborted due to application shutdown")
                break
            if not getattr(aoi, "auto_capture_enabled", False):
                continue

            try:
                predictor_parameters = inspect.signature(self._predictor.predict).parameters
                if "enabled_satellites" in predictor_parameters:
                    raw_predictions = list(
                        self._predictor.predict(aoi.bbox, api_key, enabled_satellites=enabled_satellites)
                    )
                else:
                    raw_predictions = list(self._predictor.predict(aoi.bbox, api_key))

                if not raw_predictions and hasattr(self._aois, "get_cached_forecast") and aoi.id is not None:
                    try:
                        cached = self._aois.get_cached_forecast(aoi.id)
                        if cached and cached.get("predictions"):
                            raw_predictions = list(cached["predictions"])
                    except Exception as cache_exc:
                        logger.debug("Failed to read cached forecast for AOI %s: %s", aoi.id, cache_exc)

                parsed_passes: list[dict[str, Any]] = []
                for pred in raw_predictions:
                    sat_name = pred.get("satellite") or "Sentinel-1A"
                    if active_sats is not None and sat_name not in active_sats:
                        continue

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
                        # Unknown provenance is never eligible for automatic AIS.
                        contrib_norm = "unknown"

                    pass_time_raw = pred.get("time")
                    if pass_time_raw:
                        try:
                            p_time = datetime.fromisoformat(str(pass_time_raw).replace("Z", "+00:00"))
                            if p_time.utcoffset() is None:
                                p_time = p_time.replace(tzinfo=timezone.utc)
                            p_time_utc = p_time.astimezone(timezone.utc)
                            parsed_passes.append({
                                "time": p_time_utc,
                                "satellite": sat_name,
                                "orbit_direction": pred.get("orbit_direction"),
                                "relative_orbit": pred.get("relative_orbit"),
                                "source": src or ("COMBINED" if contrib_norm == "both" else ("N2YO" if contrib_norm == "n2yo" else ("HISTORICAL_MISSION" if contrib_norm == "historical" else "UNKNOWN"))),
                                "contribution": contrib_norm,
                                "basis_product_id": pred.get("basis_product_id"),
                                "basis_acquisition_time": pred.get("basis_acquisition_time"),
                                "basis_satellite": pred.get("basis_satellite"),
                                "basis_relative_orbit": pred.get("basis_relative_orbit"),
                            })
                        except Exception:
                            continue

                parsed_passes.sort(key=lambda p: p["time"])
                sar_future_passes = [
                    p["time"] for p in parsed_passes 
                    if is_historical_prediction(p) and p["time"] >= (now - timedelta(minutes=5))
                ]
                valid_future_passes = sar_future_passes
                next_pass = valid_future_passes[0] if valid_future_passes else None

                ais_records_scraped = 0
                is_flypast_active = False

                if next_pass:
                    self._aois.update_prediction(aoi.id, next_pass, now)

                # Check if ANY predicted pass or aoi.next_scan is currently active (-5min to +5min)
                active_flypast_time: datetime | None = None
                active_pass_info: dict[str, Any] | None = None
                for p in parsed_passes:
                    if is_historical_prediction(p) and -300 <= (p["time"] - now).total_seconds() <= 300:
                        active_flypast_time = p["time"]
                        active_pass_info = p
                        break

                if active_flypast_time:
                    is_flypast_active = True
                    if self._pass_monitor is not None:
                        self._pass_monitor.schedule_or_start(
                            aoi=aoi,
                            pass_time=active_flypast_time,
                            active_pass_info=active_pass_info,
                        )
                    elif self._automatic_scrape is not None:
                        # Non-background callers still use the same causal workflow.
                        automatic_result = self._automatic_scrape.execute(
                            aoi,
                            active_flypast_time,
                            active_pass_info,
                            active_flypast_time + timedelta(minutes=5),
                            now=now,
                        )
                        ais_records_scraped = automatic_result["total_inserted"]
                elif next_pass and self._pass_monitor is not None:
                    next_info = next((p for p in parsed_passes if p["time"] == next_pass), None)
                    self._pass_monitor.schedule_or_start(
                        aoi=aoi,
                        pass_time=next_pass,
                        active_pass_info=next_info,
                    )

                results.append({
                    "aoi_id": aoi.id,
                    "name": aoi.name,
                    "next_pass": next_pass.isoformat() if next_pass else None,
                    "flypast_active": is_flypast_active,
                    "ais_records": ais_records_scraped,
                    "status": (
                        "FLYPAST_ACTIVE" if is_flypast_active
                        else ("SCHEDULED" if next_pass else "NO_HISTORICAL_PREDICTION")
                    ),
                })
            except Exception as exc:
                results.append({
                    "aoi_id": aoi.id,
                    "name": aoi.name,
                    "error": str(exc),
                    "status": "ERROR",
                })

        # Process due post-pass catalog checks only if explicitly requested
        post_pass_results: list[dict[str, Any]] = []
        if check_post_pass and self._ingest_post_pass is not None and not shutdown_coordinator.is_shutting_down:
            try:
                post_pass_results = self._ingest_post_pass.execute()
            except Exception as exc:
                post_pass_results = [{"status": "ERROR", "error": str(exc)}]

        results.extend(post_pass_results)

        return results
