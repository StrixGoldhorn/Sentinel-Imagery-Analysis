"""NASA Alaska Satellite Facility (ASF) DAAC SAR client.

Provides open-access search and discovery across historical and active Synthetic Aperture
Radar (SAR) archives (Sentinel-1A, Sentinel-1C, NISAR, ALOS PALSAR) via the NASA ASF DAAC API.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from sentinel_analysis.domain.entities import BoundingBox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ASFProduct:
    """Represents a SAR product granule indexed by NASA ASF DAAC."""

    granule_name: str
    platform: str
    processing_level: str
    beam_mode: str
    polarization: str
    flight_direction: str
    start_time: datetime
    stop_time: datetime
    orbit: int | None
    relative_orbit: int | None
    download_url: str
    size_mb: float | None
    center_lat: float | None
    center_lon: float | None

    def to_dict(self) -> dict[str, Any]:
        """Convert product to serializable dictionary."""
        d = asdict(self)
        d["start_time"] = self.start_time.isoformat()
        d["stop_time"] = self.stop_time.isoformat()
        return d


class ASFSearchClient:
    """Client for querying the NASA Alaska Satellite Facility (ASF) SAR catalog."""

    API_URL = "https://api.daac.asf.alaska.edu/services/search/param"

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        earthdata_token: str | None = None,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self.earthdata_token = earthdata_token

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Sentinel-Imagery-Analysis/1.0",
        }
        if self.earthdata_token:
            headers["Authorization"] = f"Bearer {self.earthdata_token}"
        return headers

    def search(
        self,
        bbox: BoundingBox,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
        platform: str = "SENTINEL-1",
        processing_level: str = "GRD_HD",
        beam_mode: str = "IW",
        flight_direction: str | None = None,
        max_results: int = 50,
    ) -> list[ASFProduct]:
        """Query ASF DAAC for SAR granules matching bounding box and acquisition filters."""
        # Bounding box format for ASF: min_lon,min_lat,max_lon,max_lat
        bbox_str = f"{bbox.min_longitude:.4f},{bbox.min_latitude:.4f},{bbox.max_longitude:.4f},{bbox.max_latitude:.4f}"

        params: dict[str, str | int] = {
            "bbox": bbox_str,
            "platform": platform,
            "processingLevel": processing_level,
            "beamMode": beam_mode,
            "output": "json",
            "maxResults": max_results,
        }

        if flight_direction:
            params["flightDirection"] = flight_direction.upper()

        if start_date is not None:
            if isinstance(start_date, datetime):
                params["start"] = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                params["start"] = str(start_date)

        if end_date is not None:
            if isinstance(end_date, datetime):
                params["end"] = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                params["end"] = str(end_date)

        try:
            resp = self._session.get(self.API_URL, params=params, headers=self._get_headers(), timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("ASF DAAC search request failed: %s", exc)
            return []

        return self.parse_results(data)

    def parse_results(self, data: Any) -> list[ASFProduct]:
        """Parse raw ASF JSON response into strongly typed ASFProduct list."""
        results: list[ASFProduct] = []

        # ASF returns list of results or list inside list: [[{...}, {...}]]
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, list):
                    items.extend(item for item in entry if isinstance(item, dict))
                elif isinstance(entry, dict):
                    items.append(entry)
        elif isinstance(data, dict) and "results" in data:
            items = [item for item in data.get("results", []) if isinstance(item, dict)]

        for item in items:
            granule_name = str(item.get("granuleName") or item.get("fileName") or "").strip()
            if not granule_name:
                continue

            platform = str(item.get("platform") or "Sentinel-1")
            processing_level = str(item.get("processingLevel") or "GRD_HD")
            beam_mode = str(item.get("beamModeType") or item.get("beamMode") or "IW")
            polarization = str(item.get("polarization") or "VV+VH")
            flight_dir = str(item.get("flightDirection") or "DESCENDING").upper()

            start_raw = item.get("startTime") or item.get("centerTime")
            stop_raw = item.get("stopTime") or item.get("startTime")

            try:
                start_time = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                start_time = datetime.now(timezone.utc)

            try:
                stop_time = datetime.fromisoformat(str(stop_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                stop_time = start_time

            orbit = int(item["orbit"]) if item.get("orbit") is not None else None
            rel_orbit = (
                int(item.get("pathNumber") or item.get("relativeOrbit"))
                if (item.get("pathNumber") or item.get("relativeOrbit")) is not None
                else None
            )
            download_url = str(item.get("downloadUrl") or item.get("url") or "")

            try:
                size_mb = float(item.get("sizeMB") or 0.0) if item.get("sizeMB") is not None else None
            except (ValueError, TypeError):
                size_mb = None

            try:
                center_lat = float(item.get("centerLat")) if item.get("centerLat") is not None else None
                center_lon = float(item.get("centerLon")) if item.get("centerLon") is not None else None
            except (ValueError, TypeError):
                center_lat = None
                center_lon = None

            product = ASFProduct(
                granule_name=granule_name,
                platform=platform,
                processing_level=processing_level,
                beam_mode=beam_mode,
                polarization=polarization,
                flight_direction=flight_dir,
                start_time=start_time,
                stop_time=stop_time,
                orbit=orbit,
                relative_orbit=rel_orbit,
                download_url=download_url,
                size_mb=size_mb,
                center_lat=center_lat,
                center_lon=center_lon,
            )
            results.append(product)

        return results
