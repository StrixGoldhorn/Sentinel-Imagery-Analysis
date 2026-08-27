"""Copernicus Data Space implementation of the imagery provider port."""

import io
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

from sentinel_analysis.domain.entities import Acquisition, BoundingBox, ImageTile
from sentinel_analysis.domain.exceptions import AuthenticationError, ExternalServiceError
from sentinel_analysis.infrastructure.imagery.evalscripts import SAR
from sentinel_analysis.infrastructure.imagery.tiling import TileGridCalculator


IDENTITY_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"


class CopernicusTokenProvider:
    def __init__(self, username: str | None, password: str | None, timeout: float = 30) -> None:
        self._username = username
        self._password = password
        self._timeout = timeout
        self._access_token: str | None = None
        self._expires_at = 0.0

    def get(self) -> str:
        if self._access_token and time.monotonic() < self._expires_at:
            return self._access_token
        if not self._username or not self._password:
            raise AuthenticationError("Copernicus username and password are required")
        try:
            response = requests.post(
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
            token = payload.get("access_token")
            expires_in = max(60, int(payload.get("expires_in", 300)))
        except (requests.RequestException, ValueError) as exc:
            raise AuthenticationError("Copernicus authentication failed") from exc
        if not token:
            raise AuthenticationError("Copernicus authentication returned no access token")
        self._access_token = str(token)
        self._expires_at = time.monotonic() + expires_in - 30
        return self._access_token


class CopernicusImageryProvider:
    def __init__(self, token_provider: CopernicusTokenProvider, tiler: TileGridCalculator | None = None) -> None:
        self._token_provider = token_provider
        self._tiler = tiler or TileGridCalculator()

    def find_latest_acquisition(
        self,
        bbox: BoundingBox,
        days_ago: int = 30,
    ) -> Acquisition | None:
        try:
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=days_ago)
            response = requests.get(
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
            features = response.json().get("features", [])
            if not features:
                return None
            feature = features[0]
            acquired_at = datetime.fromisoformat(feature["properties"]["datetime"].replace("Z", "+00:00"))
            product_id = feature.get("id")
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise ExternalServiceError("Copernicus catalog request failed") from exc
        return Acquisition(acquired_at, "Sentinel-1", "sentinel-1-grd", product_id)

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
            response = requests.post(
                PROCESS_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token_provider.get()}",
                },
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path)
        except requests.RequestException as exc:
            raise ExternalServiceError("Copernicus imagery request failed") from exc
        except (OSError, ValueError) as exc:
            raise ExternalServiceError("Copernicus returned an invalid image") from exc
