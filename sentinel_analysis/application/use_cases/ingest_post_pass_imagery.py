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


class IngestPostPassImagery:
    """Checks for newly published Copernicus SAR imagery post-pass and triggers automated scan ingestion."""

    def __init__(
        self,
        post_pass_repository: PostPassIngestionRepository,
        aoi_repository: AreaOfInterestRepository,
        imagery_provider: ImageryProvider,
        create_scan: CreateScan,
        detect_ships: Optional[DetectShips] = None,
        max_wait_hours: float = 6.0,
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

            # Check timeout
            elapsed_seconds = (now - job.pass_time).total_seconds()
            if elapsed_seconds > (self._max_wait_hours * 3600):
                updated_job = PostPassIngestionJob(
                    id=job.id,
                    aoi_id=job.aoi_id,
                    pass_time=job.pass_time,
                    satellite=job.satellite,
                    orbit_direction=job.orbit_direction,
                    status="TIMED_OUT",
                    attempts=job.attempts,
                    last_polled_at=now,
                    next_poll_at=None,
                    scan_folder=job.scan_folder,
                    error_message=f"Exceeded maximum post-pass wait window ({self._max_wait_hours} hours)",
                    created_at=job.created_at,
                    completed_at=now,
                    aoi_name=aoi.name,
                )
                self._jobs.update(updated_job)
                results.append({
                    "job_id": job.id,
                    "aoi_id": job.aoi_id,
                    "status": "TIMED_OUT",
                    "message": f"Exceeded maximum post-pass wait window ({self._max_wait_hours}h)",
                })
                continue

            try:
                # Query Copernicus for acquisition matching pass time window (±15 min)
                window_start = job.pass_time - timedelta(minutes=15)
                window_end = job.pass_time + timedelta(minutes=15)
                
                acquisition_ready = False
                matched_acquisition = None

                if hasattr(self._imagery, "search_historical_acquisitions"):
                    acquisitions = self._imagery.search_historical_acquisitions(
                        aoi.bbox,
                        start_date=window_start,
                        end_date=window_end,
                        limit=5,
                    )
                    if acquisitions:
                        acquisition_ready = True
                else:
                    # Fallback to find_latest_acquisition
                    acq = self._imagery.find_latest_acquisition(aoi.bbox, days_ago=1)
                    if acq is not None and abs((acq.acquired_at - job.pass_time).total_seconds()) <= 1800:
                        acquisition_ready = True
                        matched_acquisition = acq

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
                    )
                    self._jobs.update(completed_job)
                    results.append({
                        "job_id": job.id,
                        "aoi_id": job.aoi_id,
                        "status": "COMPLETED",
                        "scan_folder": scan.folder_name,
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
