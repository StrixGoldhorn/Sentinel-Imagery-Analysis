"""Use cases for exporting scan data to standard geospatial formats (GeoTIFF, STAC, GeoJSON)."""

from datetime import timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.imagery import GeoTIFFWriter
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import Scan

logger = logging.getLogger(__name__)


class ExportGeospatial:
    """Orchestrates standard geospatial exports for radar scans."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        geotiff_writer: Optional[GeoTIFFWriter] = None,
    ) -> None:
        self._scan_repository = scan_repository
        self._geotiff_writer = geotiff_writer

    def export_geotiff(self, folder_name: str, target_path: Optional[Path] = None) -> Path:
        """Export a SAR scan as a georeferenced GeoTIFF with EPSG:4326 geotransform tags."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")
        image_path = Path(scan.image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Scan image not found: {image_path}")

        if target_path is None:
            target_path = image_path.parent / f"{scan.folder_name}.tif"

        if self._geotiff_writer is None:
            raise RuntimeError("No GeoTIFFWriter configured in ExportGeospatial")

        return self._geotiff_writer.write_geotiff(image_path, target_path, scan.bbox)


    def export_stac_item(self, folder_name: str, base_url: str = "") -> dict[str, Any]:
        """Generate a STAC Item v1.0.0 specification compliant metadata dictionary."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")
        bbox = scan.bbox
        acq = scan.acquisition
        metadata = scan.metadata or {}

        acq_time = getattr(acq, "acquired_at", None) or getattr(acq, "begin_position", None)
        if acq_time:
            if acq_time.tzinfo is None:
                acq_time = acq_time.replace(tzinfo=timezone.utc)
            dt_str = acq_time.isoformat()
        else:
            from datetime import datetime
            dt_str = datetime.now(timezone.utc).isoformat()

        end_time = getattr(acq, "end_position", None)
        end_str = end_time.isoformat() if end_time else dt_str

        platform_raw = (getattr(acq, "satellite", None) or getattr(acq, "platform", "sentinel-1")).lower()
        constellation = "sentinel-1" if "sentinel" in platform_raw else ("umbra" if "umbra" in platform_raw else "sar")

        coordinates = [[
            [bbox.min_longitude, bbox.min_latitude],
            [bbox.max_longitude, bbox.min_latitude],
            [bbox.max_longitude, bbox.max_latitude],
            [bbox.min_longitude, bbox.max_latitude],
            [bbox.min_longitude, bbox.min_latitude],
        ]]

        clean_base = base_url.rstrip("/")

        stac_item: dict[str, Any] = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/sar/v1.0.0/schema.json",
                "https://stac-extensions.github.io/sat/v1.0.0/schema.json",
            ],
            "id": scan.folder_name,
            "geometry": {
                "type": "Polygon",
                "coordinates": coordinates,
            },
            "bbox": [
                bbox.min_longitude,
                bbox.min_latitude,
                bbox.max_longitude,
                bbox.max_latitude,
            ],
            "properties": {
                "datetime": dt_str,
                "start_datetime": dt_str,
                "end_datetime": end_str,
                "platform": platform_raw,
                "constellation": constellation,
                "sar:instrument_mode": getattr(acq, "sensor_mode", None) or "IW",
                "sar:frequency_band": "C" if "sentinel" in platform_raw else "X",
                "sar:polarizations": list(acq.polarizations) if acq.polarizations else ["VV"],
                "sar:product_type": "GRD",
                "sat:orbit_state": (acq.orbit_direction or "descending").lower(),
                "sat:relative_orbit": acq.relative_orbit or 0,
                "title": f"SAR Scan {scan.folder_name}",
                "description": f"Synthetic Aperture Radar capture over {metadata.get('aoi_name', 'AOI')}",
                "created": dt_str,
                "updated": dt_str,
            },
            "assets": {
                "thumbnail": {
                    "href": f"{clean_base}/static/output/{scan.folder_name}/images/{Path(scan.image_path).name}",
                    "type": "image/png",
                    "title": "SAR Overview Image",
                    "roles": ["thumbnail", "overview"],
                },
                "geotiff": {
                    "href": f"{clean_base}/api/scan/{scan.folder_name}/export/geotiff",
                    "type": "image/tiff; application=geotiff",
                    "title": "Georeferenced Cloud-Optimized GeoTIFF",
                    "roles": ["data"],
                },
                "detections": {
                    "href": f"{clean_base}/api/scan/{scan.folder_name}/export/geojson",
                    "type": "application/geo+json",
                    "title": "Vessel Detections GeoJSON",
                    "roles": ["metadata", "annotations"],
                },
            },
            "links": [
                {
                    "rel": "self",
                    "href": f"{clean_base}/api/scan/{scan.folder_name}/export/stac",
                    "type": "application/json",
                },
                {
                    "rel": "root",
                    "href": f"{clean_base}/api/scans",
                    "type": "application/json",
                },
            ],
        }

        return stac_item

    def export_geojson(self, folder_name: str) -> dict[str, Any]:
        """Generate GeoJSON FeatureCollection of all detected vessels and OBBs."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")
        image_path = Path(scan.image_path)
        folder_dir = image_path.parent

        # Check existing geojson files first
        candidates = [
            folder_dir / f"{image_path.stem}_detections.geojson",
            folder_dir / "detections.geojson",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    with open(c, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                            return data
                except Exception:
                    pass

        # Synthesize from scan metadata detections
        metadata = scan.metadata or {}
        raw_detections = metadata.get("detections", [])
        features: list[dict[str, Any]] = []

        bbox = scan.bbox
        for idx, det in enumerate(raw_detections):
            if not isinstance(det, dict):
                continue

            lat = det.get("latitude")
            lon = det.get("longitude")
            if lat is None or lon is None:
                # Estimate from pixel coordinates if lat/lon not present
                px = det.get("pixel_x") or det.get("center_x") or 0.0
                py = det.get("pixel_y") or det.get("center_y") or 0.0
                # Fallback to bbox center if missing
                lon = bbox.min_longitude + (bbox.max_longitude - bbox.min_longitude) * 0.5
                lat = bbox.min_latitude + (bbox.max_latitude - bbox.min_latitude) * 0.5

            props = dict(det)
            props["feature_id"] = idx + 1
            props["scan_folder"] = scan.folder_name

            # Check if polygon coordinates exist for OBB
            polygon_coords = det.get("polygon_geo") or det.get("obb_coordinates")
            if polygon_coords and isinstance(polygon_coords, list) and len(polygon_coords) >= 4:
                geometry = {
                    "type": "Polygon",
                    "coordinates": [polygon_coords],
                }
            else:
                geometry = {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)],
                }

            features.append({
                "type": "Feature",
                "id": idx + 1,
                "geometry": geometry,
                "properties": props,
            })

        return {
            "type": "FeatureCollection",
            "crs": {
                "type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
            },
            "features": features,
        }
