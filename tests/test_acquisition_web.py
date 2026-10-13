"""Web-boundary integration tests for acquisition planning, revisit calendar, suitability scoring, and provider comparison."""

from datetime import datetime, timezone
import unittest
from pathlib import Path

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.interfaces.web.application import create_app


RUNTIME = Path(__file__).resolve().parent / "runtime" / "acquisition_web"
BBOX = BoundingBox(103.0, 1.0, 104.0, 2.0)


class TestAcquisitionWebEndpoints(unittest.TestCase):
    def setUp(self):
        project_root = Path(__file__).resolve().parent.parent
        settings = Settings(
            project_root=project_root,
            database_path=RUNTIME / "test_acq.db",
            output_root=RUNTIME / "output",
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key="TEST_API_KEY",
            debug=True,
        )
        self.container = ApplicationContainer(settings)
        # Seed an AOI
        self.aoi_id = self.container.aoi_repository.add(
            AreaOfInterest(id=1, name="Malacca Strait Patrol", bbox=BBOX, auto_capture_enabled=False)
        )
        self.app = create_app(settings, container=self.container, start_background_workers=False)
        self.client = self.app.test_client()

    def test_get_acquisition_planner_page(self):
        resp = self.client.get("/acquisition-planner")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Satellite Revisit Calendar", resp.data)
        self.assertIn(b"Best Upcoming Acquisition Opportunities", resp.data)
        self.assertIn(b"Provider Comparator", resp.data)

    def test_get_providers_catalog(self):
        resp = self.client.get("/api/providers/catalog")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["total_providers"], 6)
        provider_ids = [p["provider_id"] for p in data["providers"]]
        self.assertIn("sentinel-1", provider_ids)
        self.assertIn("sentinel-2", provider_ids)
        self.assertIn("umbra", provider_ids)
        self.assertIn("capella", provider_ids)

    def test_get_revisit_calendar_for_aoi(self):
        resp = self.client.get(f"/api/aoi/{self.aoi_id}/revisit-calendar?days_ahead=7")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("daily_matrix", data)
        self.assertIn("events", data)
        self.assertIn("revisit_metrics", data)
        self.assertGreaterEqual(len(data["events"]), 1)
        first_event = data["events"][0]
        self.assertIn("suitability_score", first_event)
        self.assertIn("modality", first_event)

    def test_get_revisit_calendar_all_aois(self):
        resp = self.client.get("/api/revisit-calendar?days_ahead=3")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("daily_matrix", data)
        self.assertIn("aoi_calendars", data)

    def test_get_best_opportunities(self):
        resp = self.client.get(
            f"/api/aoi/{self.aoi_id}/opportunities/best?horizon_hours=72&target_vessel_size_m=35&cost_preference=BALANCED"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["aoi_id"], self.aoi_id)
        self.assertIn("dispatch_guidance", data)
        self.assertIsNotNone(data.get("primary_recommendation"))
        primary = data["primary_recommendation"]
        self.assertIn("suitability_score", primary)
        self.assertGreaterEqual(primary["suitability_score"], 0)

    def test_compare_providers(self):
        resp = self.client.get(f"/api/providers/compare?aoi_id={self.aoi_id}&target_vessel_size_m=25")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["aoi_name"], "Malacca Strait Patrol")
        self.assertIn("comparisons", data)
        self.assertGreaterEqual(len(data["comparisons"]), 4)
        first_comp = data["comparisons"][0]
        self.assertIn("tactical_feasibility", first_comp)
        self.assertIn("suitability", first_comp)

    def test_evaluate_custom_suitability(self):
        payload = {
            "resolution_m": 0.5,
            "aoi_coverage_pct": 100.0,
            "incidence_angle_deg": 35.0,
            "orbit_direction": "descending",
            "modality": "SAR",
            "is_daylight": True,
            "metocean": {
                "wind_speed_mps": 6.5,
                "wave_height_m": 1.1,
                "cloud_cover_pct": 20.0,
            },
        }
        resp = self.client.post(
            f"/api/aoi/{self.aoi_id}/evaluate-suitability",
            json=payload,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("composite_score", data)
        self.assertIn("factor_scores", data)
        self.assertIn("strengths", data)
        self.assertGreaterEqual(data["composite_score"], 70)

    def test_dispatch_acquisition_opportunity(self):
        payload = {
            "provider_id": "sentinel1",
            "pass_time": datetime.now(timezone.utc).isoformat(),
            "notes": "Testing automated pass dispatch",
        }
        resp = self.client.post(
            f"/api/aoi/{self.aoi_id}/dispatch-opportunity",
            json=payload,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["dispatched"])
        self.assertTrue(data["auto_capture_enabled"])
        self.assertEqual(data["aoi_id"], self.aoi_id)


if __name__ == "__main__":
    unittest.main()
