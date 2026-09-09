"""Unit tests for the graceful shutdown system."""

import inspect
import time
import unittest
from unittest.mock import MagicMock

from sentinel_analysis.application.shutdown import ShutdownCoordinator
from sentinel_analysis.infrastructure.scheduler.pass_scheduler import PassScheduler
from sentinel_analysis.infrastructure.satellite.hybrid_predictor import HybridPassPredictor


def test_shutdown_coordinator_lifecycle():
    coord = ShutdownCoordinator()
    assert not coord.is_shutting_down

    events = []

    def high_priority():
        events.append("high")

    def failing_priority():
        raise RuntimeError("Cleanup error")

    def low_priority():
        events.append("low")

    # Lower priority numbers run first
    coord.register_cleanup(high_priority, priority=10)
    coord.register_cleanup(failing_priority, priority=50)
    coord.register_cleanup(low_priority, priority=100)

    t0 = time.time()
    coord.request_shutdown(reason="test", enable_watchdog=False)
    elapsed = time.time() - t0

    assert coord.is_shutting_down
    assert elapsed < 1.0
    # Cleanups executed in ascending priority number order
    assert events == ["high", "low"]


def test_shutdown_coordinator_sleep():
    coord = ShutdownCoordinator()

    # When not shutting down, sleep waits specified duration
    interrupted = coord.sleep(0.05)
    assert not interrupted

    # When shutting down, sleep returns True immediately
    coord.request_shutdown(reason="test", enable_watchdog=False)
    t0 = time.time()
    interrupted = coord.sleep(5.0)
    elapsed = time.time() - t0

    assert interrupted is True
    assert elapsed < 0.2


def test_pass_scheduler_responsive_stop():
    mock_use_case = MagicMock()
    scheduler = PassScheduler(schedule_use_case=mock_use_case, api_key="dummy_key", use_apscheduler=False)
    # Start scheduler
    scheduler.start()
    assert scheduler._running

    # Stop scheduler and measure time
    t0 = time.time()
    scheduler.stop()
    elapsed = time.time() - t0

    assert not scheduler._running
    assert elapsed < 1.5


def test_hybrid_predictor_early_exit_on_shutdown():
    from sentinel_analysis.application.shutdown import shutdown_coordinator
    from sentinel_analysis.domain.entities import BoundingBox

    predictor = HybridPassPredictor()
    bbox = BoundingBox(0.0, 0.0, 1.0, 1.0)

    try:
        # Simulate active shutdown without starting watchdog process killer
        shutdown_coordinator.request_shutdown(reason="test", enable_watchdog=False)
        result = predictor.predict(bbox=bbox, api_key="dummy-key")
        assert result == []
    finally:
        shutdown_coordinator.reset()


def test_schedule_aois_early_exit_on_shutdown():
    from sentinel_analysis.application.shutdown import shutdown_coordinator
    from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs

    aoi_repo = MagicMock()
    mock_aoi = MagicMock()
    mock_aoi.id = 1
    mock_aoi.name = "Test AOI"
    mock_aoi.auto_capture_enabled = True
    aoi_repo.list.return_value = [mock_aoi]

    use_case = CheckAndScheduleAOIs(
        aoi_repository=aoi_repo,
        pass_predictor=MagicMock(),
    )

    try:
        shutdown_coordinator.request_shutdown(reason="test", enable_watchdog=False)
        results = use_case.execute(api_key="dummy-key")
        assert results == []
    finally:
        shutdown_coordinator.reset()


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()
