"""Unit tests for Sentinel-1 mission history analysis, repeat-cycle pass modeling, and hybrid prediction."""

import unittest
from datetime import datetime, timedelta, timezone

from sentinel_analysis.application.ports.satellite import (
    HistoricalMissionPass,
    PassPrediction,
)
from sentinel_analysis.application.use_cases.analyze_mission_passes import AnalyzeMissionPasses
from sentinel_analysis.application.use_cases.manage_aois import PredictAreaOfInterest
from sentinel_analysis.application.use_cases.schedule_aois import CheckAndScheduleAOIs
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.infrastructure.satellite.hybrid_predictor import HybridPassPredictor
from sentinel_analysis.infrastructure.satellite.s1_analyzer import Sentinel1MissionAnalyzer


BBOX = BoundingBox(103.5, 1.0, 104.5, 2.0)


class MockHistoryProvider:
    def __init__(self, records: list[dict]):
        self.records = records

    def search_historical_acquisitions(self, bbox, start_date=None, end_date=None, limit=100):
        return self.records


class MockPassPredictor:
    def __init__(self, passes: list[PassPrediction], error: Exception | None = None):
        self.passes = passes
        self.error = error

    def predict(self, bbox, api_key):
        if self.error:
            raise self.error
        return list(self.passes)


class MockAOIRepository:
    def __init__(self, aois: list[AreaOfInterest]):
        self.aois = {aoi.id: aoi for aoi in aois}
        self.updates: list[tuple[int, datetime, datetime]] = []

    def get(self, aoi_id: int):
        return self.aois.get(aoi_id)

    def list(self):
        return list(self.aois.values())

    def update_prediction(self, aoi_id, next_scan, last_checked):
        self.updates.append((aoi_id, next_scan, last_checked))


class MockIngestAIS:
    def __init__(self):
        self.calls: list[tuple[BoundingBox, tuple[datetime, datetime]]] = []

    def execute(self, bbox, time_range):
        self.calls.append((bbox, time_range))
        return {"total_inserted": 12, "logs": []}


def test_analyzer_computes_statistics_from_historical_acquisitions() -> None:
    raw_records = [
        {
            "product_id": "S1A_IW_GRDH_1SDV_20260701T103000",
            "platform": "Sentinel-1A",
            "acquisition_time": "2026-07-01T10:30:00Z",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 142,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        },
        {
            "product_id": "S1A_IW_GRDH_1SDV_20260713T103000",
            "platform": "Sentinel-1A",
            "acquisition_time": "2026-07-13T10:30:00Z",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 142,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        },
        {
            "product_id": "S1C_IW_GRDH_1SDV_20260719T103000",
            "platform": "Sentinel-1C",
            "acquisition_time": "2026-07-19T10:30:00Z",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 142,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        },
        {
            "product_id": "S1A_IW_GRDH_1SDV_20260725T223000",
            "platform": "Sentinel-1A",
            "acquisition_time": "2026-07-25T22:30:00Z",
            "orbit_direction": "DESCENDING",
            "relative_orbit": 69,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        },
    ]

    analyzer = Sentinel1MissionAnalyzer(MockHistoryProvider(raw_records))
    summary, history = analyzer.analyze_history(BBOX)

    assert summary["total_acquisitions"] == 4
    assert summary["dominant_tracks"] == [142, 69]
    assert summary["ascending_count"] == 3
    assert summary["descending_count"] == 1
    assert len(history) == 4
    assert "ASCENDING" in summary["typical_utc_windows"]
    assert "DESCENDING" in summary["typical_utc_windows"]


def test_analyzer_predicts_future_passes_using_12_day_repeat_cycle() -> None:
    now = datetime.now(timezone.utc)
    hist_time = (now - timedelta(days=10)).replace(microsecond=0)

    raw_records = [
        {
            "product_id": "S1A_IW_GRDH_1SDV_001",
            "platform": "Sentinel-1A",
            "acquisition_time": hist_time.isoformat(),
            "orbit_direction": "ASCENDING",
            "relative_orbit": 142,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        }
    ]

    analyzer = Sentinel1MissionAnalyzer(MockHistoryProvider(raw_records))
    predictions = analyzer.predict_from_history(BBOX, days_ahead=15)

    assert len(predictions) >= 1
    projected_times = [datetime.fromisoformat(p["time"].replace("Z", "+00:00")) for p in predictions]
    assert any(abs((t - (hist_time + timedelta(days=12))).total_seconds()) < 2 for t in projected_times)
    assert predictions[0]["source"] == "HISTORICAL_MISSION"
    assert predictions[0]["relative_orbit"] == 142


def test_hybrid_predictor_merges_overlapping_passes_into_combined() -> None:
    now = datetime.now(timezone.utc)
    pass_time = now + timedelta(days=2)

    n2yo_pass = PassPrediction(
        time=pass_time.isoformat(),
        max_elevation=68.0,
        source="N2YO",
        satellite="Sentinel-1A",
    )
    hist_pass = PassPrediction(
        time=(pass_time + timedelta(minutes=3)).isoformat(),
        max_elevation=75.0,
        source="HISTORICAL_MISSION",
        satellite="Sentinel-1A",
        relative_orbit=142,
        orbit_direction="ASCENDING",
    )

    n2yo_predictor = MockPassPredictor([n2yo_pass])
    class StubAnalyzer(Sentinel1MissionAnalyzer):
        def predict_from_history(self, bbox, days_ahead=10, limit=20):
            return [hist_pass]

    hybrid = HybridPassPredictor(n2yo_predictor, StubAnalyzer())
    results = hybrid.predict(BBOX, "api_key")

    assert len(results) == 1
    combined = results[0]
    assert combined["source"] == "COMBINED"
    assert combined["relative_orbit"] == 142
    assert combined["orbit_direction"] == "ASCENDING"
    assert combined["confidence_score"] == 0.98


def test_hybrid_predictor_keeps_standalone_passes_from_both_sources() -> None:
    now = datetime.now(timezone.utc)
    pass_1 = PassPrediction(time=(now + timedelta(days=1)).isoformat(), max_elevation=45.0, source="N2YO")
    pass_2 = PassPrediction(time=(now + timedelta(days=3)).isoformat(), max_elevation=75.0, source="HISTORICAL_MISSION", relative_orbit=88, confidence_score=0.94)

    n2yo_predictor = MockPassPredictor([pass_1])
    class StubAnalyzer(Sentinel1MissionAnalyzer):
        def predict_from_history(self, bbox, days_ahead=10, limit=20):
            return [pass_2]

    hybrid = HybridPassPredictor(n2yo_predictor, StubAnalyzer())
    results = hybrid.predict(BBOX, "api_key")

    assert len(results) == 2
    assert results[0]["source"] == "N2YO"
    assert results[0]["confidence_score"] == 0.68  # Lower weight
    assert results[1]["source"] == "HISTORICAL_MISSION"
    assert results[1]["confidence_score"] == 0.94  # Higher weight


def test_analyze_mission_passes_use_case() -> None:
    aoi = AreaOfInterest("Singapore Strait", BBOX, id=1)
    repo = MockAOIRepository([aoi])
    analyzer = Sentinel1MissionAnalyzer(MockHistoryProvider([]))
    use_case = AnalyzeMissionPasses(repo, analyzer)

    result = use_case.execute(1)
    assert result["aoi_id"] == 1
    assert result["name"] == "Singapore Strait"
    assert "mission_analysis" in result


def test_predict_area_of_interest_with_analysis() -> None:
    now = datetime.now(timezone.utc)
    aoi = AreaOfInterest("Port Area", BBOX, id=1)
    repo = MockAOIRepository([aoi])
    predictor = MockPassPredictor([{"time": (now + timedelta(hours=2)).isoformat(), "max_elevation": 60, "source": "N2YO"}])
    analyzer = Sentinel1MissionAnalyzer(MockHistoryProvider([]))

    use_case = PredictAreaOfInterest(repo, predictor, analyzer)
    result = use_case.execute_with_analysis(1, "key")

    assert result["aoi_id"] == 1
    assert len(result["predictions"]) >= 1
    assert "n2yo_predictions" in result
    assert "historical_predictions" in result
    assert result["mission_analysis"] is not None
    assert len(repo.updates) == 1



def test_check_and_schedule_aois_executes_1_minute_cadence_ais_scraping_during_flypast() -> None:
    now = datetime.now(timezone.utc)
    active_pass_time = now + timedelta(minutes=2)
    aoi = AreaOfInterest("Active AOI", BBOX, id=1, auto_capture_enabled=True)
    repo = MockAOIRepository([aoi])
    predictor = MockPassPredictor([{"time": active_pass_time.isoformat()}])
    mock_ingest = MockIngestAIS()

    scheduler = CheckAndScheduleAOIs(repo, predictor, ingest_ais=mock_ingest)
    results = scheduler.execute("api_key")

    assert len(results) == 1
    assert results[0]["flypast_active"] is True
    assert results[0]["status"] == "FLYPAST_ACTIVE"
    assert results[0]["ais_records"] == 12
def test_dynamic_history_limit_scales_with_latitude() -> None:
    equatorial = BoundingBox(103.5, 1.0, 104.5, 2.0)
    subtropical = BoundingBox(120.0, 22.0, 122.0, 24.0)
    mid_lat = BoundingBox(135.0, 35.0, 137.0, 37.0)
    high_lat = BoundingBox(3.0, 56.0, 6.0, 59.0)  # North Sea / Norway

    assert Sentinel1MissionAnalyzer.calculate_dynamic_history_limit(equatorial) == 150
    assert Sentinel1MissionAnalyzer.calculate_dynamic_history_limit(subtropical) == 200
    assert Sentinel1MissionAnalyzer.calculate_dynamic_history_limit(mid_lat) == 300
    assert Sentinel1MissionAnalyzer.calculate_dynamic_history_limit(high_lat) == 500

    assert Sentinel1MissionAnalyzer.get_nominal_revisit_days(equatorial) == 6.0
    assert Sentinel1MissionAnalyzer.get_nominal_revisit_days(high_lat) <= 2.5


def test_analyzer_uses_dynamic_limit_when_querying_provider() -> None:
    class TrackingProvider:
        def __init__(self):
            self.last_limit = None

        def search_historical_acquisitions(self, bbox, start_date=None, end_date=None, limit=100):
            self.last_limit = limit
            return []

    provider = TrackingProvider()
    analyzer = Sentinel1MissionAnalyzer(provider)

    # High latitude bbox (North Sea)
    high_lat_bbox = BoundingBox(3.0, 58.0, 6.0, 60.0)
    analyzer.analyze_history(high_lat_bbox)
    assert provider.last_limit == 500

    # Equatorial bbox (Singapore Strait)
    eq_bbox = BoundingBox(103.5, 1.0, 104.5, 2.0)
    analyzer.analyze_history(eq_bbox)
    assert provider.last_limit == 150


def test_high_latitude_multi_track_convergence_predictions() -> None:
    now = datetime.now(timezone.utc)
    high_lat_bbox = BoundingBox(3.0, 58.0, 6.0, 60.0)

    # 3 distinct overlapping relative orbits typical of high latitudes
    raw_records = [
        {
            "product_id": "S1A_001",
            "platform": "Sentinel-1A",
            "acquisition_time": (now - timedelta(days=2)).isoformat(),
            "orbit_direction": "ASCENDING",
            "relative_orbit": 14,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        },
        {
            "product_id": "S1A_002",
            "platform": "Sentinel-1A",
            "acquisition_time": (now - timedelta(days=5)).isoformat(),
            "orbit_direction": "DESCENDING",
            "relative_orbit": 87,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        },
        {
            "product_id": "S1A_003",
            "platform": "Sentinel-1A",
            "acquisition_time": (now - timedelta(days=8)).isoformat(),
            "orbit_direction": "ASCENDING",
            "relative_orbit": 160,
            "polarisation": "VV+VH",
            "instrument_mode": "IW",
        },
    ]

    analyzer = Sentinel1MissionAnalyzer(MockHistoryProvider(raw_records))
    predictions = analyzer.predict_from_history(high_lat_bbox, days_ahead=12, limit=20)

    # Multi-track convergence generates frequent future passes across all 3 tracks
    predicted_tracks = {p["relative_orbit"] for p in predictions}
    assert 14 in predicted_tracks
    assert 87 in predicted_tracks
    assert 160 in predicted_tracks
    assert len(predictions) >= 3


def load_tests(loader, standard_tests, pattern):

    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

