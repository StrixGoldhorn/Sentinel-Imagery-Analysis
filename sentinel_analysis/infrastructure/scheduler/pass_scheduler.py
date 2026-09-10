"""Background scheduler worker for periodic satellite pass checks and SAR imagery ingestion."""

from datetime import datetime, timezone
import logging
import threading
import time
import uuid
from typing import Any, Optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    BackgroundScheduler = None  # type: ignore
    IntervalTrigger = None  # type: ignore

from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs
from sentinel_analysis.application.shutdown import shutdown_coordinator

logger = logging.getLogger(__name__)


class PassSchedulerWorker:
    """Runs 30-second AOI checks and dispatches due SAR catalog jobs every minute.

    Uses APScheduler (BackgroundScheduler) when available, falling back cleanly
    to threading timers if APScheduler is not installed.
    """

    def __init__(
        self,
        schedule_use_case: CheckAndScheduleAOIs,
        api_key: Optional[str] = None,
        poll_interval_seconds: float = 60.0,
        post_pass_repo: Optional[PostPassIngestionRepository] = None,
        settings_repo: Optional[Any] = None,
        pass_monitor: Optional[Any] = None,
        ingest_post_pass: Optional[Any] = None,
        aoi_check_interval_seconds: float = 30.0,
        sar_scan_interval_seconds: Optional[float] = None,
        use_apscheduler: bool = True,
    ) -> None:
        self._schedule_use_case = schedule_use_case
        self._api_key = api_key
        self._aoi_check_interval = max(1.0, float(aoi_check_interval_seconds))
        # The dispatcher checks local job due times frequently; catalog requests are
        # rate-limited independently by each job's fixed hourly next_poll_at.
        self._sar_scan_interval = max(
            1.0,
            float(sar_scan_interval_seconds if sar_scan_interval_seconds is not None else poll_interval_seconds),
        )
        self._post_pass_repo = post_pass_repo
        self._settings_repo = settings_repo
        self._pass_monitor = pass_monitor or getattr(schedule_use_case, "pass_monitor", None)
        self._ingest_post_pass = ingest_post_pass or getattr(schedule_use_case, "_ingest_post_pass", None)
        self._use_apscheduler = use_apscheduler and APSCHEDULER_AVAILABLE
        self._running = False
        self._stop_event = threading.Event()

        self._scheduler: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None

        self._last_run_at: Optional[datetime] = None
        self._last_aoi_check_at: Optional[datetime] = None
        self._last_sar_scan_at: Optional[datetime] = None
        self._last_results: list[dict[str, Any]] = []
        self._last_error: Optional[str] = None
        self._last_aoi_error: Optional[str] = None
        self._last_sar_error: Optional[str] = None
        self._owner_id = str(uuid.uuid4())
        self._is_leader = False

    def _acquire_leadership(self) -> bool:
        if self._post_pass_repo is None or not hasattr(self._post_pass_repo, "try_acquire_scheduler_lease"):
            self._is_leader = True
            return True
        self._is_leader = self._post_pass_repo.try_acquire_scheduler_lease(
            "pass_scheduler", self._owner_id, datetime.now(timezone.utc),
            ttl_seconds=90.0,
        )
        return self._is_leader

    @property
    def backend_type(self) -> str:
        return "apscheduler" if self._use_apscheduler else "threading_fallback"

    def get_aoi_check_interval(self) -> float:
        """Return effective interval in seconds for AOI pass checks (default: 30s)."""
        if self._settings_repo is not None and hasattr(self._settings_repo, "get"):
            try:
                val = self._settings_repo.get("aoi_check_interval_seconds")
                if val is not None:
                    fval = float(val)
                    if fval >= 1.0:
                        return fval
            except Exception:
                pass
        return self._aoi_check_interval

    def set_aoi_check_interval(self, seconds: float) -> None:
        """Update interval for AOI pass checks."""
        val = max(1.0, float(seconds))
        self._aoi_check_interval = val
        if self._settings_repo is not None and hasattr(self._settings_repo, "set"):
            try:
                self._settings_repo.set("scheduler", "aoi_check_interval_seconds", val)
            except Exception:
                pass
        if self._running and self._scheduler is not None and IntervalTrigger is not None:
            try:
                self._scheduler.reschedule_job(
                    "aoi_check_job",
                    trigger=IntervalTrigger(seconds=val),
                )
            except Exception as exc:
                logger.warning("Failed to reschedule APScheduler aoi_check_job: %s", exc)

    def get_sar_scan_interval(self) -> float:
        """Return effective interval in seconds for SAR imagery catalog polling (default: 60s)."""
        if self._settings_repo is not None and hasattr(self._settings_repo, "get"):
            try:
                val = self._settings_repo.get("sar_scan_interval_seconds")
                if val is None:
                    val = self._settings_repo.get("poll_interval_seconds")
                if val is not None:
                    fval = float(val)
                    if fval >= 1.0:
                        return fval
            except Exception:
                pass
        return self._sar_scan_interval

    def set_sar_scan_interval(self, seconds: float) -> None:
        """Update interval for SAR imagery catalog polling."""
        val = max(1.0, float(seconds))
        self._sar_scan_interval = val
        if self._settings_repo is not None and hasattr(self._settings_repo, "set"):
            try:
                self._settings_repo.set("scheduler", "sar_scan_interval_seconds", val)
                self._settings_repo.set("scheduler", "poll_interval_seconds", val)
            except Exception:
                pass
        if self._running and self._scheduler is not None and IntervalTrigger is not None:
            try:
                self._scheduler.reschedule_job(
                    "sar_scan_job",
                    trigger=IntervalTrigger(seconds=val),
                )
            except Exception as exc:
                logger.warning("Failed to reschedule APScheduler sar_scan_job: %s", exc)

    # Backwards compatibility methods
    def get_poll_interval(self) -> float:
        """Alias for get_sar_scan_interval for backwards compatibility."""
        return self.get_sar_scan_interval()

    def set_poll_interval(self, seconds: float) -> None:
        """Alias for set_sar_scan_interval for backwards compatibility."""
        self.set_sar_scan_interval(seconds)

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        self._running = True

        if self._use_apscheduler and BackgroundScheduler is not None and IntervalTrigger is not None:
            try:
                self._scheduler = BackgroundScheduler(daemon=True)
                self._scheduler.add_job(
                    self._run_aoi_check_cycle,
                    trigger=IntervalTrigger(seconds=self.get_aoi_check_interval()),
                    id="aoi_check_job",
                    name="AOI Scan Check (30s)",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    next_run_time=datetime.now(timezone.utc),
                )
                self._scheduler.add_job(
                    self._poll_due_post_pass_jobs,
                    trigger=IntervalTrigger(seconds=self.get_sar_scan_interval()),
                    id="sar_scan_job",
                    name="SAR Imagery Ingestion Dispatcher",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    next_run_time=datetime.now(timezone.utc),
                )
                self._scheduler.start()
                logger.info(
                    "PassSchedulerWorker started with APScheduler (AOI check: %.1fs, SAR scan: %.1fs)",
                    self.get_aoi_check_interval(),
                    self.get_sar_scan_interval(),
                )
                return
            except Exception as exc:
                logger.warning("Failed to initialize APScheduler, falling back to threading: %s", exc)
                self._use_apscheduler = False
                self._scheduler = None

        # Threading fallback
        self._thread = threading.Thread(target=self._run_threading_loop, daemon=True, name="pass-scheduler")
        self._thread.start()
        logger.info(
            "PassSchedulerWorker started with threading worker (AOI check: %.1fs, SAR scan: %.1fs)",
            self.get_aoi_check_interval(),
            self.get_sar_scan_interval(),
        )

    def _run_aoi_check_cycle(self) -> None:
        """Run periodic AOI checks to detect flypasts and manage AIS scrapes."""
        if self._stop_event.is_set() or shutdown_coordinator.is_shutting_down:
            return
        if not self._acquire_leadership():
            return
        try:
            self.trigger_check()
        except Exception as exc:
            self._last_aoi_error = str(exc)
            self._last_error = str(exc)
            logger.warning("Error during periodic AOI scan check: %s", exc)

    def _poll_due_post_pass_jobs(self) -> list[dict[str, Any]]:
        """Check and process any post-pass catalog jobs that are due for SAR imagery ingestion."""
        if self._stop_event.is_set() or shutdown_coordinator.is_shutting_down:
            return []
        if not self._acquire_leadership():
            return []
        ingest_uc = self._ingest_post_pass or getattr(self._schedule_use_case, "_ingest_post_pass", None)
        results: list[dict[str, Any]] = []
        if ingest_uc is not None and self._post_pass_repo is not None:
            try:
                now = datetime.now(timezone.utc)
                if hasattr(self._post_pass_repo, "expire_jobs"):
                    self._post_pass_repo.expire_jobs(now)
                due_jobs = self._post_pass_repo.get_jobs_due_for_poll(now)
                if due_jobs:
                    logger.info("Polling %d due post-pass catalog jobs for SAR imagery...", len(due_jobs))
                    results = ingest_uc.execute()
                self._last_sar_scan_at = now
                self._last_sar_error = None
                return results
            except Exception as exc:
                self._last_sar_error = str(exc)
                self._last_error = str(exc)
                logger.warning("Error during periodic SAR imagery catalog check: %s", exc)
        return results

    def _run_threading_loop(self) -> None:
        """Maintain independent 30-second AOI checks and one-minute SAR dispatches."""
        # Run initial cycle upon starting if not stopping
        if not self._stop_event.is_set() and not shutdown_coordinator.is_shutting_down:
            self._run_aoi_check_cycle()
        if not self._stop_event.is_set() and not shutdown_coordinator.is_shutting_down:
            self._poll_due_post_pass_jobs()

        aoi_elapsed = 0.0
        sar_elapsed = 0.0

        while self._running and not self._stop_event.is_set() and not shutdown_coordinator.is_shutting_down:
            if self._stop_event.wait(timeout=1.0):
                break
            aoi_elapsed += 1.0
            sar_elapsed += 1.0

            aoi_interval = self.get_aoi_check_interval()
            if aoi_elapsed >= aoi_interval:
                aoi_elapsed = 0.0
                if not self._stop_event.is_set() and not shutdown_coordinator.is_shutting_down:
                    self._run_aoi_check_cycle()

            sar_interval = self.get_sar_scan_interval()
            if sar_elapsed >= sar_interval:
                sar_elapsed = 0.0
                if not self._stop_event.is_set() and not shutdown_coordinator.is_shutting_down:
                    self._poll_due_post_pass_jobs()

    def trigger_check(self) -> list[dict[str, Any]]:
        """Run an immediate AOI pass check cycle across active AOIs."""
        now = datetime.now(timezone.utc)
        try:
            results = self._schedule_use_case.execute(self._api_key or "", check_post_pass=False)
            self._last_run_at = now
            self._last_aoi_check_at = now
            self._last_results = results
            failed = [item for item in results if item.get("status") == "ERROR"]
            self._last_error = (
                f"{len(failed)} AOI check(s) failed: "
                + "; ".join(str(item.get("error") or "unknown error") for item in failed[:3])
                if failed else None
            )
            self._last_aoi_error = self._last_error
            return results
        except Exception as exc:
            self._last_aoi_error = str(exc)
            self._last_error = str(exc)
            raise

    def trigger_sar_scan(self) -> list[dict[str, Any]]:
        """Run an immediate SAR imagery catalog check cycle across due post-pass jobs."""
        return self._poll_due_post_pass_jobs()

    def get_status(self) -> dict[str, Any]:
        """Return the current scheduler status, intervals, backend type, and execution details."""
        active_jobs_count = 0
        if self._post_pass_repo is not None:
            try:
                active_jobs_count = len(self._post_pass_repo.get_active_jobs())
            except Exception:
                pass

        active_monitors = []
        if self._pass_monitor is not None and hasattr(self._pass_monitor, "get_active_monitors"):
            try:
                active_monitors = self._pass_monitor.get_active_monitors()
            except Exception:
                pass

        thread_alive = False
        if self._scheduler is not None and hasattr(self._scheduler, "running"):
            thread_alive = bool(self._scheduler.running)
        elif self._thread is not None:
            thread_alive = bool(self._thread.is_alive())

        jobs_list = []
        if self._scheduler is not None and hasattr(self._scheduler, "get_jobs"):
            try:
                for j in self._scheduler.get_jobs():
                    jobs_list.append({
                        "id": j.id,
                        "name": j.name,
                        "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
                    })
            except Exception:
                pass

        subsystem_errors = [error for error in (self._last_aoi_error, self._last_sar_error) if error]
        if not self._running:
            operational_status = "STOPPED"
        elif not thread_alive or subsystem_errors:
            operational_status = "DEGRADED"
        elif self._post_pass_repo is not None and not self._is_leader:
            operational_status = "STANDBY"
        else:
            operational_status = "RUNNING"
        health = {
            "STOPPED": "STOPPED",
            "DEGRADED": "DEGRADED",
            "STANDBY": "HEALTHY",
            "RUNNING": "HEALTHY",
        }[operational_status]
        current_error = "; ".join(subsystem_errors) if subsystem_errors else None
        return {
            "is_running": self._running,
            "health": health,
            "operational_status": operational_status,
            "scheduler_backend": self.backend_type,
            "api_key_configured": bool(self._api_key),
            "aoi_check_interval_seconds": self.get_aoi_check_interval(),
            "sar_scan_interval_seconds": self.get_sar_scan_interval(),
            "poll_interval_seconds": self.get_poll_interval(),
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "last_aoi_check_at": self._last_aoi_check_at.isoformat() if self._last_aoi_check_at else None,
            "last_sar_scan_at": self._last_sar_scan_at.isoformat() if self._last_sar_scan_at else None,
            "last_error": current_error,
            "last_aoi_error": self._last_aoi_error,
            "last_sar_error": self._last_sar_error,
            "is_leader": self._is_leader,
            "worker_id": self._owner_id,
            "last_results_count": len(self._last_results),
            "active_post_pass_jobs_count": active_jobs_count,
            "active_pass_monitors": active_monitors,
            "thread_alive": thread_alive,
            "jobs": jobs_list,
        }

    def stop(self, timeout: float = 2.0) -> None:
        self._running = False
        self._stop_event.set()
        if self._post_pass_repo is not None and hasattr(self._post_pass_repo, "release_scheduler_lease"):
            try:
                self._post_pass_repo.release_scheduler_lease("pass_scheduler", self._owner_id)
            except Exception:
                pass
        self._is_leader = False
        if self._pass_monitor is not None and hasattr(self._pass_monitor, "stop_all"):
            try:
                self._pass_monitor.stop_all()
            except Exception:
                pass

        if self._scheduler is not None:
            try:
                if hasattr(self._scheduler, "remove_all_jobs"):
                    self._scheduler.remove_all_jobs()
            except Exception:
                pass
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=min(timeout, 1.0))
            self._thread = None


# Alias for backward compatibility and concise import
PassScheduler = PassSchedulerWorker



