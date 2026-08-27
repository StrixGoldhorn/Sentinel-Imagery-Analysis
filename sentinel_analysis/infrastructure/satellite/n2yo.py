"""N2YO implementation of the satellite-pass predictor port."""

from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Protocol

import requests

from sentinel_analysis.application.ports.satellite import PassPrediction
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.exceptions import ExternalServiceError


class HTTPResponse(Protocol):
    def raise_for_status(self) -> None:
        ...

    def json(self) -> object:
        ...


class HTTPClient(Protocol):
    def get(self, url: str, **kwargs: object) -> HTTPResponse:
        ...


class N2YOPassPredictor:
    def __init__(
        self,
        satellite_id: int = 39634,
        days: int = 10,
        minimum_elevation: int = 15,
        timeout: float = 30,
        http_client: HTTPClient | None = None,
    ) -> None:
        if isinstance(satellite_id, bool) or not isinstance(satellite_id, int) or satellite_id <= 0:
            raise ValueError("Satellite ID must be a positive integer")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 10:
            raise ValueError("Prediction window must be between 1 and 10 days")
        if isinstance(minimum_elevation, bool) or not isinstance(minimum_elevation, int) or not 0 <= minimum_elevation <= 90:
            raise ValueError("Minimum elevation must be between 0 and 90 degrees")
        if timeout <= 0:
            raise ValueError("N2YO timeout must be positive")
        self._satellite_id = satellite_id
        self._days = days
        self._minimum_elevation = minimum_elevation
        self._timeout = timeout
        self._http = http_client or requests

    def predict(self, bbox: BoundingBox, api_key: str) -> list[PassPrediction]:
        if not api_key:
            raise ValueError("N2YO API key is required")
        try:
            latitude, longitude = bbox.center
            url = (
                "https://api.n2yo.com/rest/v1/satellite/radiopasses/"
                f"{self._satellite_id}/{latitude}/{longitude}/0/{self._days}/"
                f"{self._minimum_elevation}/"
            )
            response = self._http.get(url, params={"apiKey": api_key}, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, Mapping):
                raise ExternalServiceError("N2YO returned an invalid response")
            if "error" in data:
                raise ExternalServiceError(f"N2YO API error: {data['error']}")
            if "info" not in data:
                raise ExternalServiceError("N2YO returned an invalid response")
            passes = data.get("passes", [])
            if not isinstance(passes, list):
                raise ExternalServiceError("N2YO returned invalid pass data")
            predictions: list[PassPrediction] = []
            for item in passes:
                if not isinstance(item, Mapping):
                    raise ExternalServiceError("N2YO returned invalid pass data")
                timestamp = item.get("maxUTC")
                if timestamp is not None:
                    predictions.append(
                        PassPrediction(
                            time=datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat(),
                            max_elevation=item.get("maxElev"),
                        )
                    )
            return predictions
        except ExternalServiceError:
            raise
        except (requests.RequestException, OverflowError, TypeError, ValueError) as exc:
            raise ExternalServiceError("N2YO pass prediction failed") from exc
