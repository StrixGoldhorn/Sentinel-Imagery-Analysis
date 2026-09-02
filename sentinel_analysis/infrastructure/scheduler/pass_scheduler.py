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
        poll_interval_seconds: float = 60.0,
        post_pass_repo: Optional[PostPassIngestionRepository] = None,
    ) -> None:
        self._schedule_use_case = schedule_use_case
        self._api_key = api_key
        self._poll_interval = poll_interval_seconds
        self._post_pass_repo = post_pass_repo
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_run_at: Optional[datetime] = None
        self._last_results: list[dict[str, Any]] = []
        self._last_error: Optional[str] = None

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
            # Sleep in 1s increments so we can exit cleanly on stop
            for _ in range(int(self._poll_interval)):
                if not self._running:
                    break
                time.sleep(1)

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
            "poll_interval_seconds": self._poll_interval,
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


