"""Standalone scheduler runner daemon.

Allows decoupling background AOI monitoring, satellite flypast tracking,
and post-pass SAR imagery ingestion from the web application workers.
Multiple runner instances can run concurrently; lease-based leader election
guarantees that only a single active leader executes scheduled checks while
other instances remain on hot standby.
"""

import argparse
import logging
import os
from pathlib import Path
import signal
import sys
import time
from typing import Optional

from sentinel_analysis.application.shutdown import shutdown_coordinator
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer

logger = logging.getLogger("sentinel_analysis.scheduler")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sentinel_analysis.scheduler",
        description="Run standalone Sentinel SAR background scheduler and flypast ingestion daemon.",
    )
    parser.add_argument(
        "--aoi-interval",
        type=float,
        default=None,
        help="Cadence in seconds between AOI flypast checks (default: from database settings or 30s).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of concurrent worker threads for post-pass SAR ingestion (default: 8).",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to data.db SQLite database file (default: from DATABASE_PATH or data.db).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose DEBUG logging.",
    )
    return parser.parse_args(argv)


def run_scheduler_daemon(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s (pid %(process)d): %(message)s",
    )

    logger.info("Initializing Sentinel Imagery Analysis scheduler daemon...")
    settings = Settings.from_environment()
    if args.db_path:
        object.__setattr__(settings, "database_path", Path(args.db_path).resolve())

    container = ApplicationContainer(settings)

    if args.aoi_interval is not None:
        container.pass_scheduler.set_aoi_check_interval(args.aoi_interval)
    if args.workers is not None and container.settings_repository is not None:
        container.settings_repository.set("scheduler", "post_pass_worker_count", max(1, args.workers))

    # Signal handling for clean shutdown
    stop_event = shutdown_coordinator.create_signal_handler(container.shutdown)

    def _sig_handler(signum: int, frame: object) -> None:
        logger.info("Received termination signal %d. Shutting down scheduler...", signum)
        shutdown_coordinator.request_shutdown()

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, AttributeError):
        pass  # Non-main thread or unsupported platform

    worker_id = container.pass_scheduler._owner_id
    backend = container.pass_scheduler.backend_type
    logger.info(
        "Starting PassSchedulerWorker (worker_id=%s, backend=%s, aoi_interval=%.1fs, sar_interval=%.1fs)",
        worker_id,
        backend,
        container.pass_scheduler.get_aoi_check_interval(),
        container.pass_scheduler.get_sar_scan_interval(),
    )

    container.pass_scheduler.start()

    try:
        while not shutdown_coordinator.is_shutting_down:
            status = container.pass_scheduler.get_status()
            logger.debug(
                "Scheduler status: health=%s, operational_status=%s, is_leader=%s",
                status.get("health"),
                status.get("operational_status"),
                status.get("is_leader"),
            )
            time.sleep(2.0)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
    finally:
        logger.info("Stopping scheduler daemon...")
        container.shutdown()
        logger.info("Scheduler daemon terminated cleanly.")

    return 0


def main() -> None:
    sys.exit(run_scheduler_daemon(sys.argv[1:]))


if __name__ == "__main__":
    main()
