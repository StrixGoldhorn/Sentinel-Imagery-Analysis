"""Tests for distributed leader election and scheduler web decoupling."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from sentinel_analysis.infrastructure.scheduler.leader import (
    LeaderElection,
    generate_worker_identity,
)
from sentinel_analysis.infrastructure.scheduler.pass_scheduler import PassSchedulerWorker


class MockDBLeaseRepo:
    """Mock repository mimicking try_acquire_scheduler_lease and release_scheduler_lease."""

    def __init__(self):
        self.leases = {}  # name -> (owner_id, expires_at)

    def try_acquire_scheduler_lease(self, name: str, owner_id: str, now: datetime, ttl_seconds: float = 60.0) -> bool:
        current = self.leases.get(name)
        if current is not None:
            curr_owner, curr_expires = current
            if curr_owner != owner_id and now < curr_expires:
                return False
        expires = now + timedelta(seconds=ttl_seconds)
        self.leases[name] = (owner_id, expires)
        return True

    def release_scheduler_lease(self, name: str, owner_id: str) -> None:
        current = self.leases.get(name)
        if current is not None and current[0] == owner_id:
            del self.leases[name]


class StubUseCases:
    def __init__(self):
        self.executed = 0

    def execute(self, *args, **kwargs):
        self.executed += 1
        return []


class TestLeaderElection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lock_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_worker_identity(self):
        id1 = generate_worker_identity("worker")
        id2 = generate_worker_identity("worker")
        self.assertTrue(id1.startswith("worker:"))
        self.assertNotEqual(id1, id2)

    def test_file_backed_single_leader_election(self):
        leader1 = LeaderElection(name="test_job", lock_dir=self.lock_dir, owner_id="node1", ttl_seconds=10.0)
        leader2 = LeaderElection(name="test_job", lock_dir=self.lock_dir, owner_id="node2", ttl_seconds=10.0)

        # First claimant acquires leadership
        self.assertTrue(leader1.acquire())
        self.assertTrue(leader1.is_leader)

        # Competing claimant cannot acquire while node1 holds active lease
        self.assertFalse(leader2.acquire())
        self.assertFalse(leader2.is_leader)

        # Node1 renews successfully
        self.assertTrue(leader1.acquire())

        # Node1 releases leadership
        leader1.release()
        self.assertFalse(leader1.is_leader)

        # Now node2 can acquire
        self.assertTrue(leader2.acquire())
        self.assertTrue(leader2.is_leader)
        leader2.release()

    def test_file_backed_lease_ttl_expiration(self):
        short_ttl = 0.5
        leader1 = LeaderElection(name="test_ttl", lock_dir=self.lock_dir, owner_id="node1", ttl_seconds=short_ttl)
        leader2 = LeaderElection(name="test_ttl", lock_dir=self.lock_dir, owner_id="node2", ttl_seconds=short_ttl)

        self.assertTrue(leader1.acquire())
        self.assertFalse(leader2.acquire())

        # Wait for lease to expire past TTL
        time.sleep(0.6)

        # Node2 should now take over leadership
        self.assertTrue(leader2.acquire())
        self.assertTrue(leader2.is_leader)
        leader2.release()

    def test_db_backed_leader_election(self):
        repo = MockDBLeaseRepo()
        leader1 = LeaderElection(name="db_sched", post_pass_repo=repo, owner_id="w1", ttl_seconds=30.0)
        leader2 = LeaderElection(name="db_sched", post_pass_repo=repo, owner_id="w2", ttl_seconds=30.0)

        self.assertTrue(leader1.acquire())
        self.assertFalse(leader2.acquire())

        leader1.release()
        self.assertFalse(leader1.is_leader)
        self.assertTrue(leader2.acquire())
        self.assertTrue(leader2.is_leader)
        leader2.release()

    def test_heartbeat_loop_and_callbacks(self):
        events = []

        def on_acq():
            events.append("acquired")

        def on_lost():
            events.append("lost")

        leader = LeaderElection(
            name="hb_test",
            lock_dir=self.lock_dir,
            owner_id="hb_node",
            ttl_seconds=5.0,
            heartbeat_interval=0.2,
            on_leadership_acquired=on_acq,
            on_leadership_lost=on_lost,
        )

        leader.start_heartbeat_loop()
        time.sleep(0.4)
        self.assertTrue(leader.is_leader)
        self.assertIn("acquired", events)

        leader.stop_heartbeat_loop()
        self.assertFalse(leader.is_leader)
        self.assertIn("lost", events)

    def test_pass_scheduler_worker_standby_mode(self):
        repo = MockDBLeaseRepo()
        # Seed an active lease for another worker
        now = datetime.now(timezone.utc)
        repo.leases["pass_scheduler"] = ("foreign_worker", now + timedelta(seconds=60))

        use_case = StubUseCases()
        worker = PassSchedulerWorker(
            schedule_use_case=use_case,
            post_pass_repo=repo,
            api_key="key",
            use_apscheduler=False,
        )

        # Worker starts
        worker._acquire_leadership()
        self.assertFalse(worker.is_leader)

        # AOI check and SAR scans should NOT execute while on standby
        worker._run_aoi_check_cycle()
        sar_res = worker._poll_due_post_pass_jobs()
        self.assertEqual(use_case.executed, 0)
        self.assertEqual(sar_res, [])

        # Status reflects STANDBY
        status = worker.get_status()
        self.assertFalse(status["is_leader"])
        worker.stop()


class TestWebSchedulerDecoupling(unittest.TestCase):
    def test_web_app_respects_sentinel_run_scheduler_env(self):
        from sentinel_analysis.bootstrap.config import Settings
        from sentinel_analysis.bootstrap.container import ApplicationContainer
        from sentinel_analysis.interfaces.web.application import create_app

        settings = Settings.from_environment()
        container = ApplicationContainer(settings)

        # Explicitly disable scheduler via environment variable
        os.environ["SENTINEL_RUN_SCHEDULER"] = "false"
        try:
            app = create_app(container=container, start_background_workers=True)
            # Pass scheduler must NOT be running
            self.assertFalse(container.pass_scheduler._running)
        finally:
            os.environ.pop("SENTINEL_RUN_SCHEDULER", None)


if __name__ == "__main__":
    unittest.main()
