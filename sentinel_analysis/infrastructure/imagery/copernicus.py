"""Copernicus Data Space implementation of the imagery provider port."""

import io
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

import requests
from PIL import Image

from sentinel_analysis.domain.entities import Acquisition, BoundingBox, ImageTile
from sentinel_analysis.domain.exceptions import AuthenticationError, ExternalServiceError
from sentinel_analysis.infrastructure.imagery.evalscripts import SAR
from sentinel_analysis.infrastructure.imagery.tiling import TileGridCalculator


IDENTITY_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"


class HTTPResponse(Protocol):
    content: bytes

    def raise_for_status(self) -> None:
        ...

    def json(self) -> object:
        ...


class HTTPClient(Protocol):
    def get(self, url: str, **kwargs: object) -> HTTPResponse:
        ...

    def post(self, url: str, **kwargs: object) -> HTTPResponse:
        ...


class AccessTokenProvider(Protocol):
    def get(self) -> str:
        ...


class CopernicusTokenProvider:
    def __init__(
        self,
        username: str | None,
        password: str | None,
        timeout: float = 30,
        http_client: HTTPClient | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Copernicus authentication timeout must be positive")
        self._username = username
        self._password = password
        self._timeout = timeout
        self._http = http_client or requests
        self._monotonic = monotonic_clock
        self._access_token: str | None = None
        self._expires_at = 0.0

    def get(self) -> str:
        if self._access_token and self._monotonic() < self._expires_at:
            return self._access_token
        if not self._username or not self._password:
            raise AuthenticationError("Copernicus username and password are required")
        try:
            response = self._http.post(
                IDENTITY_URL,
                data={
                    "client_id": "cdse-public",
                    "username": self._username,
                    "password": self._password,
                    "grant_type": "password",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("Authentication response must be an object")
            token = payload.get("access_token")
            expires_in = int(payload.get("expires_in", 300))
            if expires_in <= 0:
                raise ValueError("Authentication token lifetime must be positive")
        except (requests.RequestException, TypeError, ValueError) as exc:
            raise AuthenticationError("Copernicus authentication failed") from exc
        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError("Copernicus authentication returned no access token")
        self._access_token = token.strip()
        self._expires_at = self._monotonic() + max(0, expires_in - 30)
        return self._access_token


class CopernicusImageryProvider:
    def __init__(
        self,
        token_provider: AccessTokenProvider,
        tiler: TileGridCalculator | None = None,
        http_client: HTTPClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._tiler = tiler or TileGridCalculator()
        self._http = http_client or requests
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def find_latest_acquisition(
        self,
        bbox: BoundingBox,
        days_ago: int = 30,
    ) -> Acquisition | None:
        if isinstance(days_ago, bool) or not isinstance(days_ago, int) or days_ago <= 0:
            raise ValueError("Catalog search window must be a positive number of days")
        try:
            now = self._clock()
            if now.utcoffset() is None:
                now = now.replace(tzinfo=timezone.utc)
            now = now.astimezone(timezone.utc)
            start = now - timedelta(days=days_ago)
            response = self._http.get(
                CATALOG_URL,
                headers={"Authorization": f"Bearer {self._token_provider.get()}"},
                params={
                    "bbox": ",".join(map(str, bbox.as_list())),
                    "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{now.isoformat().replace('+00:00', 'Z')}",
                    "collections": "sentinel-1-grd",
                    "limit": 1,
                    "sortby": "-properties.datetime",
                },
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("Catalog response must be an object")
            features = payload.get("features", [])
            if not isinstance(features, list):
                raise ValueError("Catalog features must be a list")
            if not features:
                return None
            feature = features[0]
            if not isinstance(feature, Mapping):
                raise ValueError("Catalog feature must be an object")
            properties = feature.get("properties")
            if not isinstance(properties, Mapping):
                raise ValueError("Catalog feature properties must be an object")
            acquired_at = datetime.fromisoformat(str(properties["datetime"]).replace("Z", "+00:00"))
            product_id = feature.get("id")
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise ExternalServiceError("Copernicus catalog request failed") from exc
        return Acquisition(acquired_at, "Sentinel-1", "sentinel-1-grd", str(product_id) if product_id else None)

    def calculate_tiles(self, bbox: BoundingBox) -> list[ImageTile]:
        return self._tiler.calculate(bbox)

    def download_tile(self, tile: ImageTile, acquisition: Acquisition, output_path: Path) -> None:
        range_end = acquisition.acquired_at + timedelta(minutes=1)
        payload = {
            "input": {
                "bounds": {"bbox": tile.bbox.as_list()},
                "data": [{
                    "dataFilter": {
                        "timeRange": {
                            "from": acquisition.acquired_at.isoformat().replace("+00:00", "Z"),
                            "to": range_end.isoformat().replace("+00:00", "Z"),
                        },
                        "mosaickingOrder": "mostRecent",
                    },
                    "type": acquisition.product_type,
                }],
            },
            "output": {
                "width": tile.width,
                "height": tile.height,
                "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
            },
            "evalscript": SAR,
        }
        try:
            response = self._http.post(
                PROCESS_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token_provider.get()}",
                },
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as source:
                source.load()
                image = source.convert("RGBA")
            temporary = output_path.with_name(f"{output_path.name}.tmp")
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(temporary, format="PNG")
                temporary.replace(output_path)
            finally:
                image.close()
                temporary.unlink(missing_ok=True)
        except requests.RequestException as exc:
            raise ExternalServiceError("Copernicus imagery request failed") from exc
        except (OSError, ValueError) as exc:
            raise ExternalServiceError("Copernicus returned an invalid image") from exc
