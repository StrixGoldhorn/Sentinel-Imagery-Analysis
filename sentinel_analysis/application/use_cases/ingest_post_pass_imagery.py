"""Use case to autonomously monitor and ingest Sentinel-1 SAR imagery following satellite passes."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.imagery import ImageryProvider
from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.exceptions import InvalidStateTransitionError, PostPassJobNotFoundError
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.domain.entities import Acquisition, PostPassIngestionJob


def _get_backoff_minutes(attempts: int) -> int:
    """Fixed interval for Copernicus catalog polling."""
    return 60


def _extract_acq_datetime(acq) -> Optional[datetime]:
    if acq is None:
        return None
    if isinstance(acq, Acquisition) or hasattr(acq, "acquired_at"):
        dt = getattr(acq, "acquired_at", None)
        if dt is not None and not isinstance(dt, datetime):
            try:
                dt_str = str(dt).strip()
                if not dt_str or "mock" in dt_str.lower():
                    return None
                parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        elif isinstance(dt, datetime):
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(acq, dict):
        raw = (
            acq.get("properties", {}).get("datetime")
            or acq.get("datetime")
            or acq.get("acquisition_time")
        )
        if raw is not None:
            if isinstance(raw, datetime):
                return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            try:
                raw_str = str(raw).strip()
                if not raw_str or "mock" in raw_str.lower():
                    return None
                parsed = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
                return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except Exception:
                return None
    return None


def _extract_value(acq: Any, *names: str) -> Any:
    if isinstance(acq, dict):
        props = acq.get("properties") if isinstance(acq.get("properties"), dict) else {}
        for name in names:
            if acq.get(name) is not None:
                return acq.get(name)
            if props.get(name) is not None:
                return props.get(name)
        return None
    for name in names:
        value = getattr(acq, name, None)
        if value is not None:
            return value
    return None


def _normalize_satellite(value: Any) -> str:
    return str(value or "").strip().upper().replace("SENTINEL-1", "S1")


def _matches_job(acq: Any, job: PostPassIngestionJob) -> bool:
    expected_sat = _normalize_satellite(job.satellite)
    actual_sat = _normalize_satellite(_extract_value(acq, "satellite", "platform"))
    if expected_sat not in ("", "S1") and actual_sat and actual_sat != expected_sat:
        return False
    expected_orbit = str(job.orbit_direction or "").strip().upper()
    actual_orbit = str(_extract_value(acq, "orbit_direction", "sat:orbit_state") or "").strip().upper()
    if expected_orbit and actual_orbit not in ("", "UNKNOWN") and actual_orbit != expected_orbit:
        return False
    actual_relative_orbit = _extract_value(acq, "relative_orbit", "sat:relative_orbit", "relativeOrbitNumber")
    if job.relative_orbit is not None and actual_relative_orbit is not None:
        try:
            if int(actual_relative_orbit) != job.relative_orbit:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _as_acquisition(acq: Any) -> Acquisition:
    if isinstance(acq, Acquisition):
        return acq
    acquired_at = _extract_acq_datetime(acq)
    if acquired_at is None:
        raise ValueError("Matched catalog acquisition has no valid acquisition time")
    product_id = _extract_value(acq, "product_id", "id")
    satellite = _extract_value(acq, "satellite", "platform") or "Sentinel-1"
    product_type = _extract_value(acq, "product_type", "collection") or "sentinel-1-grd"
    polarisation = _extract_value(acq, "polarisation", "polarization", "sar:polarizations")
    if isinstance(polarisation, str):
        polarizations = tuple(p.strip() for p in polarisation.replace("+", ",").split(",") if p.strip()) or ("VH",)
    elif isinstance(polarisation, (list, tuple)):
        polarizations = tuple(str(p) for p in polarisation) or ("VH",)
    else:
        polarizations = ("VH",)
    relative_orbit = _extract_value(acq, "relative_orbit", "sat:relative_orbit", "relativeOrbitNumber")
    if relative_orbit is not None:
        relative_orbit = int(relative_orbit)
    return Acquisition(
        acquired_at=acquired_at,
        satellite=str(satellite),
        product_type=str(product_type),
        product_id=str(product_id) if product_id else None,
        polarizations=polarizations,
        orbit_direction=_extract_value(acq, "orbit_direction", "sat:orbit_state"),
        relative_orbit=relative_orbit,
    )


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
        if hasattr(self._jobs, "configure_max_wait_hours"):
            self._jobs.configure_max_wait_hours(max_wait_hours)

    @property
    def max_wait_hours(self) -> float:
        return self._max_wait_hours

    def execute(
        self,
        job_id: Optional[int] = None,
        batch_limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Process one requested job or a bounded batch of due jobs.

        The periodic scheduler intentionally keeps the default batch size at one.
        Explicit "poll all" requests can supply a larger bound without changing
        scheduler fairness or allowing an unbounded background task.
        """
        now = datetime.now(timezone.utc)
        if job_id is not None:
            if hasattr(self._jobs, "claim_job"):
                job = self._jobs.claim_job(job_id, now)
                if job is None:
                    existing = self._jobs.get(job_id)
                    if existing is None:
                        raise PostPassJobNotFoundError(f"Post-pass job #{job_id} not found")
                    raise InvalidStateTransitionError(
                        f"Job #{job_id} cannot be polled while in {existing.status} state"
                    )
            else:
                job = self._jobs.get(job_id)
            jobs_to_process = [job] if job is not None else []
        else:
            claim_limit = max(1, min(int(batch_limit), 2000))
            if hasattr(self._jobs, "claim_jobs_due_for_poll"):
                jobs_to_process = self._jobs.claim_jobs_due_for_poll(now, limit=claim_limit)
            else:
                jobs_to_process = self._jobs.get_jobs_due_for_poll(now)[:claim_limit]

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
                # 1. Query for imagery that occur within that ±1 hour
                window_start = expected_time - timedelta(hours=1)
                window_end = expected_time + timedelta(hours=1)

                time_range_imagery = None

                if hasattr(self._imagery, "search_historical_acquisitions"):
                    acquisitions = self._imagery.search_historical_acquisitions(
                        aoi.bbox,
                        start_date=window_start,
                        end_date=window_end,
                        limit=100,
                    )
                    eligible = []
                    for acq in (acquisitions or []):
                        a_dt = _extract_acq_datetime(acq)
                        if a_dt and abs((a_dt - expected_time).total_seconds()) <= 3600 and _matches_job(acq, job):
                            eligible.append((abs((a_dt - expected_time).total_seconds()), acq))
                    if eligible:
                        eligible.sort(key=lambda item: item[0])
                        time_range_imagery = eligible[0][1]

                # The exact matched acquisition is passed through to CreateScan.
                if time_range_imagery is not None:
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

                    matched_acquisition = _as_acquisition(time_range_imagery)
                    scan = self._create_scan.execute(
                        aoi.bbox,
                        aoi_name=aoi.name,
                        acquisition=matched_acquisition,
                        include_dem=True,
                    )
                    # Optionally trigger ship detection on the new scan
                    detection_error: str | None = None
                    if self._detect_ships is not None:
                        try:
                            image_path = Path(scan.image_path)
                            dem_candidates = list(image_path.parent.glob("*_stitched_dem.png")) or list(image_path.parent.glob("*_dem.png"))
                            self._detect_ships.execute(
                                image_path,
                                dem_candidates[0] if dem_candidates else None,
                                threshold=40,
                            )
                        except Exception as exc:
                            detection_error = str(exc) or "Unknown ship-detection error"

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
                        error_message=(
                            f"Imagery ingested, but ship detection failed: {detection_error}"
                            if detection_error else None
                        ),
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
                        "product_id": matched_acquisition.product_id,
                        "imagery_status": "COMPLETED",
                        "detection_status": "FAILED" if detection_error else (
                            "COMPLETED" if self._detect_ships is not None else "NOT_REQUESTED"
                        ),
                        "warning": detection_error,
                    })

                # 5. OTHERWISE, mark as wait expired (if timeout exceeded) or continue polling.
                elif elapsed_seconds > (self._max_wait_hours * 3600):
                    # Timeout: No matching imagery in catalog and maximum wait duration exceeded
                    timeout_msg = f"Wait window expired: Exceeded maximum post-pass wait window ({self._max_wait_hours} hours)"
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
                if elapsed_seconds > (self._max_wait_hours * 3600):
                    timeout_msg = f"Wait window expired: Exceeded maximum post-pass wait window ({self._max_wait_hours} hours). Polling error: {exc}"
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
                        "error": timeout_msg,
                    })
                else:
                    new_attempts = job.attempts + 1
                    next_poll = now + timedelta(minutes=_get_backoff_minutes(new_attempts))
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
