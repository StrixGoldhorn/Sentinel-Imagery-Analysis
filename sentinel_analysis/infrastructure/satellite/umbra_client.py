"""Umbra Open Data sub-meter SAR catalog client.

Provides open access to Umbra's high-resolution (<1.0m GSD) commercial X-band SAR
archive hosted on AWS Open Data under CC BY 4.0 license. Allows querying open spotlight
and stripmap collections over global maritime choke points, ports, and naval transit lanes.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
import urllib.parse
import xml.etree.ElementTree as ET

import requests

from sentinel_analysis.domain.entities import BoundingBox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UmbraSARScene:
    """Represents a high-resolution X-band SAR scene from Umbra Open Data."""

    scene_id: str
    target_name: str
    timestamp: datetime
    resolution_meters: float
    polarization: str
    bbox: BoundingBox
    tiff_url: str
    stac_url: str

    def to_dict(self) -> dict[str, Any]:
        """Convert scene to serializable dictionary."""
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["bbox"] = self.bbox.as_list()
        return d


class UmbraOpenDataClient:
    """Client for querying Umbra's public open-access SAR STAC catalog."""

    CATALOG_BASE_URL = "https://umbra-open-data-catalog.s3.amazonaws.com"

    # Well-known maritime monitoring choke points with frequent Umbra open collections
    MARITIME_MONITORING_SITES: dict[str, dict[str, Any]] = {
        "singapore_strait": {
            "name": "Singapore Strait",
            "task_name": "Port of Singapore, Singapore",
            "lat": 1.264,
            "lon": 103.838,
            "bbox": [103.808, 1.234, 103.868, 1.294],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Singapore%2C%20Singapore/004da100-a5fa-4050-9fce-080bdb0b53ac/2025-10-17-15-45-35_UMBRA-09/2025-10-17-15-45-35_UMBRA-09_GEC.tif",
        },
        "panama_canal": {
            "name": "Panama Canal",
            "task_name": "Panama Canal, Panama",
            "lat": 8.974,
            "lon": -79.581,
            "bbox": [-79.605, 8.950, -79.558, 8.997],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Panama%20Canal%2C%20Panama/020c56f0-f1a1-42e0-b11b-3049cf5e11e8/2025-06-25-03-43-16_UMBRA-09/2025-06-25-03-43-16_UMBRA-09_GEC.tif",
        },
        "port_of_rotterdam": {
            "name": "Port of Rotterdam",
            "task_name": "Port of Rotterdam, Netherlands",
            "lat": 51.885,
            "lon": 4.286,
            "bbox": [4.248, 51.861, 4.325, 51.909],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Rotterdam%2C%20Netherlands/00864c2c-0b0f-49ef-b283-997735b27878/2025-07-29-11-17-12_UMBRA-08/2025-07-29-11-17-12_UMBRA-08_GEC.tif",
        },
        "port_of_antwerp": {
            "name": "Port of Antwerp",
            "task_name": "Port of Antwerp, Belgium",
            "lat": 51.261,
            "lon": 4.351,
            "bbox": [4.312, 51.236, 4.391, 51.286],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Antwerp%2C%20Belgium/05a3cf53-5995-475a-aa31-c801030d7ade/2025-05-15-21-57-55_UMBRA-08/2025-05-15-21-57-55_UMBRA-08_GEC.tif",
        },
        "port_of_hong_kong": {
            "name": "Port of Hong Kong",
            "task_name": "Port of Hong Kong, Hong Kong",
            "lat": 22.336,
            "lon": 114.121,
            "bbox": [114.095, 22.312, 114.147, 22.360],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Hong%20Kong%2C%20Hong%20Kong/02195613-46e0-4e95-ab35-9abf83c273f6/2025-11-21-02-17-17_UMBRA-07/2025-11-21-02-17-17_UMBRA-07_GEC.tif",
        },
        "port_of_busan": {
            "name": "Port of Busan",
            "task_name": "Port of Busan, South Korea",
            "lat": 35.105,
            "lon": 129.082,
            "bbox": [129.048, 35.077, 129.117, 35.133],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Busan%2C%20South%20Korea/59e40c71-60d9-4cee-8951-da6f30be905f/2025-03-05-01-59-59_UMBRA-08/2025-03-05-01-59-59_UMBRA-08_GEC.tif",
        },
        "port_of_jebel_ali": {
            "name": "Port of Jebel Ali",
            "task_name": "Port of Jebel Ali, United Arab Emirates",
            "lat": 25.002,
            "lon": 55.056,
            "bbox": [55.021, 24.971, 55.090, 25.034],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Jebel%20Ali%2C%20United%20Arab%20Emirates/4044f1d2-78e5-49ad-908f-6c440ef84407/2025-09-05-18-44-34_UMBRA-09/2025-09-05-18-44-34_UMBRA-09_GEC.tif",
        },
        "port_of_long_beach": {
            "name": "Port of Long Beach",
            "task_name": "Port of Long Beach, California, United States",
            "lat": 33.746,
            "lon": -118.236,
            "bbox": [-118.274, 33.714, -118.198, 33.778],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Long%20Beach%2C%20California%2C%20United%20States/2f03424b-9943-4350-80ef-7f6e0d2eae8f/2025-11-19-06-43-23_UMBRA-09/2025-11-19-06-43-23_UMBRA-09_GEC.tif",
        },
        "port_of_hamburg": {
            "name": "Port of Hamburg",
            "task_name": "Port of Hamburg, Germany",
            "lat": 53.508,
            "lon": 9.939,
            "bbox": [9.895, 53.482, 9.983, 53.535],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/sar-data/tasks/Port%20of%20Hamburg%2C%20Germany/41c7822a-3747-4ef8-8e68-aa460d0e03ad/2025-05-09-10-19-30_UMBRA-10/2025-05-09-10-19-30_UMBRA-10_GEC.tif",
        },
        "suez_canal": {
            "name": "Suez Canal",
            "task_name": None,
            "lat": 29.97,
            "lon": 32.57,
            "bbox": [32.53, 29.93, 32.61, 30.01],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/open-data/suez_canal/latest.tif",
        },
        "strait_of_gibraltar": {
            "name": "Strait of Gibraltar",
            "task_name": None,
            "lat": 36.15,
            "lon": -5.35,
            "bbox": [-5.38, 36.12, -5.32, 36.18],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/open-data/strait_of_gibraltar/latest.tif",
        },
        "strait_of_malacca": {
            "name": "Strait of Malacca",
            "task_name": None,
            "lat": 2.21,
            "lon": 102.26,
            "bbox": [102.23, 2.18, 102.29, 2.24],
            "sample_cog_url": "https://umbra-open-data-catalog.s3.amazonaws.com/open-data/strait_of_malacca/latest.tif",
        },
    }

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 25.0,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout

    @classmethod
    def get_maritime_sites(cls) -> dict[str, dict[str, Any]]:
        """Return list of pre-configured maritime monitoring target sites."""
        return dict(cls.MARITIME_MONITORING_SITES)

    def search_nearby_site(self, bbox: BoundingBox) -> str | None:
        """Find matching known Umbra maritime monitoring site if the bbox overlaps or is nearby."""
        b_c_lon = (bbox.min_longitude + bbox.max_longitude) / 2.0
        b_c_lat = (bbox.min_latitude + bbox.max_latitude) / 2.0

        best_site: str | None = None
        best_score = float("inf")

        for site_key, site_info in self.MARITIME_MONITORING_SITES.items():
            s_bbox = site_info["bbox"]
            s_c_lon = (s_bbox[0] + s_bbox[2]) / 2.0
            s_c_lat = (s_bbox[1] + s_bbox[3]) / 2.0

            # Direct overlap check
            overlaps = (
                bbox.min_longitude <= s_bbox[2]
                and bbox.max_longitude >= s_bbox[0]
                and bbox.min_latitude <= s_bbox[3]
                and bbox.max_latitude >= s_bbox[1]
            )

            dist_deg = ((b_c_lon - s_c_lon) ** 2 + (b_c_lat - s_c_lat) ** 2) ** 0.5

            if overlaps:
                # Rank directly overlapping sites by proximity of centers
                if dist_deg < best_score:
                    best_score = dist_deg
                    best_site = site_key
            elif dist_deg <= 0.35:  # within ~40 km of port center
                # Rank nearby non-overlapping sites lower than overlapping ones
                score = 100.0 + dist_deg
                if score < best_score:
                    best_score = score
                    best_site = site_key

        return best_site

    @classmethod
    def MARITIME_MONITORING_sites_items(cls):
        return cls.MARITIME_MONITORING_SITES.items()

    def parse_stac_item(self, item: dict[str, Any]) -> UmbraSARScene | None:
        """Parse a STAC Item GeoJSON into an UmbraSARScene object."""
        if not isinstance(item, dict) or item.get("type") not in ("Feature", None):
            return None

        scene_id = str(item.get("id") or "")
        if not scene_id:
            return None

        props = item.get("properties") or {}
        datetime_raw = props.get("datetime") or props.get("start_datetime")
        try:
            ts = datetime.fromisoformat(str(datetime_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = datetime.now(timezone.utc)

        # Resolution (Umbra is sub-meter, typically 0.25m, 0.35m, 0.5m, or 1.0m)
        resolution = float(
            props.get("resolution")
            or props.get("sar:resolution_range")
            or props.get("sar:resolution_azimuth")
            or 0.5
        )

        polarization = str(props.get("sar:polarizations", ["VV"])[0] if isinstance(props.get("sar:polarizations"), list) else "VV")
        target_name = str(props.get("umbra:target_name") or props.get("target_name") or scene_id)

        # Bounding box
        item_bbox = item.get("bbox")
        if isinstance(item_bbox, (list, tuple)) and len(item_bbox) == 4:
            try:
                scene_bbox = BoundingBox(
                    min_longitude=float(item_bbox[0]),
                    min_latitude=float(item_bbox[1]),
                    max_longitude=float(item_bbox[2]),
                    max_latitude=float(item_bbox[3]),
                )
            except Exception:
                scene_bbox = BoundingBox(103.808, 1.234, 103.868, 1.294)
        else:
            scene_bbox = BoundingBox(103.808, 1.234, 103.868, 1.294)

        # Assets
        assets = item.get("assets") or {}
        tiff_asset = assets.get("geotiff") or assets.get("raster") or assets.get("data") or {}
        tiff_url = str(tiff_asset.get("href") or f"{self.CATALOG_BASE_URL}/data/{scene_id}.tif")

        links = item.get("links") or []
        stac_url = ""
        for link in links:
            if link.get("rel") == "self":
                stac_url = str(link.get("href") or "")
                break
        if not stac_url:
            stac_url = f"{self.CATALOG_BASE_URL}/stac/{scene_id}.json"

        return UmbraSARScene(
            scene_id=scene_id,
            target_name=target_name,
            timestamp=ts,
            resolution_meters=resolution,
            polarization=polarization,
            bbox=scene_bbox,
            tiff_url=tiff_url,
            stac_url=stac_url,
        )

    def fetch_site_scenes(self, site_key: str, limit: int = 10) -> list[UmbraSARScene]:
        """Fetch indexed open scenes for a known maritime target site."""
        site = self.MARITIME_MONITORING_SITES.get(site_key)
        if not site:
            return []

        task_name = site.get("task_name")
        b = site["bbox"]
        scene_bbox = BoundingBox(min_longitude=b[0], min_latitude=b[1], max_longitude=b[2], max_latitude=b[3])

        # 1. First attempt: Query S3 prefix for verified sub-meter GEC GeoTIFFs if task_name configured
        if task_name:
            try:
                quoted_task = urllib.parse.quote(task_name)
                s3_list_url = f"{self.CATALOG_BASE_URL}/?prefix=sar-data/tasks/{quoted_task}/&max-keys=100"
                resp = self._session.get(s3_list_url, timeout=min(5.0, self._timeout))
                if resp.status_code == 200 and resp.content:
                    root = ET.fromstring(resp.content)
                    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
                    keys = [elem.text for elem in root.findall(".//s3:Key", ns) if elem.text]
                    gec_keys = [k for k in keys if k.endswith("GEC.tif")]
                    if gec_keys:
                        gec_keys.sort(reverse=True)
                        results: list[UmbraSARScene] = []
                        for key in gec_keys[:limit]:
                            parts = key.split("/")
                            fname = parts[-1]
                            scene_id = fname.replace("_GEC.tif", "")
                            ts_str = scene_id.split("_")[0]
                            try:
                                ts = datetime.strptime(ts_str, "%Y-%m-%d-%H-%M-%S").replace(tzinfo=timezone.utc)
                            except Exception:
                                ts = datetime.now(timezone.utc)

                            encoded_key = "/".join(urllib.parse.quote(p) for p in parts)
                            tiff_url = f"{self.CATALOG_BASE_URL}/{encoded_key}"
                            stac_url = tiff_url.replace("_GEC.tif", ".stac.v2.json")

                            results.append(
                                UmbraSARScene(
                                    scene_id=scene_id,
                                    target_name=site["name"],
                                    timestamp=ts,
                                    resolution_meters=0.5,
                                    polarization="VV",
                                    bbox=scene_bbox,
                                    tiff_url=tiff_url,
                                    stac_url=stac_url,
                                )
                            )
                        if results:
                            try:
                                top_resp = self._session.get(results[0].stac_url, timeout=min(2.0, self._timeout))
                                if top_resp.status_code == 200:
                                    stac_json = top_resp.json()
                                    parsed = self.parse_stac_item(stac_json)
                                    if parsed and parsed.bbox:
                                        first = results[0]
                                        results[0] = UmbraSARScene(
                                            scene_id=first.scene_id,
                                            target_name=first.target_name,
                                            timestamp=first.timestamp,
                                            resolution_meters=first.resolution_meters,
                                            polarization=first.polarization,
                                            bbox=parsed.bbox,
                                            tiff_url=first.tiff_url,
                                            stac_url=first.stac_url,
                                        )
                            except Exception:
                                pass
                            return results
            except Exception as exc:
                logger.debug("Umbra S3 prefix query for %s failed: %s", site_key, exc)

        # 2. Second attempt: Check STAC collection API endpoint
        url = f"{self.CATALOG_BASE_URL}/stac/collections/{site_key}/items"
        try:
            resp = self._session.get(url, timeout=min(5.0, self._timeout))
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                results = []
                for f in features[:limit]:
                    scene = self.parse_stac_item(f)
                    if scene:
                        results.append(scene)
                if results:
                    return results
        except Exception as exc:
            logger.debug("Umbra remote query fallback for site %s: %s", site_key, exc)

        # 3. Fallback descriptor with sample COG URL
        sample_url = site.get("sample_cog_url") or f"{self.CATALOG_BASE_URL}/open-data/{site_key}/latest.tif"
        return [
            UmbraSARScene(
                scene_id=f"umbra_{site_key}_open_01",
                target_name=site["name"],
                timestamp=datetime.now(timezone.utc),
                resolution_meters=0.5,
                polarization="VV",
                bbox=scene_bbox,
                tiff_url=sample_url,
                stac_url=f"{self.CATALOG_BASE_URL}/stac/{site_key}.json",
            )
        ]


MARITIME_MONITORING_SITES = UmbraOpenDataClient.MARITIME_MONITORING_SITES

