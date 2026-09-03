from datetime import datetime, timezone
from typing import Any, Optional

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.satellite import (
    MissionPassAnalyzer,
    PassPrediction,
    PassPredictor,
)
from sentinel_analysis.application.use_cases.predict_passes import PredictPasses
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox


class ListAreasOfInterest:
    def __init__(self, repository: AreaOfInterestRepository) -> None:
        self._repository = repository

    def execute(self) -> list[AreaOfInterest]:
        return list(self._repository.list())


class AddAreaOfInterest:
    def __init__(self, repository: AreaOfInterestRepository) -> None:
        self._repository = repository

    def execute(self, name: str, bbox: BoundingBox) -> int:
        return self._repository.add(AreaOfInterest(name, bbox))


from datetime import datetime, timedelta, timezone


class PredictAreaOfInterest:
    def __init__(
        self,
        repository: AreaOfInterestRepository,
        predictor: PassPredictor,
        mission_analyzer: Optional[MissionPassAnalyzer] = None,
        n2yo_predictor: Optional[PassPredictor] = None,
        cache_ttl_seconds: int = 10800,
    ) -> None:
        self._repository = repository
        self._predictor = predictor
        self._predict_passes = PredictPasses(predictor)
        self._mission_analyzer = mission_analyzer
        self._n2yo_predictor = n2yo_predictor
        self._cache_ttl_seconds = cache_ttl_seconds

    def execute(self, aoi_id: int, api_key: str) -> list[PassPrediction]:
        if isinstance(aoi_id, bool) or not isinstance(aoi_id, int) or aoi_id <= 0:
            raise ValueError("Area-of-interest ID must be a positive integer")
        aoi = self._repository.get(aoi_id)
        if aoi is None:
            raise AreaOfInterestNotFoundError(f"Area of interest not found: {aoi_id}")
        predictions = self._predict_passes.execute(aoi.bbox, api_key)
        if predictions:
            next_scan = datetime.fromisoformat(str(predictions[0]["time"]).replace("Z", "+00:00"))
            self._repository.update_prediction(aoi_id, next_scan, datetime.now(timezone.utc))
        return predictions

    def execute_with_analysis(
        self,
        aoi_id: int,
        api_key: str,
        force_refresh: bool = False,
        cache_ttl_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        if isinstance(aoi_id, bool) or not isinstance(aoi_id, int) or aoi_id <= 0:
            raise ValueError("Area-of-interest ID must be a positive integer")
        aoi = self._repository.get(aoi_id)
        if aoi is None:
            raise AreaOfInterestNotFoundError(f"Area of interest not found: {aoi_id}")

        effective_ttl = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None and isinstance(cache_ttl_seconds, int) and cache_ttl_seconds > 0
            else self._cache_ttl_seconds
        )

        # 1. Check database cache first if not explicitly refreshing
        if not force_refresh and hasattr(self._repository, "get_cached_forecast"):
            try:
                cached = self._repository.get_cached_forecast(aoi_id)
                if cached is not None:
                    cached_dict = dict(cached)
                    cached_dict["cached"] = True
                    return cached_dict
            except Exception:
                pass

        predictions = self.execute(aoi_id, api_key)

        # Extract n2yo passes from predictions or fallback to dedicated predictor
        n2yo_predictions: list[PassPrediction] = [
            p for p in predictions if p.get("source") in ("N2YO", "COMBINED") or p.get("contribution") in ("n2yo", "both")
        ]
        if not n2yo_predictions and self._n2yo_predictor is not None and isinstance(api_key, str) and api_key.strip():
            try:
                n2yo_predictions = PredictPasses(self._n2yo_predictor).execute(aoi.bbox, api_key)
            except Exception:
                n2yo_predictions = []

        # Extract historical passes from predictions or fallback to mission analyzer
        historical_predictions: list[PassPrediction] = [
            p for p in predictions if p.get("source") in ("HISTORICAL_MISSION", "COMBINED") or p.get("contribution") in ("historical", "both")
        ]
        if not historical_predictions and self._mission_analyzer is not None:
            try:
                historical_predictions = self._mission_analyzer.predict_from_history(aoi.bbox, days_ahead=10, limit=100)
            except Exception:
                historical_predictions = []

        mission_summary = None
        if self._mission_analyzer is not None:
            try:
                mission_summary, _ = self._mission_analyzer.analyze_history(aoi.bbox, limit=50)
            except Exception:
                mission_summary = None

        next_scan_val = None
        if predictions:
            next_scan_val = predictions[0]["time"]
        elif historical_predictions:
            next_scan_val = historical_predictions[0]["time"]
        elif n2yo_predictions:
            next_scan_val = n2yo_predictions[0]["time"]

        now = datetime.now(timezone.utc)
        fetched_at = now
        expires_at = now + timedelta(seconds=effective_ttl)

        forecast_result: dict[str, Any] = {
            "aoi_id": aoi.id,
            "name": aoi.name,
            "predictions": predictions,
            "n2yo_predictions": n2yo_predictions,
            "historical_predictions": historical_predictions,
            "next_scan": next_scan_val,
            "mission_analysis": mission_summary,
            "fetched_at": fetched_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "cached": False,
        }

        if hasattr(self._repository, "save_cached_forecast"):
            try:
                self._repository.save_cached_forecast(
                    aoi_id=aoi.id,
                    forecast_data=forecast_result,
                    ttl_seconds=effective_ttl,
                )
            except Exception:
                pass

        return forecast_result
