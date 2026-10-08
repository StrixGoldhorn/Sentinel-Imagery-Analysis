"""Distributed leader election and lease management for scheduler and background workers.

Ensures only a single worker instance acts as active leader for periodic AOI checks,
satellite flypast scraping, and SAR imagery ingestion, enabling safe multi-worker
web deployments (e.g. Gunicorn / uWSGI) and standalone decoupled scheduler workers.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any, Callable, Optional
import uuid

logger = logging.getLogger(__name__)


def generate_worker_identity(prefix: str = "worker") -> str:
    """Generate a globally unique process-level worker identifier."""
    hostname = socket.gethostname()
    pid = os.getpid()
    unique_suffix = uuid.uuid4().hex[:8]
    return f"{prefix}:{hostname}:{pid}:{unique_suffix}"


class LeaderElection:
    """Manages distributed lease-based leader election with TTL and heartbeat renewals.

    Supports database-backed leases via PostPassIngestionRepository / SQLite,
    with an automatic file-backed lease fallback for lightweight or standalone setups.
    """

    def __init__(
        self,
        name: str = "pass_scheduler",
        post_pass_repo: Optional[Any] = None,
        lock_dir: Optional[Path] = None,
        owner_id: Optional[str] = None,
        ttl_seconds: float = 60.0,
        heartbeat_interval: float = 20.0,
        on_leadership_acquired: Optional[Callable[[], None]] = None,
        on_leadership_lost: Optional[Callable[[], None]] = None,
    ) -> None:
        self.name = name
        self._repo = post_pass_repo
        self.owner_id = owner_id or generate_worker_identity(name)
        self.ttl_seconds = max(0.1, float(ttl_seconds))
        self.heartbeat_interval = max(0.05, float(heartbeat_interval))
        default_dir = os.environ.get("SENTINEL_LOCK_DIR")
        if lock_dir is not None:
            self._lock_dir = Path(lock_dir)
        elif default_dir:
            self._lock_dir = Path(default_dir)
        else:
            self._lock_dir = Path(".sentinel_leases")
        self._on_acquired = on_leadership_acquired
        self._on_lost = on_leadership_lost

        self._is_leader = False
        self._last_heartbeat: Optional[datetime] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._mutex = threading.Lock()

    @property
    def is_leader(self) -> bool:
        """Return True if this instance currently holds valid leadership."""
        with self._mutex:
            return self._is_leader

    def acquire(self) -> bool:
        """Attempt to acquire or renew the leadership lease."""
        with self._mutex:
            now = datetime.now(timezone.utc)
            acquired = False

            if self._repo is not None:
                if hasattr(self._repo, "try_acquire_scheduler_lease"):
                    try:
                        acquired = bool(
                            self._repo.try_acquire_scheduler_lease(
                                self.name,
                                self.owner_id,
                                now,
                                ttl_seconds=self.ttl_seconds,
                            )
                        )
                    except Exception as exc:
                        logger.warning("Database lease acquisition error for '%s': %s", self.name, exc)
                        acquired = False
                else:
                    # Non-distributed mock/stub repository, assume local leader
                    acquired = True
            else:
                # File-backed lease fallback
                acquired = self._file_try_acquire(now)

            was_leader = self._is_leader
            self._is_leader = acquired

            if acquired:
                self._last_heartbeat = now
                if not was_leader and self._on_acquired:
                    try:
                        self._on_acquired()
                    except Exception as exc:
                        logger.error("Error in on_leadership_acquired callback: %s", exc)
            else:
                if was_leader and self._on_lost:
                    try:
                        self._on_lost()
                    except Exception as exc:
                        logger.error("Error in on_leadership_lost callback: %s", exc)

            return acquired

    def heartbeat(self) -> bool:
        """Explicitly send a heartbeat renewal if currently leader."""
        return self.acquire()

    def release(self) -> None:
        """Voluntarily yield leadership and remove active lease."""
        with self._mutex:
            was_leader = self._is_leader
            self._is_leader = False

            if self._repo is not None and hasattr(self._repo, "release_scheduler_lease"):
                try:
                    self._repo.release_scheduler_lease(self.name, self.owner_id)
                except Exception as exc:
                    logger.warning("Failed to release DB lease for '%s': %s", self.name, exc)
            else:
                self._file_release()

            if was_leader and self._on_lost:
                try:
                    self._on_lost()
                except Exception as exc:
                    logger.error("Error in on_leadership_lost callback: %s", exc)

    def start_heartbeat_loop(self) -> None:
        """Start a background daemon thread that continually renews the lease."""
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._stop_event.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._run_heartbeat_loop,
            daemon=True,
            name=f"leader-heartbeat-{self.name}",
        )
        self._heartbeat_thread.start()

    def stop_heartbeat_loop(self) -> None:
        """Stop background heartbeat thread and release leadership."""
        self._stop_event.set()
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)
        self.release()

    def _run_heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.acquire()
            except Exception as exc:
                logger.warning("Heartbeat loop error for leader lease '%s': %s", self.name, exc)
            if self._stop_event.wait(timeout=self.heartbeat_interval):
                break

    def __enter__(self) -> "LeaderElection":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()

    # --- File-backed lease implementation ---

    def _get_lease_file(self) -> Optional[Path]:
        try:
            self._lock_dir.mkdir(parents=True, exist_ok=True)
            return self._lock_dir / f"{self.name}.lease.json"
        except OSError as exc:
            logger.warning("Could not create lock dir %s: %s", self._lock_dir, exc)
            return None

    def _file_try_acquire(self, now: datetime) -> bool:
        lease_file = self._get_lease_file()
        if lease_file is None:
            return True
        now_ts = now.timestamp()
        expires_ts = now_ts + self.ttl_seconds

        if lease_file.exists():
            try:
                data = json.loads(lease_file.read_text(encoding="utf-8"))
                current_owner = data.get("owner_id")
                current_expires = float(data.get("lease_expires_ts", 0.0))

                # If another owner holds a valid, non-expired lease, cannot acquire
                if current_owner != self.owner_id and now_ts < current_expires:
                    return False
            except Exception:
                # Corrupt or unreadable lease file, eligible for overwrite
                pass

        payload = {
            "name": self.name,
            "owner_id": self.owner_id,
            "heartbeat_ts": now_ts,
            "lease_expires_ts": expires_ts,
            "heartbeat_at": now.isoformat(),
            "lease_expires_at": (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
        }

        try:
            # Atomic file write using temporary file replacement
            tmp_path = lease_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(lease_file)
            return True
        except Exception as exc:
            logger.warning("Failed to write lease file '%s': %s", lease_file, exc)
            return False

    def _file_release(self) -> None:
        try:
            lease_file = self._get_lease_file()
            if lease_file is None or not lease_file.exists():
                return
            data = json.loads(lease_file.read_text(encoding="utf-8"))
            if data.get("owner_id") == self.owner_id:
                lease_file.unlink(missing_ok=True)
        except Exception:
            pass
