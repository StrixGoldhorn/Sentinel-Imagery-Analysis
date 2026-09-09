"""Trigger AIS collection for a historically predicted satellite fly-past."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.domain.entities import AreaOfInterest, PostPassIngestionJob
from sentinel_analysis.domain.satellite import is_historical_prediction


class TriggerAutomaticAISScrape:
    """Run an automatic AIS scrape and create its post-pass imagery job.

    Invoking this use case represents the automatic AIS trigger. Registration is
    performed at trigger time (before external provider calls) so a provider error
    cannot suppress the causally related imagery job.
    """

    def __init__(
        self,
        ingest_ais: IngestAIS,
        post_pass_repository: PostPassIngestionRepository,
    ) -> None:
        self._ingest_ais = ingest_ais
        self._post_pass_repo = post_pass_repository

    def execute(
        self,
        aoi: AreaOfInterest,
        pass_time: datetime,
        active_pass_info: Optional[dict[str, Any]],
        window_end: datetime,
        *,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        if aoi.id is None:
            raise ValueError("An automatic AIS scrape requires a persisted AOI")
        if not is_historical_prediction(active_pass_info):
            raise ValueError("Automatic AIS scraping requires a historical fly-past prediction")

        current = now or datetime.now(timezone.utc)
        if current.utcoffset() is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        pass_utc = pass_time.astimezone(timezone.utc) if pass_time.tzinfo else pass_time.replace(tzinfo=timezone.utc)
        end_utc = window_end.astimezone(timezone.utc) if window_end.tzinfo else window_end.replace(tzinfo=timezone.utc)

        existing = self._post_pass_repo.find_by_aoi_and_pass(aoi.id, pass_utc)
        if existing is None:
            satellite = (active_pass_info or {}).get("satellite") or "Sentinel-1"
            orbit_direction = (active_pass_info or {}).get("orbit_direction")
            status = "POLLING_CATALOG" if current >= end_utc else "PENDING_PASS"
            job = PostPassIngestionJob(
                aoi_id=aoi.id,
                pass_time=pass_utc,
                satellite=satellite,
                orbit_direction=orbit_direction,
                relative_orbit=(active_pass_info or {}).get("relative_orbit"),
                trigger_type="AUTOMATIC_AIS",
                prediction_source=(active_pass_info or {}).get("source"),
                workflow_id=f"aoi:{aoi.id}:pass:{pass_utc.isoformat()}",
                basis_product_id=(active_pass_info or {}).get("basis_product_id"),
                basis_acquisition_time=(active_pass_info or {}).get("basis_acquisition_time"),
                basis_satellite=(active_pass_info or {}).get("basis_satellite"),
                basis_relative_orbit=(active_pass_info or {}).get("basis_relative_orbit"),
                status=status,
                attempts=0,
                next_poll_at=current if status == "POLLING_CATALOG" else end_utc,
                created_at=current,
                aoi_name=aoi.name,
                expected_imagery_time=pass_utc,
            )
            job_id = self._post_pass_repo.add(job)
        else:
            job_id = existing.id

        if job_id is None:
            raise RuntimeError("Post-pass job registration did not return an ID")
        claimed_tick = True
        if hasattr(self._post_pass_repo, "claim_automatic_ais_tick"):
            claimed_tick = self._post_pass_repo.claim_automatic_ais_tick(
                aoi.id, pass_utc, current, job_id
            )
        if not claimed_tick:
            return {
                "total_inserted": 0,
                "logs": [],
                "post_pass_job_id": job_id,
                "pass_time": pass_utc.isoformat(),
                "skipped": True,
                "reason": "This automatic AIS minute was already processed",
            }

        start_time = max(pass_utc - timedelta(minutes=5), current - timedelta(minutes=1))
        end_time = min(end_utc, current + timedelta(minutes=1))
        reason = f"Automatic Satellite Flypast ({aoi.name}) at {pass_utc.isoformat()}"
        try:
            result = self._ingest_ais.execute(
                aoi.bbox,
                (start_time, end_time),
                trigger_reason=reason,
            )
        except Exception as exc:
            if hasattr(self._post_pass_repo, "finish_automatic_ais_tick"):
                self._post_pass_repo.finish_automatic_ais_tick(
                    aoi.id, pass_utc, current, str(exc)
                )
            raise
        if hasattr(self._post_pass_repo, "finish_automatic_ais_tick"):
            self._post_pass_repo.finish_automatic_ais_tick(aoi.id, pass_utc, current)

        return {
            "total_inserted": int(result.get("total_inserted", 0)),
            "logs": list(result.get("logs", [])),
            "post_pass_job_id": job_id,
            "pass_time": pass_utc.isoformat(),
        }
