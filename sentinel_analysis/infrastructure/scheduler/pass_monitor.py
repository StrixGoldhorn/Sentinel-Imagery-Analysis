"""Dedicated background monitor for satellite flypast AIS scraping."""

from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Optional

from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.domain.entities import AreaOfInterest, PostPassIngestionJob

logger = logging.getLogger(__name__)


class BackgroundPassMonitor:
    """Manages high-frequency minute-by-minute AIS scraping during active satellite flypast windows."""

    def __init__(
        self,
        ingest_ais: Optional[Any] = None,
        post_pass_repo: Optional[PostPassIngestionRepository] = None,
        interval_seconds: float = 60.0,
    ) -> None:
        self._ingest_ais = ingest_ais
        self._post_pass_repo = post_pass_repo
        self._interval_seconds = max(0.5, float(interval_seconds))
        self._monitors: dict[tuple[int, str], dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def schedule_or_start(
        self,
        aoi: AreaOfInterest,
        pass_time: datetime,
        active_pass_info: Optional[dict[str, Any]] = None,
        window_minutes: float = 5.0,
    ) -> Optional[dict[str, Any]]:
        """Schedule or immediately start a 60-second AIS scrape monitor for a satellite pass."""
        if aoi.id is None:
            return None

        # Ensure UTC timezone
        pass_utc = pass_time.astimezone(timezone.utc) if pass_time.tzinfo else pass_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        window_start = pass_utc - timedelta(minutes=window_minutes)
        window_end = pass_utc + timedelta(minutes=window_minutes)

        if now > window_end:
            logger.debug("Pass window for AOI %s already ended at %s", aoi.id, window_end)
            return None

        key = (aoi.id, pass_utc.isoformat())
        with self._lock:
            existing = self._monitors.get(key)
            if existing and existing.get("status") in ("SCHEDULED", "ACTIVE"):
                return existing

            monitor_entry: dict[str, Any] = {
                "aoi_id": aoi.id,
                "aoi_name": aoi.name,
                "bbox": aoi.bbox,
                "pass_time": pass_utc,
                "window_start": window_start,
                "window_end": window_end,
                "active_pass_info": active_pass_info,
                "status": "SCHEDULED",
                "scrapes_completed": 0,
                "records_ingested": 0,
                "last_scraped_at": None,
                "thread": None,
                "timer": None,
                "error": None,
            }
            self._monitors[key] = monitor_entry

        if now >= window_start:
            # Pass is currently active: start monitoring thread immediately
            self._start_active_thread(key, aoi, pass_utc, window_start, window_end, active_pass_info)
        else:
            # Pass is upcoming: set timer to activate at window_start
            delay = (window_start - now).total_seconds()
            timer = threading.Timer(
                delay,
                self._start_active_thread,
                args=[key, aoi, pass_utc, window_start, window_end, active_pass_info],
            )
            timer.daemon = True
            with self._lock:
                monitor_entry["timer"] = timer
            timer.start()
            logger.info("Scheduled pass monitor for AOI %s (%s) in %.1f seconds", aoi.id, aoi.name, delay)

        return monitor_entry

    def _start_active_thread(
        self,
        key: tuple[int, str],
        aoi: AreaOfInterest,
        pass_time: datetime,
        window_start: datetime,
        window_end: datetime,
        active_pass_info: Optional[dict[str, Any]],
    ) -> None:
        with self._lock:
            entry = self._monitors.get(key)
            if not entry or self._stop_event.is_set():
                return
            entry["status"] = "ACTIVE"
            thread = threading.Thread(
                target=self._run_pass_loop,
                args=(key, aoi, pass_time, window_start, window_end, active_pass_info),
                daemon=True,
                name=f"pass-monitor-aoi-{aoi.id}",
            )
            entry["thread"] = thread
            thread.start()
            logger.info("Started active 60s pass monitor for AOI %s (%s) until %s", aoi.id, aoi.name, window_end)

    def _run_pass_loop(
        self,
        key: tuple[int, str],
        aoi: AreaOfInterest,
        pass_time: datetime,
        window_start: datetime,
        window_end: datetime,
        active_pass_info: Optional[dict[str, Any]],
    ) -> None:
        """Runs the 60-second periodic AIS scraping loop across the active flypast window."""
        while not self._stop_event.is_set():
            tick_now = datetime.now(timezone.utc)
            if tick_now > window_end:
                break

            if tick_now < window_start:
                sleep_secs = min(self._interval_seconds, (window_start - tick_now).total_seconds())
                if self._stop_event.wait(timeout=max(0.1, sleep_secs)):
                    break
                continue

            # Scrape 1-minute window around current minute
            start_time = max(window_start, tick_now - timedelta(minutes=1))
            end_time = min(window_end, tick_now + timedelta(minutes=1))
            records = 0

            if self._ingest_ais is not None:
                try:
                    try:
                        res = self._ingest_ais.execute(
                            aoi.bbox,
                            (start_time, end_time),
                            trigger_reason=f"Satellite Flypast ({aoi.name}) [Minute Scan]",
                        )
                    except TypeError:
                        res = self._ingest_ais.execute(aoi.bbox, (start_time, end_time))
                    records = res.get("total_inserted", 0) if isinstance(res, dict) else 0
                except Exception as exc:
                    logger.warning("AIS minute scrape failed for AOI %s (%s): %s", aoi.id, aoi.name, exc)
                    with self._lock:
                        if key in self._monitors:
                            self._monitors[key]["error"] = str(exc)

            # Register or update PostPassIngestionJob once an autoscan has occurred
            if self._post_pass_repo is not None and aoi.id is not None:
                self._ensure_post_pass_job(aoi, pass_time, active_pass_info, window_end)

            with self._lock:
                if key in self._monitors:
                    self._monitors[key]["scrapes_completed"] += 1
                    self._monitors[key]["records_ingested"] += records
                    self._monitors[key]["last_scraped_at"] = tick_now

            # Wait for next 60s interval or remaining time until window_end
            remaining_to_end = (window_end - datetime.now(timezone.utc)).total_seconds()
            sleep_duration = min(self._interval_seconds, max(0.1, remaining_to_end))
            if self._stop_event.wait(timeout=sleep_duration):
                break

        # Pass window has completed
        with self._lock:
            if key in self._monitors:
                self._monitors[key]["status"] = "CANCELLED" if self._stop_event.is_set() else "COMPLETED"

        # Transition post-pass job to POLLING_CATALOG once pass window ends
        if not self._stop_event.is_set() and self._post_pass_repo is not None and aoi.id is not None:
            self._transition_job_to_polling(aoi.id, pass_time)

    def _ensure_post_pass_job(
        self,
        aoi: AreaOfInterest,
        pass_time: datetime,
        active_pass_info: Optional[dict[str, Any]],
        window_end: datetime,
    ) -> None:
        """Ensure a post-pass ingestion job is added upon active autoscan."""
        if active_pass_info and active_pass_info.get("contribution") == "n2yo":
            return  # Optical-only passes are not tracked for SAR catalog polling

        try:
            existing = self._post_pass_repo.find_by_aoi_and_pass(aoi.id, pass_time)
            now = datetime.now(timezone.utc)
            is_completed = now >= window_end
            status = "POLLING_CATALOG" if is_completed else "PENDING_PASS"
            next_poll = now if is_completed else window_end
            satellite = (active_pass_info.get("satellite") if active_pass_info else None) or "Sentinel-1"
            orbit_dir = active_pass_info.get("orbit_direction") if active_pass_info else None

            if existing is None:
                new_job = PostPassIngestionJob(
                    aoi_id=aoi.id,
                    pass_time=pass_time,
                    satellite=satellite,
                    orbit_direction=orbit_dir,
                    status=status,
                    attempts=0,
                    next_poll_at=next_poll,
                    created_at=now,
                    aoi_name=aoi.name,
                    expected_imagery_time=pass_time,
                )
                self._post_pass_repo.add(new_job)
                logger.info("Registered post-pass job for AOI %s (%s) at pass %s", aoi.id, aoi.name, pass_time)
        except Exception as exc:
            logger.warning("Failed to register post-pass job for AOI %s: %s", aoi.id, exc)

    def _transition_job_to_polling(self, aoi_id: int, pass_time: datetime) -> None:
        """Transition post-pass job to POLLING_CATALOG when window ends."""
        try:
            existing = self._post_pass_repo.find_by_aoi_and_pass(aoi_id, pass_time)
            if existing and existing.status == "PENDING_PASS":
                now = datetime.now(timezone.utc)
                updated = PostPassIngestionJob(
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
                    aoi_name=existing.aoi_name,
                    expected_imagery_time=existing.expected_imagery_time,
                )
                self._post_pass_repo.update(updated)
                logger.info("Transitioned post-pass job for AOI %s to POLLING_CATALOG", aoi_id)
        except Exception as exc:
            logger.warning("Failed to transition post-pass job for AOI %s to POLLING_CATALOG: %s", aoi_id, exc)

    def is_monitoring(self, aoi_id: int, pass_time: datetime) -> bool:
        """Check if an active or scheduled monitor exists for the given AOI and pass."""
        pass_utc = pass_time.astimezone(timezone.utc) if pass_time.tzinfo else pass_time.replace(tzinfo=timezone.utc)
        key = (aoi_id, pass_utc.isoformat())
        with self._lock:
            entry = self._monitors.get(key)
            return bool(entry and entry.get("status") in ("SCHEDULED", "ACTIVE"))

    def get_active_monitors(self) -> list[dict[str, Any]]:
        """Return serialized list of all active or scheduled pass monitors."""
        with self._lock:
            result = []
            for entry in self._monitors.values():
                result.append({
                    "aoi_id": entry["aoi_id"],
                    "aoi_name": entry["aoi_name"],
                    "pass_time": entry["pass_time"].isoformat(),
                    "window_start": entry["window_start"].isoformat(),
                    "window_end": entry["window_end"].isoformat(),
                    "status": entry["status"],
                    "scrapes_completed": entry["scrapes_completed"],
                    "records_ingested": entry["records_ingested"],
                    "last_scraped_at": entry["last_scraped_at"].isoformat() if entry["last_scraped_at"] else None,
                    "error": entry["error"],
                })
            return result

    def stop_for_aoi(self, aoi_id: int) -> None:
        """Cancel monitors for a specific AOI."""
        with self._lock:
            for (aid, _), entry in list(self._monitors.items()):
                if aid == aoi_id:
                    if entry.get("timer"):
                        entry["timer"].cancel()
                    entry["status"] = "CANCELLED"

    def stop_all(self) -> None:
        """Stop all running monitors and cancel all scheduled timers."""
        self._stop_event.set()
        with self._lock:
            for entry in self._monitors.values():
                if entry.get("timer"):
                    entry["timer"].cancel()
                entry["status"] = "CANCELLED"
