"""Generate and persist a stitched DEM raster for an area of interest or scan."""

import logging
from collections.abc import Sequence
from pathlib import Path

from sentinel_analysis.application.ports.imagery import ImageStitcher, ImageryProvider, TileImage
from sentinel_analysis.domain.entities import BoundingBox

logger = logging.getLogger(__name__)


class GenerateDEM:
    def __init__(self, imagery: ImageryProvider, stitcher: ImageStitcher) -> None:
        self._imagery = imagery
        self._stitcher = stitcher

    def execute(self, bbox: BoundingBox, output_path: Path) -> bool:
        if not hasattr(self._imagery, "download_dem_tile"):
            return False

        tiles = list(self._imagery.calculate_tiles(bbox))
        if not tiles:
            return False

        image_dir = output_path.parent
        image_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[TileImage] = []
        try:
            for tile in tiles:
                tile_path = image_dir / f"dem_tile_{tile.x}_{tile.y}.png"
                self._imagery.download_dem_tile(tile, tile_path)
                downloaded.append((tile, tile_path))

            try:
                self._stitcher.stitch(downloaded, output_path, allow_empty=True)
            except TypeError:
                self._stitcher.stitch(downloaded, output_path)

            return output_path.is_file()
        except Exception as exc:
            logger.warning("Failed to generate DEM for %s: %s", output_path, exc, exc_info=True)
            return False
        finally:
            for _, tile_path in downloaded:
                tile_path.unlink(missing_ok=True)
