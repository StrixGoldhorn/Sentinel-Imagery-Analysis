"""Evaluate active AOIs, update satellite pass forecasts, and trigger automated scans / AIS scrapes / post-pass ingestion."""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.application.use_cases.ingest_post_pass_imagery import IngestPostPassImagery
from sentinel_analysis.domain.entities import PostPassIngestionJob


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
    ) -> None:
        self._aois = aoi_repository
        self._predictor = pass_predictor
        self._create_scan = create_scan
        self._ingest_ais = ingest_ais
        self._post_pass_repo = post_pass_repository
        self._ingest_post_pass = ingest_post_pass

    def execute(self, api_key: str) -> list[dict[str, Any]]:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Satellite prediction API key is required")
        api_key = api_key.strip()
        now = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []

        for aoi in self._aois.list():
            if not getattr(aoi, "auto_capture_enabled", False):
                continue

            try:
                raw_predictions = self._predictor.predict(aoi.bbox, api_key)
                parsed_passes: list[dict[str, Any]] = []
                for pred in raw_predictions:
                    pass_time_raw = pred.get("time")
                    if pass_time_raw:
                        try:
                            p_time = datetime.fromisoformat(str(pass_time_raw).replace("Z", "+00:00"))
                            p_time_utc = p_time.astimezone(timezone.utc)
                            parsed_passes.append({
                                "time": p_time_utc,
                                "satellite": pred.get("satellite") or "Sentinel-1",
                                "orbit_direction": pred.get("orbit_direction"),
                            })
                        except Exception:
                            continue

                parsed_passes.sort(key=lambda p: p["time"])
                valid_future_passes = [p["time"] for p in parsed_passes if p["time"] >= (now - timedelta(minutes=5))]
                next_pass = valid_future_passes[0] if valid_future_passes else None

                ais_records_scraped = 0
                is_flypast_active = False

                if next_pass:
                    self._aois.update_prediction(aoi.id, next_pass, now)

                    # During the flypast (-5min to +5min of pass time), scrape AIS every minute
                    time_to_pass_sec = (next_pass - now).total_seconds()
                    if -300 <= time_to_pass_sec <= 300:
                        is_flypast_active = True
                        if self._ingest_ais is not None:
                            # 1-minute scrape window around current minute within the pass window
                            start_time = max(next_pass - timedelta(minutes=5), now - timedelta(minutes=1))
                            end_time = min(next_pass + timedelta(minutes=5), now + timedelta(minutes=1))
                            ingest_res = self._ingest_ais.execute(aoi.bbox, (start_time, end_time))
                            ais_records_scraped = ingest_res["total_inserted"]

                # Register post-pass ingestion jobs for active, upcoming, and recently completed passes (last 24h to next 24h)
                if self._post_pass_repo is not None and aoi.id is not None:
                    cutoff_recent = now - timedelta(hours=24)
                    cutoff_future = now + timedelta(hours=24)
                    for pass_info in parsed_passes:
                        p_dt = pass_info["time"]
                        if cutoff_recent <= p_dt <= cutoff_future:
                            existing = self._post_pass_repo.find_by_aoi_and_pass(aoi.id, p_dt)
                            is_completed = (p_dt + timedelta(minutes=5)) <= now
                            target_status = "POLLING_CATALOG" if is_completed else "PENDING_PASS"
                            next_poll = now if is_completed else (p_dt + timedelta(minutes=5))

                            if existing is None:
                                new_job = PostPassIngestionJob(
                                    aoi_id=aoi.id,
                                    pass_time=p_dt,
                                    satellite=pass_info.get("satellite") or "Sentinel-1",
                                    orbit_direction=pass_info.get("orbit_direction"),
                                    status=target_status,
                                    attempts=0,
                                    next_poll_at=next_poll,
                                    created_at=now,
                                    aoi_name=aoi.name,
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
