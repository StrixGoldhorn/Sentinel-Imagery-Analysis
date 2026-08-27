"""Area-of-interest application use cases."""

from datetime import datetime, timezone

from sentinel_analysis.application.ports.providers import PassPredictor
from sentinel_analysis.application.ports.repositories import AreaOfInterestRepository
from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox


class ListAreasOfInterest:
    def __init__(self, repository: AreaOfInterestRepository) -> None:
        self._repository = repository

    def execute(self) -> list[AreaOfInterest]:
        return self._repository.list()


class AddAreaOfInterest:
    def __init__(self, repository: AreaOfInterestRepository) -> None:
        self._repository = repository

    def execute(self, name: str, bbox: BoundingBox) -> int:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("AOI name is required")
        return self._repository.add(AreaOfInterest(normalized_name, bbox))


class PredictAreaOfInterest:
    def __init__(self, repository: AreaOfInterestRepository, predictor: PassPredictor) -> None:
        self._repository = repository
        self._predictor = predictor

    def execute(self, aoi_id: int, api_key: str) -> list[dict[str, object]]:
        aoi = self._repository.get(aoi_id)
        if aoi is None:
            raise LookupError("AOI not found")
        predictions = self._predictor.predict(aoi.bbox, api_key)
        if predictions:
            next_scan = datetime.fromisoformat(str(predictions[0]["time"]).replace("Z", "+00:00"))
            self._repository.update_prediction(aoi_id, next_scan, datetime.now(timezone.utc))
        return predictions

