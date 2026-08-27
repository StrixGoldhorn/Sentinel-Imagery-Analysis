"""Application-owned contract for satellite pass prediction."""

from typing import Protocol, TypedDict, runtime_checkable

from sentinel_analysis.domain.entities import BoundingBox


class PassPrediction(TypedDict):
    """Provider-neutral satellite pass data returned to the application."""

    time: str
    max_elevation: float | int | None


@runtime_checkable
class PassPredictor(Protocol):
    """Predict observable satellite passes over an area."""

    def predict(self, bbox: BoundingBox, api_key: str) -> list[PassPrediction]:
        ...
