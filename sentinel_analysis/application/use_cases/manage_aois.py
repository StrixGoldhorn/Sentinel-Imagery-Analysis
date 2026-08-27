"""Area-of-interest application use cases."""

from datetime import datetime, timezone

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.satellite import PassPrediction, PassPredictor
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


class PredictAreaOfInterest:
    def __init__(self, repository: AreaOfInterestRepository, predictor: PassPredictor) -> None:
        self._repository = repository
        self._predict_passes = PredictPasses(predictor)

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
