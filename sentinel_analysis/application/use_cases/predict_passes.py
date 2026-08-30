"""Satellite-pass prediction use case."""

from datetime import datetime, timezone

from sentinel_analysis.application.exceptions import InvalidPredictionError
from sentinel_analysis.application.ports.satellite import PassPrediction, PassPredictor
from sentinel_analysis.domain.entities import BoundingBox


class PredictPasses:
    def __init__(self, predictor: PassPredictor) -> None:
        self._predictor = predictor

    def execute(self, bbox: BoundingBox, api_key: str) -> list[PassPrediction]:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Satellite prediction API key is required")

        normalized: list[tuple[datetime, PassPrediction]] = []
        for prediction in self._predictor.predict(bbox, api_key.strip()):
            try:
                predicted_at = datetime.fromisoformat(str(prediction["time"]).replace("Z", "+00:00"))
                if predicted_at.utcoffset() is None:
                    predicted_at = predicted_at.replace(tzinfo=timezone.utc)
                predicted_at = predicted_at.astimezone(timezone.utc)
                elevation = prediction.get("max_elevation")
                if elevation is not None:
                    elevation = float(elevation)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise InvalidPredictionError("Pass provider returned an invalid prediction") from exc

            rel_orbit = prediction.get("relative_orbit")
            if rel_orbit is not None:
                try:
                    rel_orbit = int(rel_orbit)
                except (TypeError, ValueError):
                    rel_orbit = None

            conf_score = prediction.get("confidence_score")
            if conf_score is not None:
                try:
                    conf_score = float(conf_score)
                except (TypeError, ValueError):
                    conf_score = None

            normalized.append(
                (
                    predicted_at,
                    PassPrediction(
                        time=predicted_at.isoformat(),
                        max_elevation=elevation,
                        source=prediction.get("source"),
                        satellite=prediction.get("satellite"),
                        orbit_direction=prediction.get("orbit_direction"),
                        relative_orbit=rel_orbit,
                        confidence_score=conf_score,
                        swath_mode=prediction.get("swath_mode"),
                        historical_match=prediction.get("historical_match"),
                    ),
                )
            )

        normalized.sort(key=lambda item: item[0])
        return [prediction for _, prediction in normalized]
