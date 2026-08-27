"""Command-line interface for Copernicus imagery downloads."""

import argparse
from pathlib import Path

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.exceptions import NoImageryFoundError
from sentinel_analysis.infrastructure.imagery.copernicus import CopernicusImageryProvider, CopernicusTokenProvider
from sentinel_analysis.infrastructure.imagery.stitching import PillowImageStitcher


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and stitch Sentinel-1 SAR imagery")
    parser.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    parser.add_argument("--days-ago", type=int, default=30)
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args()

    settings = Settings.from_environment()
    provider = CopernicusImageryProvider(
        CopernicusTokenProvider(settings.copernicus_username, settings.copernicus_password)
    )
    bbox = BoundingBox.from_sequence(args.bbox)
    acquisition = provider.find_latest_acquisition(bbox, args.days_ago)
    if acquisition is None:
        raise NoImageryFoundError("No SAR imagery found")

    folder = Path(args.output_dir).resolve() / acquisition.acquired_at.strftime("%Y%m%d_%H%M%S")
    image_dir = folder / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for tile in provider.calculate_tiles(bbox):
        path = image_dir / f"tile_{tile.x}_{tile.y}.png"
        provider.download_tile(tile, acquisition, path)
        downloaded.append((tile, path))
    output = image_dir / f"{folder.name}_stitched_sar.png"
    PillowImageStitcher().stitch(downloaded, output)
    print(output)


if __name__ == "__main__":
    main()

