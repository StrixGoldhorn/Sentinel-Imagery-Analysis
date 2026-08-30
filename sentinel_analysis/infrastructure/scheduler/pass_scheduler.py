"""Background daemon worker for periodic satellite pass checks."""

import threading
import time
from typing import Optional

from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs


class PassSchedulerWorker:
    """Runs periodic AOI checks in a daemon background thread."""

    def __init__(
        self,
        schedule_use_case: CheckAndScheduleAOIs,
        api_key: Optional[str] = None,
        poll_interval_seconds: float = 300.0,
    ) -> None:
        self._schedule_use_case = schedule_use_case
        self._api_key = api_key
        self._poll_interval = poll_interval_seconds
        self._running = False
        self._thread: Optional[threading.Thread] = None

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
                    self._schedule_use_case.execute(self._api_key)
            except Exception:
                pass  # Suppress background errors to keep daemon running
            # Sleep in 1s increments so we can exit cleanly on stop
            for _ in range(int(self._poll_interval)):
                if not self._running:
                    break
                time.sleep(1)

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
