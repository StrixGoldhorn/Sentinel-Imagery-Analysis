"""Evaluate active AOIs, update satellite pass forecasts, and trigger automated scans / AIS scrapes / post-pass ingestion."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.application.use_cases.ingest_post_pass_imagery import IngestPostPassImagery
from sentinel_analysis.domain.entities import PostPassIngestionJob
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
    ) -> None:
        self._aois = aoi_repository
        self._predictor = pass_predictor
        self._create_scan = create_scan
        self._ingest_ais = ingest_ais
        self._post_pass_repo = post_pass_repository
        self._ingest_post_pass = ingest_post_pass
        self._settings_repo = settings_repo

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

    def execute(self, api_key: str) -> list[dict[str, Any]]:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Satellite prediction API key is required")
        api_key = api_key.strip()
        now = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []
        enabled_satellites = self.get_enabled_satellites()
        active_sats = set(enabled_satellites) if enabled_satellites is not None else None

        for aoi in self._aois.list():
            if not getattr(aoi, "auto_capture_enabled", False):
                continue

            try:
                raw_predictions: list[dict[str, Any]] = []
                try:
                    try:
                        raw_predictions = list(self._predictor.predict(aoi.bbox, api_key, enabled_satellites=enabled_satellites))
                    except TypeError:
                        raw_predictions = list(self._predictor.predict(aoi.bbox, api_key))
                except Exception as pred_exc:
                    logger.warning("Predictor call failed for AOI %s (%s): %s", aoi.id, aoi.name, pred_exc)

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
                        contrib_norm = "both"

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
                                "source": src or ("COMBINED" if contrib_norm == "both" else ("N2YO" if contrib_norm == "n2yo" else "HISTORICAL_MISSION")),
                                "contribution": contrib_norm,
                            })
                        except Exception:
                            continue

                parsed_passes.sort(key=lambda p: p["time"])
                sar_future_passes = [
                    p["time"] for p in parsed_passes 
                    if p.get("contribution") != "n2yo" and p["time"] >= (now - timedelta(minutes=5))
                ]
                valid_future_passes = sar_future_passes if sar_future_passes else [
                    p["time"] for p in parsed_passes if p["time"] >= (now - timedelta(minutes=5))
                ]
                next_pass = valid_future_passes[0] if valid_future_passes else None

                if not next_pass and getattr(aoi, "next_scan", None):
                    scan_dt = aoi.next_scan if aoi.next_scan.tzinfo else aoi.next_scan.replace(tzinfo=timezone.utc)
                    if scan_dt >= (now - timedelta(minutes=5)):
                        next_pass = scan_dt

                ais_records_scraped = 0
                is_flypast_active = False

                if next_pass:
                    self._aois.update_prediction(aoi.id, next_pass, now)

                # Check if ANY predicted pass or aoi.next_scan is currently active (-5min to +5min)
                active_flypast_time: datetime | None = None
                active_pass_info: dict[str, Any] | None = None
                for p in parsed_passes:
                    if -300 <= (p["time"] - now).total_seconds() <= 300:
                        active_flypast_time = p["time"]
                        active_pass_info = p
                        break

                if not active_flypast_time and getattr(aoi, "next_scan", None):
                    scan_dt = aoi.next_scan if aoi.next_scan.tzinfo else aoi.next_scan.replace(tzinfo=timezone.utc)
                    if -300 <= (scan_dt - now).total_seconds() <= 300:
                        active_flypast_time = scan_dt

                if active_flypast_time:
                    is_flypast_active = True
                    if self._ingest_ais is not None:
                        # 1-minute scrape window around current minute within the pass window
                        start_time = max(active_flypast_time - timedelta(minutes=5), now - timedelta(minutes=1))
                        end_time = min(active_flypast_time + timedelta(minutes=5), now + timedelta(minutes=1))
                        try:
                            ingest_res = self._ingest_ais.execute(
                                aoi.bbox,
                                (start_time, end_time),
                                trigger_reason=f"Satellite Flypast ({aoi.name})",
                            )
                        except TypeError:
                            ingest_res = self._ingest_ais.execute(
                                aoi.bbox,
                                (start_time, end_time),
                            )
                        ais_records_scraped = ingest_res["total_inserted"]

                    # Once an AOI is auto scanned, the job is added.
                    # Otherwise, if no AOI autoscan happens (e.g. if the application was not running at that time), DO NOT ADD JOB.
                    if self._post_pass_repo is not None and aoi.id is not None:
                        # Skip n2yo-only predicted passes for imagery catalog polling
                        if not (active_pass_info and active_pass_info.get("contribution") == "n2yo"):
                            existing = self._post_pass_repo.find_by_aoi_and_pass(aoi.id, active_flypast_time)
                            is_completed = (active_flypast_time + timedelta(minutes=5)) <= now
                            target_status = "POLLING_CATALOG" if is_completed else "PENDING_PASS"
                            next_poll = now if is_completed else (active_flypast_time + timedelta(minutes=5))

                            satellite = (active_pass_info.get("satellite") if active_pass_info else None) or "Sentinel-1"
                            orbit_dir = active_pass_info.get("orbit_direction") if active_pass_info else None

                            if existing is None:
                                new_job = PostPassIngestionJob(
                                    aoi_id=aoi.id,
                                    pass_time=active_flypast_time,
                                    satellite=satellite,
                                    orbit_direction=orbit_dir,
                                    status=target_status,
                                    attempts=0,
                                    next_poll_at=next_poll,
                                    created_at=now,
                                    aoi_name=aoi.name,
                                    expected_imagery_time=active_flypast_time,
                                )
                                self._post_pass_repo.add(new_job)
                            elif existing.status == "PENDING_PASS" and is_completed:
                                updated_job = PostPassIngestionJob(
                                    id=existing.id,
                                    aoi_id=existing.aoi_id,
                                    pass_time=existing.pass_time,
                                    satellite=existing.satellite,
                                    orbit_direction=existing.orbit_direction,
                                    status="POLLING_CATALOG",
                                    attempts=existing.attempts,
                                    last_polled_at=existing.last_polled_at,
                                    next_poll_at=now,
                                    scan_folder=existing.scan_folder,
                                    error_message=existing.error_message,
                                    created_at=existing.created_at,
                                    completed_at=existing.completed_at,
                                    aoi_name=aoi.name,
                                    expected_imagery_time=existing.expected_imagery_time,
                                )
                                self._post_pass_repo.update(updated_job)

                results.append({
                    "aoi_id": aoi.id,
                    "name": aoi.name,
                    "next_pass": next_pass.isoformat() if next_pass else None,
                    "flypast_active": is_flypast_active,
                    "ais_records": ais_records_scraped,
                    "status": "FLYPAST_ACTIVE" if is_flypast_active else "SCHEDULED",
                })
            except Exception as exc:
                results.append({
                    "aoi_id": aoi.id,
                    "name": aoi.name,
                    "error": str(exc),
                    "status": "ERROR",
                })

        # Process due post-pass catalog checks
        post_pass_results: list[dict[str, Any]] = []
        if self._ingest_post_pass is not None:
            try:
                post_pass_results = self._ingest_post_pass.execute()
            except Exception:
                pass

        return results
