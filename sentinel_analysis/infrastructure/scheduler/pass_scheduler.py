"""Background daemon worker for periodic satellite pass checks."""

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Optional

from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs

logger = logging.getLogger(__name__)


class PassSchedulerWorker:
    """Runs periodic AOI checks in a daemon background thread."""

    def __init__(
        self,
        schedule_use_case: CheckAndScheduleAOIs,
        api_key: Optional[str] = None,
        poll_interval_seconds: float = 3600.0,
        post_pass_repo: Optional[PostPassIngestionRepository] = None,
        settings_repo: Optional[Any] = None,
        pass_monitor: Optional[Any] = None,
        ingest_post_pass: Optional[Any] = None,
    ) -> None:
        self._schedule_use_case = schedule_use_case
        self._api_key = api_key
        self._poll_interval = poll_interval_seconds
        self._post_pass_repo = post_pass_repo
        self._settings_repo = settings_repo
        self._pass_monitor = pass_monitor or getattr(schedule_use_case, "pass_monitor", None)
        self._ingest_post_pass = ingest_post_pass or getattr(schedule_use_case, "_ingest_post_pass", None)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_run_at: Optional[datetime] = None
        self._last_results: list[dict[str, Any]] = []
        self._last_error: Optional[str] = None

    def get_poll_interval(self) -> float:
        """Return the effective poll interval in seconds, checking settings if available."""
        if self._settings_repo is not None and hasattr(self._settings_repo, "get"):
            try:
                val = self._settings_repo.get("poll_interval_seconds")
                if val is not None:
                    fval = float(val)
                    if fval >= 1.0:
                        return fval
            except Exception:
                pass
        return self._poll_interval

    def set_poll_interval(self, seconds: float) -> None:
        """Update the poll interval in seconds."""
        val = max(1.0, float(seconds))
        self._poll_interval = val
        if self._settings_repo is not None and hasattr(self._settings_repo, "set"):
            try:
                self._settings_repo.set("scheduler", "poll_interval_seconds", val)
            except Exception:
                pass

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="pass-scheduler")
        self._thread.start()

    def _poll_due_post_pass_jobs(self) -> None:
        """Check and process any post-pass catalog jobs that are due."""
        ingest_uc = self._ingest_post_pass or getattr(self._schedule_use_case, "_ingest_post_pass", None)
        if ingest_uc is not None and self._post_pass_repo is not None:
            try:
                now = datetime.now(timezone.utc)
                due_jobs = self._post_pass_repo.get_jobs_due_for_poll(now)
                if due_jobs:
                    logger.info("Polling %d due post-pass catalog jobs...", len(due_jobs))
                    ingest_uc.execute()
            except Exception as exc:
                logger.warning("Error during periodic post-pass catalog check: %s", exc)

    def _run_loop(self) -> None:
        post_pass_check_interval = 30.0  # Check for due post-pass catalog jobs every 30 seconds
        while self._running:
            try:
                if self._api_key:
                    self.trigger_check()
                else:
                    self._poll_due_post_pass_jobs()
            except Exception as exc:
                self._last_error = str(exc)
            # Sleep in 1s increments, dynamically respecting changes to poll interval
            elapsed = 0.0
            post_pass_elapsed = 0.0
            while self._running:
                interval = self.get_poll_interval()
                if elapsed >= interval:
                    break
                time.sleep(1)
                elapsed += 1.0
                post_pass_elapsed += 1.0
                if post_pass_elapsed >= post_pass_check_interval:
                    post_pass_elapsed = 0.0
                    self._poll_due_post_pass_jobs()

    def trigger_check(self) -> list[dict[str, Any]]:
        """Run an immediate check cycle across active AOIs."""
        if not self._api_key:
            raise ValueError("Satellite prediction API key is not configured")
        now = datetime.now(timezone.utc)
        try:
            results = self._schedule_use_case.execute(self._api_key)
            self._last_run_at = now
            self._last_results = results
            self._last_error = None
            return results
        except Exception as exc:
            self._last_error = str(exc)
            raise

    def get_status(self) -> dict[str, Any]:
        """Return the current daemon status, interval, and last execution details."""
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

        return {
            "is_running": self._running,
            "api_key_configured": bool(self._api_key),
            "poll_interval_seconds": self.get_poll_interval(),
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "last_error": self._last_error,
            "last_results_count": len(self._last_results),
            "active_post_pass_jobs_count": active_jobs_count,
            "active_pass_monitors": active_monitors,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
        }

    def stop(self) -> None:
        self._running = False
        if self._pass_monitor is not None and hasattr(self._pass_monitor, "stop_all"):
            try:
                self._pass_monitor.stop_all()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


