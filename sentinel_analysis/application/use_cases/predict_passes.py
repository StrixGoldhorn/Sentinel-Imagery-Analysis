"""Satellite-pass prediction use case."""

from sentinel_analysis.application.ports.providers import PassPredictor
from sentinel_analysis.domain.entities import BoundingBox


class PredictPasses:
    def __init__(self, predictor: PassPredictor) -> None:
        self._predictor = predictor

    def execute(self, bbox: BoundingBox, api_key: str) -> list[dict[str, object]]:
        return self._predictor.predict(bbox, api_key)

