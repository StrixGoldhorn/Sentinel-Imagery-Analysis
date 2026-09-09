"""Cooperative shutdown coordinator and signal management."""

import atexit
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class ShutdownCoordinator:
    """Manages application-wide graceful shutdown with a hard deadline watchdog."""

    def __init__(self) -> None:
        self._shutdown_event = threading.Event()
        self._lock = threading.Lock()
        self._cleanup_callbacks: list[tuple[int, Callable[[], Any]]] = []
        self._watchdog_started = False
        self._is_handling_signal = False
        self._installed = False

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been requested."""
        return self._shutdown_event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for shutdown signal or timeout. Returns True if shutdown was signaled."""
        return self._shutdown_event.wait(timeout=timeout)

    def sleep(self, duration: float) -> bool:
        """Sleep for `duration` seconds, returning True immediately if shutdown is requested."""
        if duration <= 0:
            return self.is_shutting_down
        return self._shutdown_event.wait(timeout=duration)

    def register_cleanup(self, callback: Callable[[], Any], priority: int = 100) -> None:
        """Register a cleanup callback. Lower priority numbers run first."""
        with self._lock:
            self._cleanup_callbacks.append((priority, callback))
            self._cleanup_callbacks.sort(key=lambda item: item[0])

    def unregister_cleanup(self, callback: Callable[[], Any]) -> None:
        """Unregister a cleanup callback."""
        with self._lock:
            self._cleanup_callbacks = [
                item for item in self._cleanup_callbacks if item[1] != callback
            ]

    def _start_watchdog(self, deadline_seconds: float = 3.0) -> None:
        """Start a background daemon watchdog thread that will force exit if cleanup takes too long."""
        with self._lock:
            if self._watchdog_started:
                return
            self._watchdog_started = True

        def _watchdog_target() -> None:
            time.sleep(deadline_seconds)
            if not self._shutdown_event.is_set():
                return
            # If still alive after deadline_seconds, forcefully terminate process
            logger.warning(
                "Graceful shutdown exceeded %.1fs deadline. Forcing exit immediately.",
                deadline_seconds,
            )
            os._exit(0)

        watchdog_thread = threading.Thread(
            target=_watchdog_target,
            name="shutdown-watchdog",
            daemon=True,
        )
        watchdog_thread.start()

    def reset(self) -> None:
        """Reset the coordinator state (primarily for test environments)."""
        with self._lock:
            self._shutdown_event.clear()
            self._watchdog_started = False
            self._cleanup_callbacks.clear()

    def request_shutdown(
        self,
        reason: str = "Requested",
        hard_deadline_seconds: float = 3.0,
        trigger_exit: bool = False,
        enable_watchdog: bool = True,
    ) -> None:
        """Initiate graceful shutdown across all components."""
        if not self._shutdown_event.is_set():
            logger.info("Shutdown initiated (%s). Starting %.1fs watchdog timer.", reason, hard_deadline_seconds)
            self._shutdown_event.set()

        if enable_watchdog:
            self._start_watchdog(deadline_seconds=hard_deadline_seconds)

        # Execute registered cleanups
        with self._lock:
            callbacks = [cb for _, cb in self._cleanup_callbacks]

        for cb in callbacks:
            try:
                cb()
            except Exception as exc:
                logger.debug("Error in shutdown cleanup hook: %s", exc)

        if trigger_exit:
            sys.exit(0)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle incoming process termination signals."""
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info("Received signal %s. Initiating fast shutdown within 3s...", sig_name)

        self.request_shutdown(reason=f"Signal {sig_name}", hard_deadline_seconds=3.0)

        # Raise KeyboardInterrupt on main thread if SIGINT so caller stack unrolls cleanly
        if signum == getattr(signal, "SIGINT", 2):
            raise KeyboardInterrupt()
        sys.exit(0)

    def install_signal_handlers(self) -> None:
        """Install signal handlers for SIGINT, SIGTERM, and SIGBREAK (Windows)."""
        if self._installed:
            return

        # Signals can only be registered in the main thread
        if threading.current_thread() is not threading.main_thread():
            return

        for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, self._signal_handler)
                except (ValueError, OSError):
                    pass

        try:
            atexit.register(self._on_atexit)
        except Exception:
            pass

        self._installed = True

    def _on_atexit(self) -> None:
        """Ensure cleanup runs if the interpreter exits normally or unexpectedly."""
        if not self.is_shutting_down:
            self.request_shutdown(reason="atexit", hard_deadline_seconds=2.0)


# Global singleton instance
shutdown_coordinator = ShutdownCoordinator()
