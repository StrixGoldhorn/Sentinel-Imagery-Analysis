"""Copernicus Data Space implementation of the imagery provider port."""

import io
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

import requests
from PIL import Image

from sentinel_analysis.application.exceptions import AuthenticationError, ExternalServiceError
from sentinel_analysis.application.ports.cache import TileCache
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, ImageTile
from sentinel_analysis.infrastructure.imagery.evalscripts import SAR, SAR_DUAL_POL
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
        http_client: HTTPClient | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._http = http_client or requests
        self._monotonic = monotonic_clock or time.monotonic
        self._access_token: str | None = None
        self._expires_at = 0.0

    def get(self) -> str:
        if not self._username or not self._password:
            raise AuthenticationError("Copernicus credentials are not configured")
        if self._access_token and self._monotonic() < self._expires_at:
            return self._access_token

        try:
            response = self._http.post(
                IDENTITY_URL,
                data={
                    "client_id": "cdse-public",
                    "username": self._username,
                    "password": self._password,
                    "grant_type": "password",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            if hasattr(exc, "response") and exc.response is not None:
                if exc.response.status_code in (400, 401, 403):
                    raise AuthenticationError("Copernicus authentication rejected: please verify your COP_USERNAME and COP_PASSWORD credentials") from exc
            raise ExternalServiceError("Copernicus authentication service unreachable") from exc


        if not isinstance(payload, Mapping) or "access_token" not in payload:
            raise ExternalServiceError("Copernicus returned an invalid token response")

        token = str(payload["access_token"])
        expires_in = payload.get("expires_in", 300)
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            expires_in = 300

        self._access_token = token
        self._expires_at = self._monotonic() + max(0, expires_in - 30)
        return self._access_token


class CopernicusImageryProvider:
    def __init__(
        self,
        token_provider: AccessTokenProvider,
        tiler: TileGridCalculator | None = None,
        http_client: HTTPClient | None = None,
        clock: Callable[[], datetime] | None = None,
        tile_cache: TileCache | None = None,
        evalscript: str = SAR,
    ) -> None:
        self._token_provider = token_provider
        self._tiler = tiler or TileGridCalculator()
        self._http = http_client or requests
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tile_cache = tile_cache
        self._evalscript = evalscript

    def find_latest_acquisition(
        self,
        bbox: BoundingBox,
        days_ago: int | None = None,
    ) -> Acquisition | None:
        if days_ago is not None:
            if isinstance(days_ago, bool) or not isinstance(days_ago, int) or days_ago <= 0:
                raise ValueError("Catalog search window must be a positive number of days")
        try:
            now = self._clock()
            if now.utcoffset() is None:
                now = now.replace(tzinfo=timezone.utc)
            now = now.astimezone(timezone.utc)
            if days_ago is not None:
                start = now - timedelta(days=days_ago)
            else:
                # Search across all Sentinel-1 acquisitions to find the most recent available
                start = datetime(2014, 1, 1, tzinfo=timezone.utc)

            end = now + timedelta(days=1)
            response = self._http.get(
                CATALOG_URL,
                headers={"Authorization": f"Bearer {self._token_provider.get()}"},
                params={
                    "bbox": ",".join(map(str, bbox.as_list())),
                    "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}",
                    "collections": "sentinel-1-grd",
                    "limit": 1,
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
        except requests.RequestException as exc:
            msg = "Copernicus catalog request failed"
            if hasattr(exc, "response") and exc.response is not None:
                try:
                    err_json = exc.response.json()
                    if isinstance(err_json, dict) and "message" in err_json:
                        msg = f"Copernicus catalog error: {err_json['message']}"
                except Exception:
                    pass
            raise ExternalServiceError(msg) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ExternalServiceError("Copernicus catalog response invalid") from exc
        return Acquisition(acquired_at, "Sentinel-1", "sentinel-1-grd", str(product_id) if product_id else None)


    def calculate_tiles(self, bbox: BoundingBox) -> list[ImageTile]:
        return self._tiler.calculate(bbox)

    def download_tile(self, tile: ImageTile, acquisition: Acquisition, output_path: Path) -> None:
        cache_key = f"{acquisition.product_id or 'unknown'}_{tile.bbox.as_list()}_{tile.width}_{tile.height}_{hash(self._evalscript)}"
        if self._tile_cache and self._tile_cache.has(cache_key):
            cached_data = self._tile_cache.get(cache_key)
            if cached_data is not None:
                try:
                    with Image.open(io.BytesIO(cached_data)) as source:
                        source.load()
                        image = source.convert("RGBA")
                    temporary = output_path.with_name(f"{output_path.name}.tmp")
                    try:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        image.save(temporary, format="PNG")
                        temporary.replace(output_path)
                        return
                    finally:
                        image.close()
                        temporary.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass

        range_start = acquisition.acquired_at - timedelta(hours=1)
        range_end = acquisition.acquired_at + timedelta(hours=1)
        payload = {
            "input": {
                "bounds": {
                    "bbox": tile.bbox.as_list(),
                    "properties": {
                        "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                    },
                },
                "data": [{
                    "dataFilter": {
                        "timeRange": {
                            "from": range_start.isoformat().replace("+00:00", "Z"),
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
            "evalscript": self._evalscript,
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
            if self._tile_cache:
                self._tile_cache.set(cache_key, response.content)

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
