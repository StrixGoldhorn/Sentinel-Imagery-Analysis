"""Application-owned contracts for acquiring and combining satellite imagery."""

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from sentinel_analysis.domain.entities import Acquisition, BoundingBox, ImageTile


TileImage = tuple[ImageTile, Path]


@runtime_checkable
class ImageryProvider(Protocol):
    """Find and download imagery without exposing a vendor-specific API."""

    def find_latest_acquisition(
        self,
        bbox: BoundingBox,
        days_ago: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
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

    def download_dem_tile(
        self,
        tile: ImageTile,
        output_path: Path,
    ) -> None:
        ...


@runtime_checkable
class ImageStitcher(Protocol):
    """Combine ordered tile artifacts into one output image."""

    def stitch(self, tiles: Sequence[TileImage], output_path: Path, allow_empty: bool = False) -> None:
        ...


@runtime_checkable
class GeoTIFFWriter(Protocol):
    """Write georeferenced TIFF files with spatial transformation tags."""

    def write_geotiff(
        self,
        image_path: Path,
        output_path: Path,
        bbox: BoundingBox,
    ) -> Path:
        ...


@runtime_checkable
class ChipRenderer(Protocol):
    """Render tactical chip modalities using image processing."""

    def get_image_dimensions(self, image_path: Path | str) -> tuple[int, int]:
        ...

    def render_sar_chip(
        self,
        image_path: Path | str,
        det: dict[str, Any],
        det_x: float,
        det_y: float,
        det_w: float,
        det_h: float,
        center_x: float,
        center_y: float,
        padding_px: int,
        out_size: int,
        sensor_label: str = "S-1",
    ) -> tuple[bool, str, str, dict[str, Any]]:
        ...

    def render_optical_chip(
        self,
        optical_path: Optional[Path],
        lat: float,
        lon: float,
        scan_bbox: BoundingBox,
        det: dict[str, Any],
        out_size: int,
        opt_status: str,
        opt_confirmed: bool,
        cloud_cover: Optional[float] = None,
        time_delta_h: Optional[float] = None,
        scene_id: Optional[str] = None,
    ) -> tuple[bool, str, str, dict[str, Any]]:
        ...

    def render_previous_pass_chip(
        self,
        prior_image_path: Optional[Path],
        prior_bbox: Optional[BoundingBox],
        prior_scan_id: Optional[str],
        lat: float,
        lon: float,
        padding_px: int,
        out_size: int,
        time_delta_h: Optional[float],
        has_prior_detection: bool = False,
    ) -> tuple[bool, str, str, dict[str, Any]]:
        ...

    def render_ais_context_chip(
        self,
        lat: float,
        lon: float,
        det: dict[str, Any],
        nearby_vessels: Sequence[dict[str, Any]],
        out_size: int,
        max_range_nm: float = 5.0,
        nearest_dist_m: Optional[float] = None,
        nearest_mmsi: Optional[str] = None,
        correlated_mmsi: Optional[str] = None,
    ) -> tuple[bool, str, str, dict[str, Any]]:
        ...


@runtime_checkable
class SarRadiometryProcessor(Protocol):
    """Protocol for SAR pixel radiometry, histogram extraction, and despeckle filtering."""

    def compute_histogram(
        self,
        image_path: Path | str,
        polarization: Optional[str] = None,
        num_bins: int = 256,
    ) -> Any:
        ...

    def enhance_radiometry(
        self,
        image_path: Path | str,
        adjustment: Any,
    ) -> bytes:
        ...



