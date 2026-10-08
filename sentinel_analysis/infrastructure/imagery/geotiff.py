"""Pillow-based GeoTIFF writer with EPSG:4326 geospatial metadata tags."""

import logging
from pathlib import Path

from PIL import Image, TiffImagePlugin

from sentinel_analysis.domain.entities import BoundingBox

logger = logging.getLogger(__name__)


class PillowGeoTIFFWriter:
    """Encodes standard GeoTIFF files with ModelPixelScale and ModelTiepoint tags."""

    def write_geotiff(
        self,
        image_path: Path,
        output_path: Path,
        bbox: BoundingBox,
    ) -> Path:
        image_file = Path(image_path)
        if not image_file.is_file():
            raise FileNotFoundError(f"Input image not found: {image_file}")

        target_file = Path(output_path)
        target_file.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(image_file) as img:
            width, height = img.size
            if width <= 0 or height <= 0:
                raise ValueError("Invalid image dimensions")

            scale_x = (bbox.max_longitude - bbox.min_longitude) / float(width)
            scale_y = (bbox.max_latitude - bbox.min_latitude) / float(height)

            # ModelTiepointTag ties pixel (0, 0, 0) to upper-left coordinate (min_lon, max_lat, 0.0)
            tie_points = (0.0, 0.0, 0.0, float(bbox.min_longitude), float(bbox.max_latitude), 0.0)
            pixel_scale = (float(scale_x), float(scale_y), 0.0)

            # GeoKeyDirectoryTag for standard EPSG:4326 (WGS 84 Geographic 2D)
            # Header: KeyDirectoryVersion=1, KeyRevision=1, MinorRevision=0, NumberOfKeys=3
            # Key 1024 (GTModelTypeGeoKey): 2 (ModelTypeGeographic)
            # Key 1025 (GTRasterTypeGeoKey): 1 (RasterPixelIsArea)
            # Key 2048 (GeographicTypeGeoKey): 4326 (GCS_WGS_84)
            geo_keys = (
                1, 1, 0, 3,
                1024, 0, 1, 2,
                1025, 0, 1, 1,
                2048, 0, 1, 4326,
            )

            tiff_info = TiffImagePlugin.ImageFileDirectory_v2()
            tiff_info[33550] = pixel_scale
            tiff_info[33922] = tie_points
            tiff_info[34735] = geo_keys
            tiff_info[305] = "Sentinel Imagery Analysis Geospatial Engine"
            tiff_info[270] = f"SAR Scene (EPSG:4326) [{bbox.min_longitude:.4f}, {bbox.min_latitude:.4f}, {bbox.max_longitude:.4f}, {bbox.max_latitude:.4f}]"

            img.save(target_file, format="TIFF", tiffinfo=tiff_info)

        logger.info("Saved GeoTIFF to %s with bbox %s", target_file, bbox)
        return target_file
