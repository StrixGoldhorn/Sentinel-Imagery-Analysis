"""HTTP-boundary tests for the Flask application factory and routes."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from sentinel_analysis.application.exceptions import ExternalServiceError, NoImageryFoundError, ScanNotFoundError
from sentinel_analysis.application.ports.detection import DetectionResult
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import Acquisition, BackgroundTask, BoundingBox, Scan
from sentinel_analysis.interfaces.web.application import create_app


RUNTIME = Path(__file__).resolve().parent / "runtime" / "web"
BBOX = BoundingBox(103, 1, 104, 2)


class StubUseCase:
    def __init__(self, result=None, error=None, analysis_result=None):
        self.result = result
        self.error = error
        self.analysis_result = analysis_result
        self.calls = []
        self.keyword_calls = []

    def execute(self, *args, **kwargs):
        self.calls.append(args)
        self.keyword_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    def execute_with_analysis(self, *args, **kwargs):
        self.calls.append(args)
        self.keyword_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.analysis_result is not None:
            return self.analysis_result
        return {
            "predictions": self.result if isinstance(self.result, list) else [],
            "n2yo_predictions": [{"time": "2026-09-01T12:00:00Z", "max_elevation": 60, "source": "N2YO"}],
            "historical_predictions": [{"time": "2026-09-02T10:00:00Z", "max_elevation": 70, "source": "HISTORICAL_MISSION"}],
            "next_scan": "2026-09-01T12:00:00Z",
            "mission_analysis": {"total_acquisitions": 10},
        }



class StubTaskQueue:
    def __init__(self):
        self.tasks = {}

    def submit(self, task_type, scan_id, func):
        task = BackgroundTask(task_id="task_123", task_type=task_type, scan_id=scan_id, status="PENDING")
        self.tasks["task_123"] = task
        return task

    def get_task(self, task_id):
        return self.tasks.get(task_id)


class StubContainer:
    def __init__(self, settings, scan):
        self.settings = settings
        self.create_scan = StubUseCase(scan)
        self.detect_ships = StubUseCase(DetectionResult([], 10, 10))
        self.get_scan = StubUseCase(scan)
        self.list_scans = StubUseCase([scan])
        self.rename_scan = StubUseCase(None)
        self.delete_scan = StubUseCase(None)
        self.list_aois = StubUseCase([])
        self.add_aoi = StubUseCase(1)
        self.predict_aoi = StubUseCase([])
        self.ingest_ais = StubUseCase({"total_inserted": 0, "logs": []})
        self.get_vessels = StubUseCase([
            {
                "vessel_id": 1,
                "imo": "9123456",
                "mmsi": "563000111",
                "name": "PACIFIC TRADER",
                "type": "Cargo",
                "callsign": "9V123",
                "latitude": 1.25,
                "longitude": 103.85,
                "speed": 12.5,
                "heading": 90.0,
                "timestamp": "2026-08-30T10:00:00+00:00",
                "source_plugin": "MockAISPlugin",
            }
        ])
        self.scrape_aoi_ais = StubUseCase({"total_inserted": 8, "logs": []})
        self.analyze_mission_passes = StubUseCase({"total_acquisitions": 0, "passes": []})
        self.get_upcoming_scrapes = StubUseCase({
            "events": [
                {
                    "aoi_id": 1,
                    "aoi_name": "Singapore Strait",
                    "bbox": [103.0, 1.0, 104.0, 2.0],
                    "auto_capture_enabled": True,
                    "pass_time": "2026-09-02T10:00:00+00:00",
                    "window_start": "2026-09-02T09:55:00+00:00",
                    "window_end": "2026-09-02T10:05:00+00:00",
                    "satellite": "Sentinel-1A",
                    "orbit_direction": "ASCENDING",
                    "relative_orbit": 171,
                    "confidence_score": 0.95,
                    "max_elevation": 65.0,
                    "source": "COMBINED",
                    "historical_match": "Orbit #171",
                    "status": "SCHEDULED",
                    "is_active": False,
                    "seconds_until_pass": 3600,
                }
            ],
            "metrics": {
                "total_aois": 1,
                "auto_capture_count": 1,
                "total_upcoming_scrapes": 1,
                "upcoming_24h_count": 1,
                "upcoming_7d_count": 1,
                "active_flypasts_count": 0,
            },
            "generated_at": "2026-09-01T12:00:00+00:00",
        })
        self.schedule_aois = StubUseCase([{"aoi_id": 1, "status": "SCHEDULED"}])
        self.list_scrapers = StubUseCase({
            "scrapers": [
                {
                    "name": "VesselFinderPlugin",
                    "display_name": "VesselFinder (Playwright Stealth)",
                    "category": "Live Web Scraper",
                    "description": "Stealth browser scraping",
                    "requires_network": True,
                    "enabled": True,
                    "total_runs": 10,
                    "total_records": 100,
                    "success_runs": 9,
                    "failed_runs": 1,
                    "success_rate": 90.0,
                    "last_run_at": "2026-09-01T12:00:00Z",
                }
            ],
            "metrics": {
                "total_scrapers": 1,
                "active_scrapers": 1,
                "total_records_ingested": 100,
                "overall_success_rate": 90.0,
                "total_runs": 10,
            },
        })
        self.toggle_scraper = StubUseCase({"plugin_name": "VesselFinderPlugin", "enabled": False})
        self.update_scraper_config = StubUseCase({"plugin_name": "VesselFinderPlugin", "config": {"proxy_url": "http://127.0.0.1:8080"}})
        self.reset_scraper_cooldown = StubUseCase({"plugin_name": "VesselFinderPlugin", "status": "reset"})
        self.get_scraper_logs_use_case = StubUseCase({
            "logs": [
                {
                    "id": 1,
                    "plugin_name": "VesselFinderPlugin",
                    "status": "SUCCESS",
                    "records_inserted": 25,
                    "timestamp": "2026-09-01T12:00:00Z",
                    "error_message": None,
                }
            ],
            "count": 1,
            "stats": {"VesselFinderPlugin": {"total_runs": 1, "total_records": 25}},
            "metrics": {"total_runs": 1, "total_records": 25, "overall_success_rate": 100.0},
        })
        self.task_queue = StubTaskQueue()
        self.aoi_repository = None
        self.ais_repository = None
        self.pass_scheduler = None





def _get_test_context():
    output_root = RUNTIME / "output"
    image_path = output_root / "scan_1" / "images" / "scan.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (10, 10), (255, 255, 255, 255)).save(image_path)
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        database_path=RUNTIME / "web.db",
        output_root=output_root,
        copernicus_username=None,
        copernicus_password=None,
        n2yo_api_key="key",
    )
    scan = Scan(
        "scan_1",
        BBOX,
        Acquisition(datetime(2026, 8, 27, tzinfo=timezone.utc), "Sentinel-1", "sar"),
        str(image_path),
        {"custom_name": "Test scan"},
    )
    return settings, scan


def make_client():
    settings, scan = _get_test_context()
    container = StubContainer(settings, scan)
    app = create_app(container=container)
    app.config["TESTING"] = True
    return app.test_client(), container, settings, scan


def test_factory_uses_injected_container_settings_and_rejects_mismatch() -> None:
    settings, scan = _get_test_context()
    container = StubContainer(settings, scan)
    app = create_app(container=container)

    assert app.extensions["sentinel_container"] is container
    different = Settings(
        project_root=settings.project_root,
        database_path=settings.database_path,
        output_root=settings.output_root,
        copernicus_username=None,
        copernicus_password=None,
        n2yo_api_key=None,
    )
    try:
        create_app(different, container)
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_json_boundary_rejects_malformed_non_object_and_wrong_media_type() -> None:
    client, _, _, _ = make_client()

    assert client.post("/scan", data="{", content_type="application/json").status_code == 400
    assert client.post("/scan", json=[]).status_code == 400
    assert client.post("/scan", json={"bbox": None}).status_code == 400
    assert client.post("/scan", data="bbox=1").status_code == 415


def test_create_scan_returns_created_response_and_external_media_url() -> None:
    client, _, _, _ = make_client()

    response = client.post("/scan", json={"bbox": BBOX.as_list()})

    assert response.status_code == 201
    assert response.json["imageUrl"] == "/media/scans/scan_1/images/scan.png"
    media = client.get(response.json["imageUrl"])
    try:
        assert media.status_code == 200
        assert media.mimetype == "image/png"
    finally:
        media.close()


def test_expected_failures_are_mapped_to_stable_http_statuses() -> None:
    client, container, _, _ = make_client()
    container.create_scan.error = NoImageryFoundError("none found")
    assert client.post("/scan", json={"bbox": BBOX.as_list()}).status_code == 404

    container.create_scan.error = ExternalServiceError("provider down")
    assert client.post("/scan", json={"bbox": BBOX.as_list()}).status_code == 502

    container.get_scan.error = ScanNotFoundError("missing")
    assert client.get("/api/scan/missing").status_code == 404


def test_unexpected_errors_are_logged_but_not_exposed() -> None:
    from unittest.mock import patch

    client, container, _, _ = make_client()
    container.create_scan.error = RuntimeError("sensitive implementation detail")

    with patch("logging.Logger.exception"):
        response = client.post("/scan", json={"bbox": BBOX.as_list()})

    assert response.status_code == 500
    assert response.json == {"error": "Internal server error"}
    assert "sensitive" not in response.get_data(as_text=True)


def test_route_fields_are_strictly_typed_and_folder_names_are_not_rewritten() -> None:
    client, _, _, _ = make_client()

    assert client.post("/api/update_metadata/scan_1", json={"custom_name": 42}).status_code == 400
    assert client.post("/api/run_cv/scan_1", json={"threshold": True}).status_code == 400
    assert client.post("/api/run_cv/scan_1", json={"threshold": 40.5}).status_code == 400
    assert client.get("/api/scan/bad%20name").status_code == 400


def test_aoi_and_ais_routes_apply_request_contracts() -> None:
    client, container, _, _ = make_client()

    created = client.post("/api/aoi", json={"name": " Harbour ", "bbox": BBOX.as_list()})
    assert created.status_code == 201
    assert container.add_aoi.calls[0][0] == "Harbour"
    assert client.post("/api/aoi", json={"name": 3, "bbox": BBOX.as_list()}).status_code == 400
    assert client.post("/api/ingest_ais", json={"bbox": BBOX.as_list(), "plugin": 3}).status_code == 400


def test_security_headers_are_applied_to_json_responses() -> None:
    client, _, _, _ = make_client()

    response = client.get("/api/aoi")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Cache-Control"] == "no-store"


def test_async_task_and_crop_routes() -> None:
    client, _, _, _ = make_client()

    # Submit async task
    async_res = client.post("/api/tasks/scan", json={"bbox": BBOX.as_list()})
    assert async_res.status_code == 202
    task_id = async_res.json["task_id"]
    assert task_id == "task_123"

    # Get task status
    status_res = client.get(f"/api/tasks/{task_id}")
    assert status_res.status_code == 200
    assert status_res.json["task_id"] == "task_123"

    # Get detection crop
    crop_res = client.get("/api/scan/scan_1/crop?x=0&y=0&width=5&height=5&padding=2")
    assert crop_res.status_code == 200
    assert "data_uri" in crop_res.json
    assert "stats" in crop_res.json


def test_templates_escape_dynamic_values_in_browser_generated_markup() -> None:
    client, _, settings, _ = make_client()
    notifs = (settings.project_root / "static" / "js" / "notifications.js").read_text(encoding="utf-8")
    app_js = (settings.project_root / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert client.get("/gallery").status_code == 200
    assert "function escapeHtml(value)" in notifs
    assert "div.textContent = item.display_name" in app_js
    assert "div.innerHTML = item.display_name" not in app_js


def test_delete_scan_route_success_and_error_handling() -> None:
    client, container, _, _ = make_client()

    # Successful DELETE
    res = client.delete("/api/scan/scan_1")
    assert res.status_code == 200
    assert res.json["status"] == "success"
    assert container.delete_scan.calls[-1] == ("scan_1",)

    # Successful POST /api/scan/<folder>/delete
    res_post = client.post("/api/scan/scan_1/delete")
    assert res_post.status_code == 200
    assert res_post.json["status"] == "success"

    # Scan not found -> 404
    container.delete_scan.error = ScanNotFoundError("missing")
    res_404 = client.delete("/api/scan/missing")
    assert res_404.status_code == 404

    # Invalid folder name -> 400
    res_400 = client.delete("/api/scan/invalid%20name")
    assert res_400.status_code == 400


def test_aois_page_route_renders_successfully() -> None:
    client, _, _, _ = make_client()
    response = client.get("/aois")
    assert response.status_code == 200
    assert "Areas of Interest" in response.get_data(as_text=True)
    assert "/static/js/aois_page.js" in response.get_data(as_text=True)


def test_force_ais_scan_route() -> None:
    client, container, _, _ = make_client()
    response = client.post("/api/aoi/1/force_ais_scan", json={"force": True})
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["forced"] is True
    assert container.scrape_aoi_ais.calls[-1] == (1,)
    assert container.scrape_aoi_ais.keyword_calls[-1].get("force_now") is True


def test_list_vessels_route() -> None:
    client, container, _, _ = make_client()
    response = client.get("/api/ais/vessels?bbox=103.8,1.2,103.9,1.3&latest_only=true&limit=50")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["count"] == 1
    assert response.json["vessels"][0]["name"] == "PACIFIC TRADER"
    assert response.json["vessels"][0]["type"] == "Cargo"
    assert container.get_vessels.keyword_calls[-1].get("latest_only") is True



def test_ais_timeline_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/api/ais/timeline")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert "min_timestamp" in response.json
    assert "max_timestamp" in response.json
    assert "total_records" in response.json
    assert "count" in response.json


def test_predict_aoi_route_returns_n2yo_and_historical_lists() -> None:
    client, container, _, _ = make_client()
    response = client.post("/api/aoi/1/predict")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert "n2yo_predictions" in response.json
    assert "historical_predictions" in response.json
    assert len(response.json["n2yo_predictions"]) == 1
    assert len(response.json["historical_predictions"]) == 1
    assert response.json["n2yo_predictions"][0]["source"] == "N2YO"
    assert response.json["historical_predictions"][0]["source"] == "HISTORICAL_MISSION"


def test_predict_aoi_route_supports_refresh_flag_and_cache_metadata() -> None:
    client, container, _, _ = make_client()
    container.predict_aoi = StubUseCase(
        analysis_result={
            "predictions": [{"time": "2026-09-01T12:00:00Z", "source": "COMBINED"}],
            "n2yo_predictions": [{"time": "2026-09-01T12:00:00Z", "source": "N2YO"}],
            "historical_predictions": [],
            "next_scan": "2026-09-01T12:00:00Z",
            "cached": True,
            "fetched_at": "2026-09-01T11:00:00Z",
            "expires_at": "2026-09-01T12:00:00Z",
        }
    )

    # Normal fetch returning cached data
    res = client.post("/api/aoi/1/predict")
    assert res.status_code == 200
    assert res.json["cached"] is True
    assert res.json["fetched_at"] == "2026-09-01T11:00:00Z"
    assert container.predict_aoi.keyword_calls[-1].get("force_refresh") is False

    # Refresh query parameter
    res_refresh = client.post("/api/aoi/1/predict?refresh=true")
    assert res_refresh.status_code == 200
    assert container.predict_aoi.keyword_calls[-1].get("force_refresh") is True



def test_schedule_page_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/schedule")
    assert response.status_code == 200
    assert b"Planned Satellite Scrapes" in response.data


def test_upcoming_scrapes_route() -> None:
    client, container, _, _ = make_client()
    response = client.get("/api/schedule/upcoming?days_ahead=7&auto_capture_only=true")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert len(response.json["events"]) == 1
    assert response.json["events"][0]["aoi_name"] == "Singapore Strait"
    assert response.json["metrics"]["upcoming_24h_count"] == 1


def test_scheduler_status_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/api/schedule/status")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert "scheduler" in response.json
    assert "is_running" in response.json["scheduler"]


def test_scraper_logs_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/api/schedule/logs?limit=10")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert "logs" in response.json


def test_trigger_schedule_poll_route() -> None:
    client, container, _, _ = make_client()
    response = client.post("/api/schedule/trigger_poll")
    assert response.status_code == 200
    assert response.json["status"] == "success"
def test_scrapers_dashboard_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/scrapers")
    assert response.status_code == 200
    assert b"AIS Scraper Plugins" in response.data


def test_list_scrapers_api_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/api/scrapers")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert len(response.json["scrapers"]) == 1
    assert response.json["scrapers"][0]["name"] == "VesselFinderPlugin"
    assert response.json["metrics"]["active_scrapers"] == 1


def test_toggle_scraper_api_route() -> None:
    client, container, _, _ = make_client()
    response = client.post("/api/scrapers/VesselFinderPlugin/toggle", json={"enabled": False})
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["enabled"] is False


def test_logs_dashboard_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/logs")
    assert response.status_code == 200
    assert b"Scraper Execution Logs" in response.data


def test_logs_api_route() -> None:
    client, _, _, _ = make_client()
    response = client.get("/api/logs?limit=25")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert len(response.json["logs"]) == 1
    assert response.json["logs"][0]["plugin_name"] == "VesselFinderPlugin"


def test_update_scraper_config_api_route() -> None:
    client, _, _, _ = make_client()
    response = client.post(
        "/api/scrapers/VesselFinderPlugin/config",
        json={"proxy_url": "http://127.0.0.1:8080", "timeout": 45},
    )
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["plugin_name"] == "VesselFinderPlugin"


def test_reset_scraper_cooldown_api_route() -> None:
    client, _, _, _ = make_client()
    response = client.post("/api/scrapers/VesselFinderPlugin/reset_cooldown")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert response.json["plugin_name"] == "VesselFinderPlugin"


def load_tests(loader, standard_tests, pattern):



    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()



