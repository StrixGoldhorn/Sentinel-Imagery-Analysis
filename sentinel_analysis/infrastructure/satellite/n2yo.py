"""N2YO implementation of the satellite-pass predictor port."""

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.exceptions import ExternalServiceError
from predict_scans import predict_next_scans_n2yo


class N2YOPassPredictor:
    def predict(self, bbox: BoundingBox, api_key: str) -> list[dict[str, object]]:
        if not api_key:
            raise ValueError("N2YO API key is required")
        try:
            return predict_next_scans_n2yo(bbox.as_list(), api_key)
        except Exception as exc:
            raise ExternalServiceError("N2YO pass prediction failed") from exc

