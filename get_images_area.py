"""Compatibility facade for the packaged Copernicus imagery components."""

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.imagery.copernicus import (
    PROCESS_URL as API_URL,
    CATALOG_URL,
    CopernicusImageryProvider,
    CopernicusTokenProvider,
)
from sentinel_analysis.infrastructure.imagery.evalscripts import DEM as EVALSCRIPT_DEM_PNG, SAR as EVALSCRIPT_SAR
from sentinel_analysis.infrastructure.imagery.tiling import TileGridCalculator
from sentinel_analysis.interfaces.cli.download import main

DEFAULT_TIMEOUT = 300
MAX_IMAGE_SIZE = 2500
RESOLUTION_M = 10
MAX_TILE_SIZE_M = MAX_IMAGE_SIZE * RESOLUTION_M


def _provider() -> CopernicusImageryProvider:
    settings = Settings.from_environment()
    return CopernicusImageryProvider(
        CopernicusTokenProvider(settings.copernicus_username, settings.copernicus_password)
    )


def get_latest_sar_datetime(bbox, days_ago: int = 30):
    acquisition = _provider().find_latest_acquisition(BoundingBox.from_sequence(bbox), days_ago)
    return acquisition.acquired_at.isoformat() if acquisition else None


def calculate_tiles(bbox):
    return [
        (tile.bbox.as_list(), tile.width, tile.height, tile.x, tile.y)
        for tile in TileGridCalculator().calculate(BoundingBox.from_sequence(bbox))
    ]


def build_payload(bbox, width, height, evalscript, data_type, time_from=None, time_to=None):
    data_filter = {"timeRange": {"from": time_from, "to": time_to}} if time_from and time_to else {}
    return {
        "input": {"bounds": {"bbox": bbox}, "data": [{"dataFilter": data_filter, "type": data_type}]},
        "output": {
            "width": width,
            "height": height,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": evalscript,
    }


if __name__ == "__main__":
    raise SystemExit(main())
