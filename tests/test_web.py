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
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def execute(self, *args):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.result


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
        self.task_queue = StubTaskQueue()
        self.aoi_repository = None
        self.pass_scheduler = None



class WebInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        output_root = RUNTIME / "output"
        image_path = output_root / "scan_1" / "images" / "scan.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (10, 10), (255, 255, 255, 255)).save(image_path)
        cls.settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=RUNTIME / "web.db",
            output_root=output_root,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key="key",
        )
        cls.scan = Scan(
            "scan_1",
            BBOX,
            Acquisition(datetime(2026, 8, 27, tzinfo=timezone.utc), "Sentinel-1", "sar"),
            str(image_path),
            {"custom_name": "Test scan"},
        )

    def make_client(self):
        container = StubContainer(self.settings, self.scan)
        app = create_app(container=container)
        app.config["TESTING"] = True
        return app.test_client(), container

    def test_factory_uses_injected_container_settings_and_rejects_mismatch(self) -> None:
        container = StubContainer(self.settings, self.scan)
        app = create_app(container=container)

        self.assertIs(app.extensions["sentinel_container"], container)
        different = Settings(
            project_root=self.settings.project_root,
            database_path=self.settings.database_path,
            output_root=self.settings.output_root,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
        )
        with self.assertRaises(ValueError):
            create_app(different, container)

    def test_json_boundary_rejects_malformed_non_object_and_wrong_media_type(self) -> None:
        client, _ = self.make_client()

        self.assertEqual(client.post("/scan", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(client.post("/scan", json=[]).status_code, 400)
        self.assertEqual(client.post("/scan", json={"bbox": None}).status_code, 400)
        self.assertEqual(client.post("/scan", data="bbox=1").status_code, 415)

    def test_create_scan_returns_created_response_and_external_media_url(self) -> None:
        client, _ = self.make_client()

        response = client.post("/scan", json={"bbox": BBOX.as_list()})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["imageUrl"], "/media/scans/scan_1/images/scan.png")
        media = client.get(response.json["imageUrl"])
        try:
            self.assertEqual(media.status_code, 200)
            self.assertEqual(media.mimetype, "image/png")
        finally:
            media.close()

    def test_expected_failures_are_mapped_to_stable_http_statuses(self) -> None:
        client, container = self.make_client()
        container.create_scan.error = NoImageryFoundError("none found")
        self.assertEqual(client.post("/scan", json={"bbox": BBOX.as_list()}).status_code, 404)

        container.create_scan.error = ExternalServiceError("provider down")
        self.assertEqual(client.post("/scan", json={"bbox": BBOX.as_list()}).status_code, 502)

        container.get_scan.error = ScanNotFoundError("missing")
        self.assertEqual(client.get("/api/scan/missing").status_code, 404)

    def test_unexpected_errors_are_logged_but_not_exposed(self) -> None:
        client, container = self.make_client()
        container.create_scan.error = RuntimeError("sensitive implementation detail")

        response = client.post("/scan", json={"bbox": BBOX.as_list()})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json, {"error": "Internal server error"})
        self.assertNotIn("sensitive", response.get_data(as_text=True))

    def test_route_fields_are_strictly_typed_and_folder_names_are_not_rewritten(self) -> None:
        client, _ = self.make_client()

        self.assertEqual(
            client.post("/api/update_metadata/scan_1", json={"custom_name": 42}).status_code,
            400,
        )
        self.assertEqual(client.post("/api/run_cv/scan_1", json={"threshold": True}).status_code, 400)
        self.assertEqual(client.post("/api/run_cv/scan_1", json={"threshold": 40.5}).status_code, 400)
        self.assertEqual(client.get("/api/scan/bad%20name").status_code, 400)

    def test_aoi_and_ais_routes_apply_request_contracts(self) -> None:
        client, container = self.make_client()

        created = client.post("/api/aoi", json={"name": " Harbour ", "bbox": BBOX.as_list()})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(container.add_aoi.calls[0][0], "Harbour")
        self.assertEqual(client.post("/api/aoi", json={"name": 3, "bbox": BBOX.as_list()}).status_code, 400)
        self.assertEqual(
            client.post("/api/ingest_ais", json={"bbox": BBOX.as_list(), "plugin": 3}).status_code,
            400,
        )

    def test_security_headers_are_applied_to_json_responses(self) -> None:
        client, _ = self.make_client()

        response = client.get("/api/aoi")

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_async_task_and_crop_routes(self) -> None:
        client, _ = self.make_client()

        # Submit async task
        async_res = client.post("/api/tasks/scan", json={"bbox": BBOX.as_list()})
        self.assertEqual(async_res.status_code, 202)
        task_id = async_res.json["task_id"]
        self.assertEqual(task_id, "task_123")

        # Get task status
        status_res = client.get(f"/api/tasks/{task_id}")
        self.assertEqual(status_res.status_code, 200)
        self.assertEqual(status_res.json["task_id"], "task_123")

        # Get detection crop
        crop_res = client.get("/api/scan/scan_1/crop?x=0&y=0&width=5&height=5&padding=2")
        self.assertEqual(crop_res.status_code, 200)
        self.assertIn("data_uri", crop_res.json)
        self.assertIn("stats", crop_res.json)

    def test_templates_escape_dynamic_values_in_browser_generated_markup(self) -> None:
        client, _ = self.make_client()
        notifs = (self.settings.project_root / "static" / "js" / "notifications.js").read_text(encoding="utf-8")
        app_js = (self.settings.project_root / "static" / "js" / "app.js").read_text(encoding="utf-8")

        self.assertEqual(client.get("/gallery").status_code, 200)
        self.assertIn("function escapeHtml(value)", notifs)
        self.assertIn("div.textContent = item.display_name", app_js)
        self.assertNotIn("div.innerHTML = item.display_name", app_js)

    def test_delete_scan_route_success_and_error_handling(self) -> None:
        client, container = self.make_client()

        # Successful DELETE
        res = client.delete("/api/scan/scan_1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json["status"], "success")
        self.assertEqual(container.delete_scan.calls[-1], ("scan_1",))

        # Successful POST /api/scan/<folder>/delete
        res_post = client.post("/api/scan/scan_1/delete")
        self.assertEqual(res_post.status_code, 200)
        self.assertEqual(res_post.json["status"], "success")

        # Scan not found -> 404
        container.delete_scan.error = ScanNotFoundError("missing")
        res_404 = client.delete("/api/scan/missing")
        self.assertEqual(res_404.status_code, 404)

        # Invalid folder name -> 400
        res_400 = client.delete("/api/scan/invalid%20name")
        self.assertEqual(res_400.status_code, 400)


if __name__ == "__main__":
    unittest.main()

