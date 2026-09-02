"""Use case to autonomously monitor and ingest Sentinel-1 SAR imagery following satellite passes."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.imagery import ImageryProvider
from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.domain.entities import PostPassIngestionJob


def _get_backoff_minutes(attempts: int) -> int:
    """Progressive backoff for Copernicus catalog polling."""
    if attempts <= 1:
        return 2
    if attempts == 2:
        return 3
    if attempts == 3:
        return 5
    return 10


def _extract_acq_datetime(acq) -> Optional[datetime]:
    if acq is None:
        return None
    if isinstance(acq, Acquisition) or hasattr(acq, "acquired_at"):
        return acq.acquired_at
    if isinstance(acq, dict):
        raw = acq.get("properties", {}).get("datetime") or acq.get("datetime")
        if raw is not None:
            if isinstance(raw, datetime):
                return raw.astimezone(timezone.utc)
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                return None
    return None


class IngestPostPassImagery:

    """Checks for newly published Copernicus SAR imagery post-pass and triggers automated scan ingestion."""

    def __init__(
        self,
        post_pass_repository: PostPassIngestionRepository,
        aoi_repository: AreaOfInterestRepository,
        imagery_provider: ImageryProvider,
        create_scan: CreateScan,
        detect_ships: Optional[DetectShips] = None,
        max_wait_hours: float = 24.0,
    ) -> None:
        self._jobs = post_pass_repository
        self._aois = aoi_repository
        self._imagery = imagery_provider
        self._create_scan = create_scan
        self._detect_ships = detect_ships
        self._max_wait_hours = max_wait_hours

    def execute(self, job_id: Optional[int] = None) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        if job_id is not None:
            job = self._jobs.get(job_id)
            jobs_to_process = [job] if job is not None else []
        else:
            jobs_to_process = self._jobs.get_jobs_due_for_poll(now)

        results: list[dict[str, Any]] = []

        for job in jobs_to_process:
            if job.id is None:
                continue

            aoi = self._aois.get(job.aoi_id)
            if aoi is None:
                updated_job = PostPassIngestionJob(
                    id=job.id,
                    aoi_id=job.aoi_id,
                    pass_time=job.pass_time,
                    satellite=job.satellite,
                    orbit_direction=job.orbit_direction,
                    status="FAILED",
                    attempts=job.attempts,
                    last_polled_at=now,
                    next_poll_at=None,
                    scan_folder=job.scan_folder,
                    error_message="Area of interest no longer exists",
                    created_at=job.created_at,
                    completed_at=now,
                    aoi_name=job.aoi_name,
                )
                self._jobs.update(updated_job)
                results.append({
                    "job_id": job.id,
                    "aoi_id": job.aoi_id,
                    "status": "FAILED",
                    "error": "Area of interest no longer exists",
                })
                continue

            expected_time = job.expected_imagery_time or job.pass_time
            elapsed_seconds = (now - expected_time).total_seconds()

            try:
                # Query Copernicus for acquisition matching expected imagery window (±1 hour)
                window_start = expected_time - timedelta(hours=1)
                window_end = expected_time + timedelta(hours=1)

                acquisition_ready = False
                matched_acq_time = None
                more_recent_acq_time = None

                if hasattr(self._imagery, "search_historical_acquisitions"):
                    acquisitions = self._imagery.search_historical_acquisitions(
                        aoi.bbox,
                        start_date=window_start,
                        end_date=window_end,
                        limit=5,
                    )
                    for acq in (acquisitions or []):
                        a_dt = _extract_acq_datetime(acq)
                        if a_dt and abs((a_dt - expected_time).total_seconds()) <= 3600:
                            acquisition_ready = True
                            matched_acq_time = a_dt
                            break

                    if not acquisition_ready:
                        # Check if a more recent acquisition has already been published beyond the +1hr window
                        recent_acquisitions = self._imagery.search_historical_acquisitions(
                            aoi.bbox,
                            start_date=window_end,
                            end_date=now + timedelta(days=1),
                            limit=5,
                        )
                        for acq in (recent_acquisitions or []):
                            a_dt = _extract_acq_datetime(acq)
                            if a_dt and a_dt > window_end:
                                more_recent_acq_time = a_dt
                                break
                else:
                    # Fallback to find_latest_acquisition
                    acq = self._imagery.find_latest_acquisition(aoi.bbox, days_ago=1)
                    if acq is not None:
                        diff_sec = (acq.acquired_at - expected_time).total_seconds()
                        if abs(diff_sec) <= 3600:
                            acquisition_ready = True
                            matched_acq_time = acq.acquired_at
                        elif diff_sec > 3600:
                            more_recent_acq_time = acq.acquired_at

                if acquisition_ready:
                    # Mark as INGESTING
                    ingesting_job = PostPassIngestionJob(
                        id=job.id,
                        aoi_id=job.aoi_id,
                        pass_time=job.pass_time,
                        satellite=job.satellite,
                        orbit_direction=job.orbit_direction,
                        status="INGESTING",
                        attempts=job.attempts + 1,
                        last_polled_at=now,
                        next_poll_at=None,
                        scan_folder=job.scan_folder,
                        error_message=None,
                        created_at=job.created_at,
                        completed_at=None,
                        aoi_name=aoi.name,
                        expected_imagery_time=expected_time,
                    )
                    self._jobs.update(ingesting_job)

                    # Trigger CreateScan
                    scan = self._create_scan.execute(aoi.bbox)

                    # Optionally trigger ship detection on the new scan
                    if self._detect_ships is not None:
                        try:
                            image_path = Path(scan.image_path)
                            dem_candidates = list(image_path.parent.glob("*_stitched_dem.png")) or list(image_path.parent.glob("*_dem.png"))
                            self._detect_ships.execute(
                                image_path,
                                dem_candidates[0] if dem_candidates else None,
                                threshold=40,
                            )
                        except Exception:
                            pass  # Detection failure does not invalidate successful scan ingestion

                    completed_job = PostPassIngestionJob(
                        id=job.id,
                        aoi_id=job.aoi_id,
                        pass_time=job.pass_time,
                        satellite=job.satellite,
                        orbit_direction=job.orbit_direction,
                        status="COMPLETED",
                        attempts=job.attempts + 1,
                        last_polled_at=now,
                        next_poll_at=None,
                        scan_folder=scan.folder_name,
                        error_message=None,
                        created_at=job.created_at,
                        completed_at=now,
                        aoi_name=aoi.name,
                        expected_imagery_time=expected_time,
                    )
                    self._jobs.update(completed_job)
                    results.append({
                        "job_id": job.id,
                        "aoi_id": job.aoi_id,
                        "status": "COMPLETED",
                        "scan_folder": scan.folder_name,
                    })
                elif more_recent_acq_time is not None:
                    # A more recent orbit has already occurred and been published; expected pass was missed
                    err_msg = (
                        f"Timing mismatch: Detected more recent imagery acquired at "
                        f"{more_recent_acq_time.strftime('%Y-%m-%d %H:%M:%S UTC')}, which is outside the expected window "
                        f"({expected_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ± 1h). Target pass imagery was not acquired."
                    )
                    failed_job = PostPassIngestionJob(
                        id=job.id,
                        aoi_id=job.aoi_id,
                        pass_time=job.pass_time,
                        satellite=job.satellite,
                        orbit_direction=job.orbit_direction,
                        status="FAILED",
                        attempts=job.attempts + 1,
                        last_polled_at=now,
                        next_poll_at=None,
                        scan_folder=job.scan_folder,
                        error_message=err_msg,
                        created_at=job.created_at,
                        completed_at=now,
                        aoi_name=aoi.name,
                        expected_imagery_time=expected_time,
                    )
                    self._jobs.update(failed_job)
                    results.append({
                        "job_id": job.id,
                        "aoi_id": job.aoi_id,
                        "status": "FAILED",
                        "error": err_msg,
                    })
                elif elapsed_seconds > (self._max_wait_hours * 3600):
                    # Timeout: No matching imagery in catalog and maximum wait duration exceeded
                    timeout_msg = f"Exceeded maximum post-pass wait window ({self._max_wait_hours} hours)"
                    timed_out_job = PostPassIngestionJob(
                        id=job.id,
                        aoi_id=job.aoi_id,
                        pass_time=job.pass_time,
                        satellite=job.satellite,
                        orbit_direction=job.orbit_direction,
                        status="TIMED_OUT",
                        attempts=job.attempts + 1,
                        last_polled_at=now,
                        next_poll_at=None,
                        scan_folder=job.scan_folder,
                        error_message=timeout_msg,
                        created_at=job.created_at,
                        completed_at=now,
                        aoi_name=aoi.name,
                        expected_imagery_time=expected_time,
                    )
                    self._jobs.update(timed_out_job)
                    results.append({
                        "job_id": job.id,
                        "aoi_id": job.aoi_id,
                        "status": "TIMED_OUT",
                        "message": timeout_msg,
                    })
                else:
                    new_attempts = job.attempts + 1
                    backoff_mins = _get_backoff_minutes(new_attempts)
                    next_poll = now + timedelta(minutes=backoff_mins)

                    updated_job = PostPassIngestionJob(
                        id=job.id,
                        aoi_id=job.aoi_id,
                        pass_time=job.pass_time,
                        satellite=job.satellite,
                        orbit_direction=job.orbit_direction,
                        status="POLLING_CATALOG",
                        attempts=new_attempts,
                        last_polled_at=now,
                        next_poll_at=next_poll,
                        scan_folder=job.scan_folder,
                        error_message=None,
                        created_at=job.created_at,
                        completed_at=None,
                        aoi_name=aoi.name,
                        expected_imagery_time=expected_time,
                    )
                    self._jobs.update(updated_job)
                    results.append({
                        "job_id": job.id,
                        "aoi_id": job.aoi_id,
                        "status": "POLLING_CATALOG",
                        "attempts": new_attempts,
                        "next_poll_at": next_poll.isoformat(),
                    })
            except Exception as exc:
                new_attempts = job.attempts + 1
                next_poll = now + timedelta(minutes=5)
                updated_job = PostPassIngestionJob(
                    id=job.id,
                    aoi_id=job.aoi_id,
                    pass_time=job.pass_time,
                    satellite=job.satellite,
                    orbit_direction=job.orbit_direction,
                    status="POLLING_CATALOG",
                    attempts=new_attempts,
                    last_polled_at=now,
                    next_poll_at=next_poll,
                    scan_folder=job.scan_folder,
                    error_message=str(exc),
                    created_at=job.created_at,
                    completed_at=None,
                    aoi_name=aoi.name,
                    expected_imagery_time=expected_time,
                )
                self._jobs.update(updated_job)
                results.append({
                    "job_id": job.id,
                    "aoi_id": job.aoi_id,
                    "status": "ERROR",
                    "error": str(exc),
                    "next_poll_at": next_poll.isoformat(),
                })

        return results
