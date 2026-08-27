"""Create and persist a stitched Sentinel-1 scan."""

from datetime import datetime, timezone

from sentinel_analysis.application.ports.geocoding import LocationResolver
from sentinel_analysis.application.ports.imagery import ImageStitcher, ImageryProvider, TileImage
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import BoundingBox, Scan
from sentinel_analysis.domain.exceptions import NoImageryFoundError


class CreateScan:
    def __init__(
        self,
        imagery: ImageryProvider,
        stitcher: ImageStitcher,
        scans: ScanRepository,
        locations: LocationResolver,
    ) -> None:
        self._imagery = imagery
        self._stitcher = stitcher
        self._scans = scans
        self._locations = locations

    def execute(self, bbox: BoundingBox, days_ago: int = 30) -> Scan:
        if isinstance(days_ago, bool) or not isinstance(days_ago, int) or days_ago <= 0:
            raise ValueError("Imagery search window must be a positive number of days")

        acquisition = self._imagery.find_latest_acquisition(bbox, days_ago)
        if acquisition is None:
            raise NoImageryFoundError("No SAR coverage found for this area")

        now = datetime.now(timezone.utc)
        folder_name = f"{acquisition.acquired_at:%Y%m%d_%H%M%S}_{now:%H%M%S%f}"
        workspace_prepared = False

        try:
            scan_dir = self._scans.prepare(folder_name)
            workspace_prepared = True
            image_dir = scan_dir / "images"
            tiles = list(self._imagery.calculate_tiles(bbox))
            if not tiles:
                raise NoImageryFoundError("The imagery provider returned no downloadable tiles")

            downloaded: list[TileImage] = []
            for tile in tiles:
                tile_path = image_dir / f"tile_{tile.x}_{tile.y}.png"
                self._imagery.download_tile(tile, acquisition, tile_path)
                downloaded.append((tile, tile_path))

            output_path = image_dir / f"{folder_name}_stitched_sar.png"
            self._stitcher.stitch(downloaded, output_path)
            for _, tile_path in downloaded:
                tile_path.unlink(missing_ok=True)
            latitude, longitude = bbox.center
            metadata: dict[str, object] = {
                "acquisition_datetime": acquisition.acquired_at.isoformat(),
                "satellite": acquisition.satellite,
                "settings": {
                    "bbox": bbox.as_list(),
                    "evalscript": "EVALSCRIPT_SAR",
                    "datasource": acquisition.product_type,
                },
                "scraped_datetime": now.isoformat(),
                "location": self._locations.resolve(latitude, longitude),
            }
            if acquisition.product_id is not None:
                metadata["product_id"] = acquisition.product_id
            scan = Scan(folder_name, bbox, acquisition, str(output_path), metadata)
            self._scans.save(scan)
            return scan
        except Exception:
            if workspace_prepared:
                self._scans.delete(folder_name)
            raise
