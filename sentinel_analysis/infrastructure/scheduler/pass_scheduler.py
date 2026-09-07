"""Background daemon worker for periodic satellite pass checks."""

from datetime import datetime, timezone
import threading
import time
from typing import Any, Optional

from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs


class PassSchedulerWorker:
    """Runs periodic AOI checks in a daemon background thread."""

    def __init__(
        self,
        schedule_use_case: CheckAndScheduleAOIs,
        api_key: Optional[str] = None,
        poll_interval_seconds: float = 3600.0,
        post_pass_repo: Optional[PostPassIngestionRepository] = None,
        settings_repo: Optional[Any] = None,
    ) -> None:
        self._schedule_use_case = schedule_use_case
        self._api_key = api_key
        self._poll_interval = poll_interval_seconds
        self._post_pass_repo = post_pass_repo
        self._settings_repo = settings_repo
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
        if not self._api_key:
            return  # N2YO API key not configured, pass scheduler disabled
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="pass-scheduler")
        self._thread.start()

    def _run_loop(self) -> None:
        while self._running:
            try:
                if self._api_key:
                    self.trigger_check()
            except Exception as exc:
                self._last_error = str(exc)
            # Sleep in 1s increments, dynamically respecting changes to poll interval
            elapsed = 0.0
            while self._running:
                interval = self.get_poll_interval()
                if elapsed >= interval:
                    break
                time.sleep(1)
                elapsed += 1.0

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

        return {
            "is_running": self._running,
            "api_key_configured": bool(self._api_key),
            "poll_interval_seconds": self.get_poll_interval(),
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "last_error": self._last_error,
            "last_results_count": len(self._last_results),
            "active_post_pass_jobs_count": active_jobs_count,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
        }

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


