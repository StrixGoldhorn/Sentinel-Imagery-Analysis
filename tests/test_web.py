"""HTTP-boundary tests for the Flask application factory and routes."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from sentinel_analysis.application.ports.detection import DetectionResult
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.exceptions import ExternalServiceError, NoImageryFoundError, ScanNotFoundError
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


class StubContainer:
    def __init__(self, settings, scan):
        self.settings = settings
        self.create_scan = StubUseCase(scan)
        self.detect_ships = StubUseCase(DetectionResult([], 10, 10))
        self.get_scan = StubUseCase(scan)
        self.list_scans = StubUseCase([scan])
        self.rename_scan = StubUseCase(None)
        self.list_aois = StubUseCase([])
        self.add_aoi = StubUseCase(1)
        self.predict_aoi = StubUseCase([])
        self.ingest_ais = StubUseCase({"total_inserted": 0, "logs": []})


class WebInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        output_root = RUNTIME / "output"
        image_path = output_root / "scan_1" / "images" / "scan.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (1, 1), (255, 255, 255, 255)).save(image_path)
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

    def test_templates_escape_dynamic_values_in_browser_generated_markup(self) -> None:
        client, _ = self.make_client()
        source = (self.settings.project_root / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertEqual(client.get("/gallery").status_code, 200)
        self.assertIn("function escapeHtml(value)", source)
        self.assertIn("div.textContent = item.display_name", source)
        self.assertNotIn("div.innerHTML = item.display_name", source)


if __name__ == "__main__":
    unittest.main()
