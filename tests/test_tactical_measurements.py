"""Unit tests for tactical geospatial and vessel measurement tools."""

from datetime import datetime, timezone
import math
from pathlib import Path
import tempfile
import unittest

from sentinel_analysis.application.use_cases.calculate_measurements import (
    CalculateTacticalMeasurements,
)
from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, Scan
from sentinel_analysis.domain.tactical_measurements import (
    AreaMeasurementResult,
    CorridorMeasurementResult,
    DistanceBearingResult,
    GeodesicPoint,
    PolylineMeasurementResult,
    VesselDimensionResult,
    classify_vessel_length,
    compass_cardinal_from_bearing,
    destination_point,
    generate_tactical_corridor,
    haversine_distance_m,
    initial_bearing_deg,
    measure_distance_and_bearing,
    measure_polygon_area,
    measure_polyline_path,
    measure_vessel_dimension_from_pixels,
    EARTH_RADIUS_M,
    METERS_PER_NAUTICAL_MILE,
)
from sentinel_analysis.interfaces.web.application import create_app


class FakeScanRepository:
    def __init__(self, scans: list[Scan]) -> None:
        self._scans = {s.folder_name: s for s in scans}

    def get(self, folder_name: str) -> Scan | None:
        return self._scans.get(folder_name)

    def list_recent(self, limit: int = 50) -> list[Scan]:
        return list(self._scans.values())[:limit]


class TestTacticalMeasurementsDomain(unittest.TestCase):
    """Test domain math engine, geodesic equations, and vessel algorithms."""

    def test_geodesic_point_validation(self) -> None:
        p = GeodesicPoint(1.290270, 103.851959)
        self.assertAlmostEqual(p.latitude, 1.290270)
        self.assertAlmostEqual(p.longitude, 103.851959)
        self.assertEqual(p.to_dict(), {"latitude": 1.29027, "longitude": 103.851959})
        self.assertEqual(p.to_geojson_coords(), [103.851959, 1.29027])

        with self.assertRaises(ValueError):
            GeodesicPoint(91.0, 0.0)
        with self.assertRaises(ValueError):
            GeodesicPoint(0.0, 185.0)

    def test_haversine_distance_equator(self) -> None:
        # 1 degree of latitude is approximately 111.195 km
        p1 = GeodesicPoint(0.0, 0.0)
        p2 = GeodesicPoint(1.0, 0.0)
        dist_m = haversine_distance_m(p1, p2)
        expected_m = (math.pi / 180.0) * EARTH_RADIUS_M
        self.assertAlmostEqual(dist_m, expected_m, delta=100.0)
        self.assertTrue(111_000 < dist_m < 112_000)

    def test_initial_bearing_and_compass_cardinals(self) -> None:
        p_origin = GeodesicPoint(0.0, 0.0)
        p_north = GeodesicPoint(1.0, 0.0)
        p_east = GeodesicPoint(0.0, 1.0)
        p_south = GeodesicPoint(-1.0, 0.0)
        p_west = GeodesicPoint(0.0, -1.0)

        # North
        b_north = initial_bearing_deg(p_origin, p_north)
        self.assertAlmostEqual(b_north, 0.0, delta=0.5)
        self.assertEqual(compass_cardinal_from_bearing(b_north), "N")

        # East
        b_east = initial_bearing_deg(p_origin, p_east)
        self.assertAlmostEqual(b_east, 90.0, delta=0.5)
        self.assertEqual(compass_cardinal_from_bearing(b_east), "E")

        # South
        b_south = initial_bearing_deg(p_origin, p_south)
        self.assertAlmostEqual(b_south, 180.0, delta=0.5)
        self.assertEqual(compass_cardinal_from_bearing(b_south), "S")

        # West
        b_west = initial_bearing_deg(p_origin, p_west)
        self.assertAlmostEqual(b_west, 270.0, delta=0.5)
        self.assertEqual(compass_cardinal_from_bearing(b_west), "W")

    def test_destination_point(self) -> None:
        p_start = GeodesicPoint(1.30, 103.80)
        # Move 10,000 meters East (90 deg)
        p_dest = destination_point(p_start, 10000.0, 90.0)
        # Verify calculated distance matches ~10,000 m
        calc_dist = haversine_distance_m(p_start, p_dest)
        self.assertAlmostEqual(calc_dist, 10000.0, delta=5.0)
        calc_bearing = initial_bearing_deg(p_start, p_dest)
        self.assertAlmostEqual(calc_bearing, 90.0, delta=0.5)

    def test_measure_distance_and_bearing(self) -> None:
        # Singapore to Malacca
        singapore = GeodesicPoint(1.290270, 103.851959)
        malacca = GeodesicPoint(2.1896, 102.2501)
        res = measure_distance_and_bearing(singapore, malacca)

        self.assertIsInstance(res, DistanceBearingResult)
        # Distance ~202 km (~109 NM)
        self.assertTrue(190.0 < res.distance_km < 220.0)
        self.assertTrue(100.0 < res.distance_nm < 120.0)
        # Heading towards North-West (~300 deg)
        self.assertTrue(290.0 < res.initial_bearing_deg < 320.0)
        self.assertIn("NW", res.compass_cardinal)

        d = res.to_dict()
        self.assertIn("meters", d["distance"])
        self.assertIn("initial_bearing_deg", d["bearing"])

    def test_measure_polyline_path(self) -> None:
        pts = [
            GeodesicPoint(1.0, 103.0),
            GeodesicPoint(1.2, 103.2),
            GeodesicPoint(1.5, 103.5),
        ]
        res = measure_polyline_path(pts)
        self.assertIsInstance(res, PolylineMeasurementResult)
        self.assertEqual(res.segment_count, 2)
        self.assertTrue(res.total_distance_m > 0)
        self.assertAlmostEqual(
            res.total_distance_m,
            res.segments[0].distance_m + res.segments[1].distance_m,
            delta=0.01,
        )

        with self.assertRaises(ValueError):
            measure_polyline_path([pts[0]])

    def test_measure_polygon_area(self) -> None:
        # Approximate 0.1 deg x 0.1 deg square near equator
        vertices = [
            GeodesicPoint(0.0, 0.0),
            GeodesicPoint(0.0, 0.1),
            GeodesicPoint(0.1, 0.1),
            GeodesicPoint(0.1, 0.0),
        ]
        res = measure_polygon_area(vertices)
        self.assertIsInstance(res, AreaMeasurementResult)
        self.assertEqual(res.vertex_count, 4)
        # 0.1 deg lat ~ 11.12 km, 0.1 deg lon ~ 11.12 km -> Area ~ 123.6 sq km
        self.assertTrue(115.0 < res.area_sq_km < 130.0)
        self.assertTrue(res.area_sq_nm > 30.0)
        self.assertTrue(res.area_hectares > 10_000.0)
        self.assertTrue(res.perimeter_m > 40_000.0)
        self.assertAlmostEqual(res.centroid.latitude, 0.05, delta=0.01)
        self.assertAlmostEqual(res.centroid.longitude, 0.05, delta=0.01)

        with self.assertRaises(ValueError):
            measure_polygon_area(vertices[:2])

    def test_generate_tactical_corridor(self) -> None:
        centerline = [
            GeodesicPoint(1.0, 103.0),
            GeodesicPoint(1.5, 103.5),
            GeodesicPoint(2.0, 104.0),
        ]
        res = generate_tactical_corridor(centerline, width_m=3704.0)  # 2.0 NM
        self.assertIsInstance(res, CorridorMeasurementResult)
        self.assertAlmostEqual(res.width_nm, 2.0, delta=0.01)
        self.assertTrue(res.corridor_area_sq_nm > 0)

        geojson = res.corridor_polygon_geojson
        self.assertEqual(geojson["type"], "Feature")
        self.assertEqual(geojson["geometry"]["type"], "Polygon")
        ring = geojson["geometry"]["coordinates"][0]
        # Must be closed (first and last coordinate match)
        self.assertEqual(ring[0], ring[-1])
        # Ring should have at least 7 points (3 left + 3 right + 1 close)
        self.assertTrue(len(ring) >= 7)

        with self.assertRaises(ValueError):
            generate_tactical_corridor([centerline[0]], width_m=1000.0)
        with self.assertRaises(ValueError):
            generate_tactical_corridor(centerline, width_m=-10.0)

    def test_classify_vessel_length(self) -> None:
        c1, _ = classify_vessel_length(15.0)
        self.assertIn("Small Craft", c1)

        c2, _ = classify_vessel_length(35.0)
        self.assertIn("Coastal Fishing", c2)

        c3, _ = classify_vessel_length(80.0)
        self.assertIn("Commercial Trawler", c3)

        c4, _ = classify_vessel_length(140.0)
        self.assertIn("General Cargo", c4)

        c5, _ = classify_vessel_length(220.0)
        self.assertIn("Panamax", c5)

        c6, _ = classify_vessel_length(300.0)
        self.assertIn("Capesize", c6)

        c7, _ = classify_vessel_length(380.0)
        self.assertIn("Ultra-Large Carrier", c7)

    def test_measure_vessel_dimension_from_pixels(self) -> None:
        # Vessel oriented vertically (dy = -30, dx = 0) -> Heading North (0 deg)
        res = measure_vessel_dimension_from_pixels(
            x1=100.0,
            y1=150.0,
            x2=100.0,
            y2=120.0,
            meters_per_pixel=10.0,
            beam_width_px=5.0,
        )
        self.assertIsInstance(res, VesselDimensionResult)
        self.assertAlmostEqual(res.length_m, 300.0)
        self.assertAlmostEqual(res.beam_m, 50.0)
        self.assertAlmostEqual(res.aspect_ratio, 6.0)
        self.assertAlmostEqual(res.heading_deg, 0.0, delta=1.0)
        self.assertIn("Capesize", res.estimated_class)

        # Vessel oriented Eastward (dx = 20, dy = 0) -> Heading East (90 deg)
        res_e = measure_vessel_dimension_from_pixels(
            x1=50.0,
            y1=50.0,
            x2=70.0,
            y2=50.0,
            meters_per_pixel=5.0,
        )
        self.assertAlmostEqual(res_e.length_m, 100.0)
        self.assertAlmostEqual(res_e.heading_deg, 90.0, delta=1.0)
        self.assertAlmostEqual(res_e.aspect_ratio, 6.2)

        with self.assertRaises(ValueError):
            measure_vessel_dimension_from_pixels(50.0, 50.0, 50.0, 50.0)


class TestCalculateTacticalMeasurementsUseCase(unittest.TestCase):
    """Test use case execution gateway and input polymorphism."""

    def setUp(self) -> None:
        self.use_case = CalculateTacticalMeasurements()

    def test_execute_distance_bearing(self) -> None:
        params = {"lat1": 1.0, "lon1": 103.0, "lat2": 2.0, "lon2": 104.0}
        out = self.use_case.execute("distance", params)
        self.assertTrue(out["success"])
        self.assertEqual(out["tool"], "distance_bearing")
        self.assertTrue(out["result"]["distance"]["kilometers"] > 100.0)

    def test_execute_polyline(self) -> None:
        params = {
            "points": [
                [1.0, 103.0],
                {"lat": 1.2, "lon": 103.3},
                GeodesicPoint(1.5, 103.6),
            ]
        }
        out = self.use_case.execute("polyline", params)
        self.assertTrue(out["success"])
        self.assertEqual(out["result"]["segment_count"], 2)

    def test_execute_area(self) -> None:
        params = {
            "vertices": [
                [1.0, 103.0],
                [1.0, 103.2],
                [1.2, 103.2],
                [1.2, 103.0],
            ]
        }
        out = self.use_case.execute("area", params)
        self.assertTrue(out["success"])
        self.assertTrue(out["result"]["area"]["sq_kilometers"] > 0)

    def test_execute_corridor(self) -> None:
        params = {
            "centerline": [[1.0, 103.0], [1.5, 103.5]],
            "width": 1.5,
            "unit": "NM",
        }
        out = self.use_case.execute("corridor", params)
        self.assertTrue(out["success"])
        self.assertEqual(out["result"]["geometry"]["type"], "Feature")

    def test_execute_vessel_dimension(self) -> None:
        params = {"x1": 10.0, "y1": 10.0, "x2": 10.0, "y2": 35.0, "meters_per_pixel": 10.0}
        out = self.use_case.execute("vessel_dimension", params)
        self.assertTrue(out["success"])
        self.assertEqual(out["result"]["dimensions"]["length_m"], 250.0)

    def test_execute_invalid_tool(self) -> None:
        with self.assertRaises(ValueError):
            self.use_case.execute("invalid_magic_tool", {})


class TestTacticalMeasurementsWebAPI(unittest.TestCase):
    """Test Flask HTTP API endpoints for tactical measurements."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        scans_dir = self.dir_path / "scans"
        scans_dir.mkdir(parents=True, exist_ok=True)

        self.scan = Scan(
            folder_name="scan_singapore",
            bbox=BoundingBox(103.0, 1.0, 104.0, 2.0),
            image_path="dummy.png",
            metadata={"settings": {"resolution_m": 12.5}},
            acquisition=Acquisition(
                acquired_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
                satellite="Sentinel-1",
                product_type="GRD",
            ),
        )
        self.repo = FakeScanRepository([self.scan])

        settings = Settings(
            project_root=Path(__file__).resolve().parents[1],
            database_path=self.dir_path / "test.db",
            output_root=scans_dir,
            copernicus_username=None,
            copernicus_password=None,
            n2yo_api_key=None,
        )
        container = ApplicationContainer(settings)
        container.scan_repository = self.repo
        container.calculate_tactical_measurements = CalculateTacticalMeasurements(self.repo)

        self.app = create_app(settings, container, start_background_workers=False)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_api_calculate_gateway(self) -> None:
        resp = self.client.post(
            "/api/measurements/calculate",
            json={
                "tool": "distance_bearing",
                "params": {"lat1": 1.29, "lon1": 103.85, "lat2": 2.19, "lon2": 102.25},
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tool"], "distance_bearing")
        self.assertIn("distance", data["result"])

    def test_api_distance_endpoint(self) -> None:
        # Two points direct
        resp = self.client.post(
            "/api/measurements/distance",
            json={"lat1": 1.0, "lon1": 103.0, "lat2": 1.5, "lon2": 103.5},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tool"], "distance_bearing")

        # Polyline track
        resp_poly = self.client.post(
            "/api/measurements/distance",
            json={"points": [[1.0, 103.0], [1.2, 103.2], [1.5, 103.5]]},
        )
        self.assertEqual(resp_poly.status_code, 200)
        data_poly = resp_poly.get_json()
        self.assertTrue(data_poly["success"])
        self.assertEqual(data_poly["tool"], "polyline")

    def test_api_area_endpoint(self) -> None:
        resp = self.client.post(
            "/api/measurements/area",
            json={"vertices": [[1.0, 103.0], [1.0, 103.1], [1.1, 103.1], [1.1, 103.0]]},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tool"], "area")
        self.assertIn("sq_nautical_miles", data["result"]["area"])

    def test_api_corridor_endpoint(self) -> None:
        resp = self.client.post(
            "/api/measurements/corridor",
            json={
                "centerline": [[1.0, 103.0], [1.2, 103.2]],
                "width": 2.5,
                "unit": "NM",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tool"], "corridor")
        self.assertEqual(data["result"]["geometry"]["type"], "Feature")

    def test_api_vessel_endpoint(self) -> None:
        # Using scan_id with metadata resolution 12.5 m/px
        resp = self.client.post(
            "/api/measurements/vessel",
            json={
                "x1": 50,
                "y1": 50,
                "x2": 50,
                "y2": 70,
                "scan_id": "scan_singapore",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tool"], "vessel_dimension")
        # 20 pixels * 12.5 m/px = 250 m
        self.assertAlmostEqual(data["result"]["dimensions"]["length_m"], 250.0)
        self.assertEqual(data["result"]["calibration"]["meters_per_pixel"], 12.5)

    def test_api_error_handling(self) -> None:
        # Missing tool in calculate
        resp = self.client.post("/api/measurements/calculate", json={})
        self.assertEqual(resp.status_code, 400)

        # Insufficient vertices for area
        resp_area = self.client.post("/api/measurements/area", json={"vertices": [[1.0, 103.0]]})
        self.assertEqual(resp_area.status_code, 400)


if __name__ == "__main__":
    unittest.main()
