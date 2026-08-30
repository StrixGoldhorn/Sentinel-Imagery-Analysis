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


class MissionAnalysisTests(unittest.TestCase):
    def test_analyzer_computes_statistics_from_historical_acquisitions(self) -> None:
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

        self.assertEqual(summary["total_acquisitions"], 4)
        self.assertEqual(summary["dominant_tracks"], [142, 69])
        self.assertEqual(summary["ascending_count"], 3)
        self.assertEqual(summary["descending_count"], 1)
        self.assertEqual(len(history), 4)
        self.assertIn("ASCENDING", summary["typical_utc_windows"])
        self.assertIn("DESCENDING", summary["typical_utc_windows"])

    def test_analyzer_predicts_future_passes_using_12_day_repeat_cycle(self) -> None:
        now = datetime.now(timezone.utc)
        # Place a historical pass exactly 12 days before now
        hist_time = (now - timedelta(days=12)).replace(microsecond=0)

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

        self.assertTrue(len(predictions) >= 1)
        # Should contain an exact 12-day forward projection
        projected_times = [datetime.fromisoformat(p["time"].replace("Z", "+00:00")) for p in predictions]
        self.assertTrue(any(abs((t - (hist_time + timedelta(days=12))).total_seconds()) < 2 for t in projected_times))
        self.assertEqual(predictions[0]["source"], "HISTORICAL_MISSION")
        self.assertEqual(predictions[0]["relative_orbit"], 142)

    def test_hybrid_predictor_merges_overlapping_passes_into_combined(self) -> None:
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

        self.assertEqual(len(results), 1)
        combined = results[0]
        self.assertEqual(combined["source"], "COMBINED")
        self.assertEqual(combined["relative_orbit"], 142)
        self.assertEqual(combined["orbit_direction"], "ASCENDING")
        self.assertEqual(combined["confidence_score"], 0.98)

    def test_hybrid_predictor_keeps_standalone_passes_from_both_sources(self) -> None:
        now = datetime.now(timezone.utc)
        pass_1 = PassPrediction(time=(now + timedelta(days=1)).isoformat(), max_elevation=45.0, source="N2YO")
        pass_2 = PassPrediction(time=(now + timedelta(days=3)).isoformat(), max_elevation=75.0, source="HISTORICAL_MISSION", relative_orbit=88)

        n2yo_predictor = MockPassPredictor([pass_1])
        class StubAnalyzer(Sentinel1MissionAnalyzer):
            def predict_from_history(self, bbox, days_ahead=10, limit=20):
                return [pass_2]

        hybrid = HybridPassPredictor(n2yo_predictor, StubAnalyzer())
        results = hybrid.predict(BBOX, "api_key")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["source"], "N2YO")
        self.assertEqual(results[1]["source"], "HISTORICAL_MISSION")

    def test_analyze_mission_passes_use_case(self) -> None:
        aoi = AreaOfInterest("Singapore Strait", BBOX, id=1)
        repo = MockAOIRepository([aoi])
        analyzer = Sentinel1MissionAnalyzer(MockHistoryProvider([]))
        use_case = AnalyzeMissionPasses(repo, analyzer)

        result = use_case.execute(1)
        self.assertEqual(result["aoi_id"], 1)
        self.assertEqual(result["name"], "Singapore Strait")
        self.assertIn("mission_analysis", result)

    def test_predict_area_of_interest_with_analysis(self) -> None:
        now = datetime.now(timezone.utc)
        aoi = AreaOfInterest("Port Area", BBOX, id=1)
        repo = MockAOIRepository([aoi])
        predictor = MockPassPredictor([{"time": (now + timedelta(hours=2)).isoformat(), "max_elevation": 60}])
        analyzer = Sentinel1MissionAnalyzer(MockHistoryProvider([]))

        use_case = PredictAreaOfInterest(repo, predictor, analyzer)
        result = use_case.execute_with_analysis(1, "key")

        self.assertEqual(result["aoi_id"], 1)
        self.assertTrue(len(result["predictions"]) >= 1)
        self.assertIsNotNone(result["mission_analysis"])
        self.assertEqual(len(repo.updates), 1)

    def test_check_and_schedule_aois_executes_1_minute_cadence_ais_scraping_during_flypast(self) -> None:
        now = datetime.now(timezone.utc)
        # Flypast active right now: pass is in 2 minutes
        active_pass_time = now + timedelta(minutes=2)
        aoi = AreaOfInterest("Active AOI", BBOX, id=1, auto_capture_enabled=True)
        repo = MockAOIRepository([aoi])
        predictor = MockPassPredictor([{"time": active_pass_time.isoformat()}])
        mock_ingest = MockIngestAIS()

        scheduler = CheckAndScheduleAOIs(repo, predictor, ingest_ais=mock_ingest)
        results = scheduler.execute("api_key")

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["flypast_active"])
        self.assertEqual(results[0]["status"], "FLYPAST_ACTIVE")
        self.assertEqual(results[0]["ais_records"], 12)
        self.assertEqual(len(mock_ingest.calls), 1)


if __name__ == "__main__":
    unittest.main()
