"""Unit and integration tests for AOI historical backfill and baseline modeling."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.application.use_cases.backfill_aoi_baseline import (
    BackfillAoiBaseline,
    GetAoiBaseline,
)
from sentinel_analysis.domain.aoi_baseline import (
    AOIBaseline,
    ClutterBaseline,
    PersistentScatterer,
    haversine_distance_m,
)
from sentinel_analysis.domain.entities import Acquisition, AreaOfInterest, BoundingBox, Scan
from sentinel_analysis.infrastructure.persistence.filesystem_aoi_baseline import (
    FileSystemAOIBaselineRepository,
)
from sentinel_analysis.interfaces.web.application import create_app
from sentinel_analysis.interfaces.web.security import rate_limiter


class InMemoryAOIRepository:
    def __init__(self, aois: list[AreaOfInterest] | None = None) -> None:
        self._aois = {a.id: a for a in (aois or [])}

    def get(self, aoi_id: int) -> AreaOfInterest | None:
        return self._aois.get(aoi_id)

    def list(self) -> list[AreaOfInterest]:
        return list(self._aois.values())

    def add(self, aoi: AreaOfInterest) -> int:
        aoi_id = len(self._aois) + 1
        new_aoi = AreaOfInterest(
            id=aoi_id,
            name=aoi.name,
            bbox=aoi.bbox,
            auto_capture_enabled=aoi.auto_capture_enabled,
        )
        self._aois[aoi_id] = new_aoi
        return aoi_id


class InMemoryScanRepository:
    def __init__(self, scans: list[Scan] | None = None) -> None:
        self._scans = {s.folder_name: s for s in (scans or [])}

    def list(self) -> list[Scan]:
        return list(self._scans.values())

    def get(self, folder_name: str) -> Scan | None:
        return self._scans.get(folder_name)


class TestBackfillAoiBaseline(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.baseline_repo = FileSystemAOIBaselineRepository(self.temp_dir / "baselines")
        self.bbox = BoundingBox(12.0, 55.0, 13.0, 56.0)
        self.aoi = AreaOfInterest(id=1, name="Kattegat North", bbox=self.bbox)
        self.aoi_repo = InMemoryAOIRepository([self.aoi])

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_haversine_distance(self) -> None:
        # Distance between two nearby coordinates
        d = haversine_distance_m(55.0, 12.0, 55.001, 12.0)
        self.assertAlmostEqual(d, 111.2, delta=5.0)
        # Same point gives 0.0
        self.assertEqual(haversine_distance_m(55.0, 12.0, 55.0, 12.0), 0.0)

    def test_domain_serialization(self) -> None:
        now = datetime.now(timezone.utc)
        ps = PersistentScatterer(
            id="ps_1",
            latitude=55.5,
            longitude=12.5,
            persistence_pct=0.8,
            observation_count=4,
            total_passes=5,
            category="OIL_RIG",
            average_length_m=120.0,
            first_seen=now - timedelta(days=20),
            last_seen=now,
        )
        cb = ClutterBaseline(
            mean_clutter_db=-21.0,
            std_clutter_db=2.5,
            p95_clutter_db=-16.8,
            recommended_threshold_db=-13.5,
            sample_count=5,
        )
        base = AOIBaseline(
            aoi_id=1,
            aoi_name="Kattegat North",
            bbox=self.bbox,
            created_at=now,
            start_date=now - timedelta(days=30),
            end_date=now,
            total_passes=5,
            persistent_scatterers=(ps,),
            clutter_baseline=cb,
            vessel_density_summary={"total_detections": 12},
            historical_pass_ids=("pass_1", "pass_2"),
        )
        d = base.to_dict()
        self.assertEqual(d["aoi_id"], 1)
        self.assertEqual(d["persistent_scatterers_count"], 1)
        self.assertEqual(d["persistent_scatterers"][0]["category"], "OIL_RIG")
        self.assertEqual(d["clutter_baseline"]["mean_clutter_db"], -21.0)

    def test_repository_save_get_delete(self) -> None:
        now = datetime.now(timezone.utc)
        cb = ClutterBaseline(-20.0, 2.0, -16.0, -14.0, 2)
        base = AOIBaseline(
            aoi_id=1,
            aoi_name="Test AOI",
            bbox=self.bbox,
            created_at=now,
            start_date=now - timedelta(days=10),
            end_date=now,
            total_passes=2,
            persistent_scatterers=(),
            clutter_baseline=cb,
            vessel_density_summary={},
            historical_pass_ids=("s1",),
        )
        self.baseline_repo.save(base)
        loaded = self.baseline_repo.get(1)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.aoi_id, 1)
        self.assertEqual(loaded.aoi_name, "Test AOI")

        all_baselines = self.baseline_repo.list_all()
        self.assertEqual(len(all_baselines), 1)

        self.baseline_repo.delete(1)
        self.assertIsNone(self.baseline_repo.get(1))

    def test_backfill_use_case_with_synthetic_scans(self) -> None:
        now = datetime.now(timezone.utc)
        # Create 3 scans over the AOI
        # Target 1 (Persistent Rig): at (55.500, 12.500), length 80m, present in all 3 scans
        # Target 2 (Transient ship): at (55.100, 12.100), present in scan 1 only
        # Target 3 (Transient ship): at (55.800, 12.800), present in scan 2 only
        scans = []
        for i in range(3):
            t = now - timedelta(days=(3 - i) * 10)
            dets = [
                {"latitude": 55.5001, "longitude": 12.5001, "length_m": 82.0, "confidence": 0.95, "rcs_db": -5.0}
            ]
            if i == 0:
                dets.append({"latitude": 55.100, "longitude": 12.100, "length_m": 45.0, "confidence": 0.88})
            elif i == 1:
                dets.append({"latitude": 55.800, "longitude": 12.800, "length_m": 120.0, "confidence": 0.90})

            scans.append(
                Scan(
                    folder_name=f"scan_pass_{i+1}",
                    image_path=str(self.temp_dir / f"scan_{i+1}.png"),
                    acquisition=Acquisition(acquired_at=t, satellite="SENTINEL-1A", product_type="GRD"),
                    bbox=self.bbox,
                    metadata={"detections": dets, "clutter_mean_db": -22.0, "clutter_std_db": 2.5},
                )
            )

        scan_repo = InMemoryScanRepository(scans)
        backfill = BackfillAoiBaseline(
            aoi_repository=self.aoi_repo,
            scan_repository=scan_repo,
            baseline_repository=self.baseline_repo,
        )

        result = backfill.execute(aoi_id=1, days_back=60, min_persistence_pct=0.5)

        self.assertEqual(result["aoi_id"], 1)
        self.assertEqual(result["total_passes"], 3)
        self.assertEqual(result["persistent_scatterers_count"], 1)
        ps = result["persistent_scatterers"][0]
        self.assertEqual(ps["category"], "OIL_RIG")
        self.assertEqual(ps["observation_count"], 3)
        self.assertAlmostEqual(ps["persistence_pct"], 1.0)
        self.assertAlmostEqual(ps["latitude"], 55.5001, places=3)
        self.assertEqual(result["vessel_density_summary"]["transient_vessels_detected"], 2)

        # Test GetAoiBaseline
        get_use_case = GetAoiBaseline(self.aoi_repo, self.baseline_repo)
        fetched = get_use_case.execute(1)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["aoi_id"], 1)

    def test_backfill_missing_aoi_raises(self) -> None:
        backfill = BackfillAoiBaseline(
            aoi_repository=self.aoi_repo,
            scan_repository=InMemoryScanRepository([]),
            baseline_repository=self.baseline_repo,
        )
        with self.assertRaises(AreaOfInterestNotFoundError):
            backfill.execute(999)


class TestWebAoiBaselineEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        rate_limiter.reset()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_baseline_endpoints_flow(self) -> None:
        # Create an AOI first
        res = self.client.post(
            "/api/aoi",
            json={
                "name": "Baseline Test Bay",
                "bbox": [10.0, 54.0, 11.0, 55.0],
                "auto_capture_enabled": False,
            },
        )
        self.assertEqual(res.status_code, 201)
        aoi_id = res.get_json()["id"]

        # 1. GET before backfill should return 404
        get_res = self.client.get(f"/api/aoi/{aoi_id}/baseline")
        self.assertEqual(get_res.status_code, 404)

        # 2. POST backfill
        post_res = self.client.post(
            f"/api/aoi/{aoi_id}/baseline/backfill",
            json={"days_back": 30, "min_persistence_pct": 0.4},
        )
        self.assertEqual(post_res.status_code, 201)
        payload = post_res.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["baseline"]["aoi_id"], aoi_id)

        # 3. GET after backfill should return 200
        get_after = self.client.get(f"/api/aoi/{aoi_id}/baseline")
        self.assertEqual(get_after.status_code, 200)
        data = get_after.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["baseline"]["aoi_id"], aoi_id)


if __name__ == "__main__":
    unittest.main()
