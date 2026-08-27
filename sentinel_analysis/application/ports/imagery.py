"""Application-owned contracts for acquiring and combining satellite imagery."""

from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from sentinel_analysis.domain.entities import Acquisition, BoundingBox, ImageTile


TileImage = tuple[ImageTile, Path]


@runtime_checkable
class ImageryProvider(Protocol):
    """Find and download imagery without exposing a vendor-specific API."""

    def find_latest_acquisition(
        self,
        bbox: BoundingBox,
        days_ago: int = 30,
    ) -> Acquisition | None:
        ...

    def calculate_tiles(self, bbox: BoundingBox) -> Sequence[ImageTile]:
        ...

    def download_tile(
        self,
        tile: ImageTile,
        acquisition: Acquisition,
        output_path: Path,
    ) -> None:
        ...


@runtime_checkable
class ImageStitcher(Protocol):
    """Combine ordered tile artifacts into one output image."""

    def stitch(self, tiles: Sequence[TileImage], output_path: Path) -> None:
        ...
