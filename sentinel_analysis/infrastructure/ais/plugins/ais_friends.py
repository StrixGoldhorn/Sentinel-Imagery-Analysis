import logging
import time
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition
from sentinel_analysis.infrastructure.ais.zone_splitter import deduplicate_ais_records, split_into_zones

logger = logging.getLogger(__name__)


class AISFriendsPlugin:
    """Fetch live AIS telemetry from aisfriends.com bounding box API."""

    name = "AISFriendsPlugin"
    base_url = "https://www.aisfriends.com/vessels/bounding-box"

    SHIP_TYPE_MAP: Mapping[str, list[int]] = {
        "Fishing": [30],
        "Tug": [31, 32, 50, 52, 53, 54],
        "Military": [35],
        "SAR": [51],
        "Law Enforcement": [55],
        "Medical Transport": [58],
        "Sailing": [36],
        "Pleasure Craft": [37],
        "High Speed Craft": list(range(40, 50)),
        "Passenger": list(range(60, 70)),
        "Cargo": list(range(70, 80)),
        "Tanker": list(range(80, 90)),
    }

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        proxy_url: str | None = None,
        user_agent: str | None = None,
        zone_delay: float = 0.0,
        zone_size_nm: float = 10.0,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self.proxy_url = proxy_url
        self.user_agent = user_agent
        self.zone_delay = max(0.0, float(zone_delay))
        self.zone_size_nm = max(0.1, float(zone_size_nm))
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}

    def configure(self, config: dict) -> None:
        """Dynamically apply user-configured proxy, timeout, headers, and zone scan pacing."""
        if not config:
            return
        if "proxy_url" in config:
            proxy = str(config.get("proxy_url") or "").strip() or None
            self.proxy_url = proxy
            if proxy:
                self._session.proxies = {"http": proxy, "https": proxy}
            else:
                self._session.proxies = {}
        if "timeout" in config and config.get("timeout") is not None:
            try:
                self._timeout = float(config["timeout"])
            except (ValueError, TypeError):
                pass
        if "user_agent" in config:
            self.user_agent = str(config.get("user_agent") or "").strip() or None
        if "zone_delay_seconds" in config and config.get("zone_delay_seconds") is not None:
            try:
                self.zone_delay = max(0.0, float(config["zone_delay_seconds"]))
            except (ValueError, TypeError):
                pass
        elif "zone_delay" in config and config.get("zone_delay") is not None:
            try:
                self.zone_delay = max(0.0, float(config["zone_delay"]))
            except (ValueError, TypeError):
                pass
        if "zone_size_nm" in config and config.get("zone_size_nm") is not None:
            try:
                self.zone_size_nm = max(0.1, float(config["zone_size_nm"]))
            except (ValueError, TypeError):
                pass

    @classmethod
    def get_ship_type(cls, ship_type_id: int | None) -> str | None:
        if ship_type_id is None:
            return None
        for ship_type, ids in cls.SHIP_TYPE_MAP.items():
            if ship_type_id in ids:
                return ship_type
        return None

    def authenticate(self) -> None:
        """AISFriends does not require prior credential authentication."""
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        zones = split_into_zones(bbox, zone_size_nm=self.zone_size_nm)
        ua = self.user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/119.36 (KHTML, like Gecko) "
            "Chrome/59.0.3071.115 Safari/537.36"
        )
        headers = {
            "Referer": "https://www.aisfriends.com/",
            "User-Agent": ua,
        }

        all_records: list[AISRecord] = []
        for idx, zone in enumerate(zones):
            if idx > 0 and self.zone_delay > 0:
                time.sleep(self.zone_delay)
            params = {
                "lon_min": zone.min_longitude,
                "lat_min": zone.min_latitude,
                "lon_max": zone.max_longitude,
                "lat_max": zone.max_latitude,
                "zoom": 15,
            }
            try:
                response = self._session.get(
                    self.base_url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, list):
                    all_records.extend(self.parse_data(data, time_range))
            except Exception as exc:
                logger.warning("Error scraping AISFriends zone %s: %s", zone, exc)

        return deduplicate_ais_records(all_records)

    def parse_data(
        self,
        items: list[dict[str, Any]],
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        start_time, end_time = time_range
        records: list[AISRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            mmsi = item.get("mmsi")
            if mmsi is None:
                continue
            mmsi_str = str(mmsi).strip()
            if not mmsi_str:
                continue

            lat = item.get("latitude")
            lon = item.get("longitude")
            if lat is None or lon is None:
                continue

            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (TypeError, ValueError):
                continue

            imo_val = item.get("imo")
            imo_str = str(imo_val).strip() if imo_val else f"UNKNOWN-{mmsi_str}"

            ship_name = item.get("name_ais") or item.get("name")
            ship_name_str = str(ship_name).strip() if ship_name else None

            ship_type_id = item.get("ship_type_id")
            vessel_type = self.get_ship_type(ship_type_id)

            ts_raw = item.get("timestamp_of_position")
            if ts_raw:
                try:
                    ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    ts = datetime.now(timezone.utc)
            else:
                ts = datetime.now(timezone.utc)

            # Filter against satellite pass / ingestion time window
            if start_time is not None and ts < start_time:
                continue
            if end_time is not None and ts > end_time:
                continue

            sog = item.get("speed_over_ground")
            speed: float | None = None
            if sog is not None:
                try:
                    speed = max(0.0, float(sog))
                except (TypeError, ValueError):
                    pass

            heading_raw = item.get("true_heading")
            heading: float | None = None
            if heading_raw is not None:
                try:
                    h_val = float(heading_raw)
                    if 0 <= h_val <= 360:
                        heading = h_val % 360
                except (TypeError, ValueError):
                    pass

            callsign = item.get("callsign")
            callsign_str = str(callsign).strip() if callsign else None

            vessel = Vessel(
                imo=imo_str,
                mmsi=mmsi_str,
                name=ship_name_str,
                vessel_type=vessel_type,
                callsign=callsign_str,
            )
            position = VesselPosition(
                mmsi=mmsi_str,
                latitude=lat_f,
                longitude=lon_f,
                timestamp=ts,
                speed=speed,
                heading=heading,
            )
            records.append(AISRecord(vessel, position))

        return records
