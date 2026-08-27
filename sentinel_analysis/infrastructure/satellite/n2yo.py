"""N2YO implementation of the satellite-pass predictor port."""

from datetime import datetime, timezone

import requests

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.exceptions import ExternalServiceError


class N2YOPassPredictor:
    def __init__(self, satellite_id: int = 39634, days: int = 10, minimum_elevation: int = 15) -> None:
        self._satellite_id = satellite_id
        self._days = days
        self._minimum_elevation = minimum_elevation

    def predict(self, bbox: BoundingBox, api_key: str) -> list[dict[str, object]]:
        if not api_key:
            raise ValueError("N2YO API key is required")
        try:
            latitude, longitude = bbox.center
            url = (
                "https://api.n2yo.com/rest/v1/satellite/radiopasses/"
                f"{self._satellite_id}/{latitude}/{longitude}/0/{self._days}/"
                f"{self._minimum_elevation}/"
            )
            response = requests.get(url, params={"apiKey": api_key}, timeout=30)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise ExternalServiceError(f"N2YO API error: {data['error']}")
            if "info" not in data:
                raise ExternalServiceError("N2YO returned an invalid response")
            predictions = []
            for item in data.get("passes", []):
                timestamp = item.get("maxUTC")
                if timestamp is not None:
                    predictions.append(
                        {
                            "time": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                            "max_elevation": item.get("maxElev"),
                        }
                    )
            return predictions
        except ExternalServiceError:
            raise
        except (requests.RequestException, TypeError, ValueError) as exc:
            raise ExternalServiceError("N2YO pass prediction failed") from exc
