"""Copernicus Data Space implementation of the imagery provider port."""

import io
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

import get_images_area
from sentinel_analysis.domain.entities import Acquisition, BoundingBox, ImageTile
from sentinel_analysis.domain.exceptions import AuthenticationError, ExternalServiceError


class CopernicusTokenProvider:
    def __init__(self, token_loader) -> None:
        self._token_loader = token_loader

    def get(self) -> str:
        token = self._token_loader()
        if not token:
            raise AuthenticationError("Copernicus authentication returned no access token")
        return token


class CopernicusImageryProvider:
    def __init__(self, token_provider: CopernicusTokenProvider) -> None:
        self._token_provider = token_provider

    def find_latest_acquisition(
        self,
        bbox: BoundingBox,
        days_ago: int = 30,
    ) -> Acquisition | None:
        value = get_images_area.get_latest_sar_datetime(bbox.as_list(), days_ago)
        if not value:
            return None
        try:
            acquired_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExternalServiceError("Copernicus returned an invalid acquisition timestamp") from exc
        return Acquisition(acquired_at, "Sentinel-1", "sentinel-1-grd")

    def calculate_tiles(self, bbox: BoundingBox) -> list[ImageTile]:
        return [
            ImageTile(BoundingBox.from_sequence(tile_bbox), width, height, x, y)
            for tile_bbox, width, height, x, y in get_images_area.calculate_tiles(bbox.as_list())
        ]

    def download_tile(self, tile: ImageTile, acquisition: Acquisition, output_path: Path) -> None:
        payload = get_images_area.build_payload(
            tile.bbox.as_list(), tile.width, tile.height,
            get_images_area.EVALSCRIPT_SAR, acquisition.product_type,
        )
        try:
            response = requests.post(
                get_images_area.API_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token_provider.get()}",
                },
                json=payload,
                timeout=get_images_area.DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path)
        except requests.RequestException as exc:
            raise ExternalServiceError("Copernicus imagery request failed") from exc
        except (OSError, ValueError) as exc:
            raise ExternalServiceError("Copernicus returned an invalid image") from exc

