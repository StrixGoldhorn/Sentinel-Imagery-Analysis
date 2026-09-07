import logging
import re
from datetime import datetime, timezone

from sentinel_analysis.application.exceptions import NoImageryFoundError
from sentinel_analysis.application.ports.geocoding import LocationResolver
from sentinel_analysis.application.ports.imagery import ImageStitcher, ImageryProvider, TileImage
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import BoundingBox, Scan

logger = logging.getLogger(__name__)


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

    def execute(
        self,
        bbox: BoundingBox,
        days_ago: int | None = None,
        aoi_name: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> Scan:
        if days_ago is not None:
            if isinstance(days_ago, bool) or not isinstance(days_ago, int) or days_ago <= 0:
                raise ValueError("Imagery search window must be a positive number of days")

        if start_date is not None:
            if start_date.utcoffset() is None:
                start_date = start_date.replace(tzinfo=timezone.utc)
            start_date = start_date.astimezone(timezone.utc)
        if end_date is not None:
            if end_date.utcoffset() is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            end_date = end_date.astimezone(timezone.utc)

        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date cannot be after end_date")

        try:
            acquisition = self._imagery.find_latest_acquisition(
                bbox,
                days_ago=days_ago,
                start_date=start_date,
                end_date=end_date,
            )
        except TypeError:
            acquisition = self._imagery.find_latest_acquisition(bbox, days_ago)

        if acquisition is None:
            raise NoImageryFoundError("No SAR coverage found for this area")

        now = datetime.now(timezone.utc)
        if aoi_name and isinstance(aoi_name, str) and aoi_name.strip():
            clean_aoi = re.sub(r"[^\w\-.]", "_", aoi_name.strip())
            clean_aoi = re.sub(r"_+", "_", clean_aoi).strip("_")
            if not clean_aoi:
                clean_aoi = "AOI"
            date_str = f"{acquisition.acquired_at:%Y-%m-%d}"
            base_folder = f"{clean_aoi}_{date_str}"
            folder_name = base_folder
            counter = 1
            while self._scans.get(folder_name) is not None:
                folder_name = f"{base_folder}_{counter}"
                counter += 1
        else:
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

            dem_available = False
            dem_output_path = image_dir / f"{folder_name}_stitched_dem.png"
            if hasattr(self._imagery, "download_dem_tile"):
                downloaded_dem: list[TileImage] = []
                try:
                    for tile in tiles:
                        dem_tile_path = image_dir / f"dem_tile_{tile.x}_{tile.y}.png"
                        self._imagery.download_dem_tile(tile, dem_tile_path)
                        downloaded_dem.append((tile, dem_tile_path))

                    try:
                        self._stitcher.stitch(downloaded_dem, dem_output_path, allow_empty=True)
                    except TypeError:
                        self._stitcher.stitch(downloaded_dem, dem_output_path)
                    dem_available = dem_output_path.is_file()
                except Exception as exc:
                    logger.warning("Failed to generate DEM for scan %s: %s", folder_name, exc, exc_info=True)
                    dem_available = False
                finally:
                    for _, dem_tile_path in downloaded_dem:
                        dem_tile_path.unlink(missing_ok=True)

            latitude, longitude = bbox.center
            settings_dict: dict[str, object] = {
                "bbox": bbox.as_list(),
                "evalscript": "EVALSCRIPT_SAR",
                "datasource": acquisition.product_type,
            }
            if start_date is not None:
                settings_dict["start_date"] = start_date.isoformat()
            if end_date is not None:
                settings_dict["end_date"] = end_date.isoformat()

            metadata: dict[str, object] = {
                "acquisition_datetime": acquisition.acquired_at.isoformat(),
                "satellite": acquisition.satellite,
                "settings": settings_dict,
                "scraped_datetime": now.isoformat(),
                "location": self._locations.resolve(latitude, longitude),
                "dem_available": dem_available,
            }
            if aoi_name and isinstance(aoi_name, str) and aoi_name.strip():
                metadata["custom_name"] = folder_name
                metadata["aoi_name"] = aoi_name.strip()
            if acquisition.product_id is not None:
                metadata["product_id"] = acquisition.product_id
            scan = Scan(folder_name, bbox, acquisition, str(output_path), metadata)
            self._scans.save(scan)
            return scan
        except Exception:
            if workspace_prepared:
                self._scans.delete(folder_name)
            raise
