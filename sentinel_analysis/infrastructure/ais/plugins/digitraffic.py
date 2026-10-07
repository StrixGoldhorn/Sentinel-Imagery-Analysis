"""DigiTraffic Marine (Fintraffic) open AIS data provider plugin.

Provides live, open-access AIS telemetry for the Baltic Sea, Gulf of Finland,
and Finnish coastal waters via Fintraffic's public DigiTraffic Marine REST API.
Does not require API keys or prior authentication.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.application.shutdown import shutdown_coordinator
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition
from sentinel_analysis.infrastructure.ais.zone_splitter import deduplicate_ais_records

logger = logging.getLogger(__name__)


class DigiTrafficPlugin:
    """Fetch live AIS telemetry from the open DigiTraffic Marine (Fintraffic) API."""

    name = "DigiTrafficPlugin"
    locations_url = "https://meri.digitraffic.fi/api/ais/v1/locations"
    vessels_url = "https://meri.digitraffic.fi/api/ais/v1/vessels"

    SHIP_TYPE_MAP: Mapping[int, str] = {
        30: "Fishing",
        31: "Tug",
        32: "Tug",
        35: "Military",
        36: "Sailing",
        37: "Pleasure Craft",
        50: "Pilot Vessel",
        51: "SAR",
        52: "Tug",
        53: "Port Tender",
        55: "Law Enforcement",
        58: "Medical Transport",
    }

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 25.0,
        proxy_url: str | None = None,
        user_agent: str | None = None,
        load_metadata: bool = True,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self.proxy_url = proxy_url
        self.user_agent = user_agent
        self.load_metadata = load_metadata
        self._vessel_metadata_cache: dict[str, dict[str, Any]] = {}
        self._metadata_last_fetched: float = 0.0
        self._metadata_ttl_seconds: float = 3600.0

        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}

    def configure(self, config: dict[str, Any]) -> None:
        """Dynamically apply user-configured parameters."""
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
        if "load_metadata" in config:
            self.load_metadata = bool(config.get("load_metadata"))

    def authenticate(self) -> None:
        """DigiTraffic Marine is an open public API and requires no authentication."""
        return None

    @classmethod
    def get_ship_type(cls, ship_type_id: int | None) -> str | None:
        if ship_type_id is None:
            return None
        if ship_type_id in cls.SHIP_TYPE_MAP:
            return cls.SHIP_TYPE_MAP[ship_type_id]
        if 40 <= ship_type_id < 50:
            return "High Speed Craft"
        if 60 <= ship_type_id < 70:
            return "Passenger"
        if 70 <= ship_type_id < 80:
            return "Cargo"
        if 80 <= ship_type_id < 90:
            return "Tanker"
        return "Other"

    def _refresh_vessel_metadata(self) -> None:
        """Optionally populate vessel identity lookup cache."""
        now = time.time()
        if self._vessel_metadata_cache and (now - self._metadata_last_fetched) < self._metadata_ttl_seconds:
            return
        if not self.load_metadata or shutdown_coordinator.is_shutting_down:
            return

        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent or "Sentinel-Imagery-Analysis/1.0",
        }
        try:
            resp = self._session.get(self.vessels_url, headers=headers, timeout=self._timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    cache = {}
                    for item in data:
                        if isinstance(item, dict) and item.get("mmsi"):
                            cache[str(item["mmsi"])] = item
                    self._vessel_metadata_cache = cache
                    self._metadata_last_fetched = now
        except Exception as exc:
            logger.debug("DigiTraffic vessel metadata refresh skipped: %s", exc)

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        """Fetch vessel locations from DigiTraffic Marine and filter against requested bbox and time."""
        if shutdown_coordinator.is_shutting_down:
            return []

        self._refresh_vessel_metadata()

        headers = {
            "Accept": "application/json, application/geo+json",
            "User-Agent": self.user_agent or "Sentinel-Imagery-Analysis/1.0",
        }

        # Request locations from DigiTraffic
        # Format can filter or return regional GeoJSON
        params = {
            "from": f"{bbox.min_longitude},{bbox.min_latitude},{bbox.max_longitude},{bbox.max_latitude}",
        }

        try:
            response = self._session.get(
                self.locations_url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("Error fetching DigiTraffic Marine AIS telemetry: %s", exc)
            return []

        records = self.parse_data(payload, bbox, time_range)
        return deduplicate_ais_records(records)

    def parse_data(
        self,
        payload: Any,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        """Parse GeoJSON FeatureCollection or JSON list into normalized AISRecord domain models."""
        features: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            if payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list):
                features = payload["features"]
            elif isinstance(payload.get("locations"), list):
                features = payload["locations"]
        elif isinstance(payload, list):
            features = payload

        start_time, end_time = time_range
        records: list[AISRecord] = []

        for feat in features:
            if not isinstance(feat, dict):
                continue

            # Support both GeoJSON Feature and flat dictionary
            if feat.get("type") == "Feature":
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates") or []
                if len(coords) < 2:
                    continue
                lon_f = float(coords[0])
                lat_f = float(coords[1])
                props = feat.get("properties") or {}
            else:
                props = feat
                lat = feat.get("latitude") or feat.get("lat")
                lon = feat.get("longitude") or feat.get("lon")
                if lat is None or lon is None:
                    continue
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                except (ValueError, TypeError):
                    continue

            # Bounding box spatial filter
            if not (bbox.min_latitude <= lat_f <= bbox.max_latitude and bbox.min_longitude <= lon_f <= bbox.max_longitude):
                continue

            mmsi = props.get("mmsi")
            if not mmsi:
                continue
            mmsi_str = str(mmsi).strip()

            # Timestamp parsing (DigiTraffic uses epoch timestamp in ms or seconds)
            time_raw = props.get("time") or props.get("timestamp")
            if time_raw:
                try:
                    t_val = float(time_raw)
                    if t_val > 1e11:  # Milliseconds
                        t_val /= 1000.0
                    ts = datetime.fromtimestamp(t_val, tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    ts = datetime.now(timezone.utc)
            else:
                ts = datetime.now(timezone.utc)

            # Temporal filter
            if start_time is not None and ts < start_time:
                continue
            if end_time is not None and ts > end_time:
                continue

            # Speed (SOG in knots)
            sog = props.get("sog")
            speed: float | None = None
            if sog is not None:
                try:
                    speed = max(0.0, float(sog))
                except (ValueError, TypeError):
                    pass

            # Heading (0-360)
            heading_raw = props.get("heading") or props.get("cog")
            heading: float | None = None
            if heading_raw is not None:
                try:
                    h_val = float(heading_raw)
                    if 0 <= h_val <= 360:
                        heading = h_val % 360
                except (ValueError, TypeError):
                    pass

            # Metadata lookup
            meta = self._vessel_metadata_cache.get(mmsi_str, {})
            name = meta.get("name") or props.get("name")
            callsign = meta.get("callSign") or meta.get("callsign") or props.get("callsign")
            imo_val = meta.get("imo") or props.get("imo")
            imo_str = str(imo_val).strip() if imo_val else f"UNKNOWN-{mmsi_str}"

            ship_type_id = meta.get("shipType") or props.get("shipType")
            vessel_type = self.get_ship_type(ship_type_id)

            vessel = Vessel(
                imo=imo_str,
                mmsi=mmsi_str,
                name=str(name).strip() if name else None,
                vessel_type=vessel_type,
                callsign=str(callsign).strip() if callsign else None,
            )
            position = VesselPosition(
                mmsi=mmsi_str,
                latitude=lat_f,
                longitude=lon_f,
                timestamp=ts,
                speed=speed,
                heading=heading,
            )
            records.append(AISRecord(vessel=vessel, position=position))

        return records
