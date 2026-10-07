"""Global Fishing Watch (GFW) API client and AIS plugin.

Integrates with Global Fishing Watch's open developer API (gateway.api.globalfishingwatch.org)
to retrieve vessel activity, loitering events, and intentional AIS disabling gap events.
Cross-corroborates radar-detected dark vessels against GFW dark fleet / AIS gap records.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.application.shutdown import shutdown_coordinator
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition
from sentinel_analysis.infrastructure.ais.zone_splitter import deduplicate_ais_records

logger = logging.getLogger(__name__)


class GlobalFishingWatchPlugin:
    """Fetch vessel telemetry, fishing events, and AIS gap disabling events from Global Fishing Watch."""

    name = "GlobalFishingWatchPlugin"
    base_url = "https://gateway.api.globalfishingwatch.org/v3"

    def __init__(
        self,
        api_token: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        proxy_url: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.api_token = api_token or os.environ.get("GFW_API_TOKEN")
        self._session = session or requests.Session()
        self._timeout = timeout
        self.proxy_url = proxy_url
        self.user_agent = user_agent

        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}

    def configure(self, config: dict[str, Any]) -> None:
        """Apply dynamic configuration from settings or user input."""
        if not config:
            return
        if "api_token" in config:
            token = str(config.get("api_token") or "").strip() or None
            self.api_token = token
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

    def authenticate(self) -> None:
        """Verify API token if provided."""
        if not self.api_token:
            logger.debug(
                "GlobalFishingWatchPlugin initialized without GFW_API_TOKEN. "
                "Get a free developer token at globalfishingwatch.org/our-apis."
            )
            return

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent or "Sentinel-Imagery-Analysis/1.0",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        """Fetch vessel activity events within the bounding box and time window."""
        if shutdown_coordinator.is_shutting_down:
            return []

        if not self.api_token:
            logger.warning("GlobalFishingWatchPlugin requires a free GFW API token to query live events.")
            return []

        start_time, end_time = time_range
        start_str = start_time.strftime("%Y-%m-%d") if start_time else "2020-01-01"
        end_str = end_time.strftime("%Y-%m-%d") if end_time else datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Query events endpoint for fishing and loitering vessel activity
        url = f"{self.base_url}/events"
        params = {
            "start-date": start_str,
            "end-date": end_str,
            "confidences": "4",
            "format": "json",
            "min-duration-minutes": "10",
        }

        try:
            resp = self._session.get(url, headers=self._get_headers(), params=params, timeout=self._timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.warning("GFW events query failed: %s", exc)
            return []

        records = self.parse_events(payload, bbox, time_range)
        return deduplicate_ais_records(records)

    def fetch_ais_gaps(
        self,
        bbox: BoundingBox,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch intentional AIS disabling gap events for dark vessel cross-validation.
        
        GFW tracks when vessels deliberately turn off Class A AIS transponders
        in international or territorial waters.
        """
        if shutdown_coordinator.is_shutting_down or not self.api_token:
            return []

        start_str = start_time.strftime("%Y-%m-%d") if start_time else "2020-01-01"
        end_str = end_time.strftime("%Y-%m-%d") if end_time else datetime.now(timezone.utc).strftime("%Y-%m-%d")

        url = f"{self.base_url}/events"
        params = {
            "event-types": "gap",
            "start-date": start_str,
            "end-date": end_str,
            "format": "json",
            "limit": "100",
        }

        try:
            resp = self._session.get(url, headers=self._get_headers(), params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("GFW AIS gap query failed: %s", exc)
            return []

        gap_events: list[dict[str, Any]] = []
        entries = data.get("entries", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            pos = entry.get("position") or {}
            lat = pos.get("lat") or entry.get("lat")
            lon = pos.get("lon") or entry.get("lon")
            if lat is None or lon is None:
                continue

            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (ValueError, TypeError):
                continue

            if not (bbox.min_latitude <= lat_f <= bbox.max_latitude and bbox.min_longitude <= lon_f <= bbox.max_longitude):
                continue

            vessel_info = entry.get("vessel") or {}
            mmsi = str(vessel_info.get("ssvid") or vessel_info.get("mmsi") or "UNKNOWN")
            vessel_name = vessel_info.get("name") or "Unknown"

            start_raw = entry.get("start")
            end_raw = entry.get("end")
            duration_hours = float(entry.get("durationHours") or 0.0)

            gap_events.append({
                "event_id": str(entry.get("id") or ""),
                "mmsi": mmsi,
                "vessel_name": vessel_name,
                "gap_start": start_raw,
                "gap_end": end_raw,
                "duration_hours": duration_hours,
                "latitude": lat_f,
                "longitude": lon_f,
                "is_intentional": bool(entry.get("gap_intentional_disabling", True)),
                "source": "Global Fishing Watch",
            })

        return gap_events

    def parse_events(
        self,
        payload: Any,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        """Convert GFW event objects into normalized AISRecord instances."""
        entries: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            entries = payload.get("entries") or payload.get("events") or []
        elif isinstance(payload, list):
            entries = payload

        start_time, end_time = time_range
        records: list[AISRecord] = []

        for item in entries:
            if not isinstance(item, dict):
                continue

            pos = item.get("position") or {}
            lat = pos.get("lat") or item.get("lat")
            lon = pos.get("lon") or item.get("lon")
            if lat is None or lon is None:
                continue

            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (ValueError, TypeError):
                continue

            if not (bbox.min_latitude <= lat_f <= bbox.max_latitude and bbox.min_longitude <= lon_f <= bbox.max_longitude):
                continue

            vessel_data = item.get("vessel") or {}
            mmsi = str(vessel_data.get("ssvid") or vessel_data.get("mmsi") or item.get("mmsi") or "").strip()
            if not mmsi:
                continue

            vessel_name = vessel_data.get("name") or item.get("vessel_name")
            ship_type = vessel_data.get("type") or item.get("type") or "Fishing"

            # Parse event start time
            time_raw = item.get("start") or item.get("timestamp")
            if time_raw:
                try:
                    ts = datetime.fromisoformat(str(time_raw).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    ts = datetime.now(timezone.utc)
            else:
                ts = datetime.now(timezone.utc)

            if start_time is not None and ts < start_time:
                continue
            if end_time is not None and ts > end_time:
                continue

            vessel = Vessel(
                imo=str(vessel_data.get("imo") or f"UNKNOWN-{mmsi}").strip(),
                mmsi=mmsi,
                name=str(vessel_name).strip() if vessel_name else None,
                vessel_type=str(ship_type).strip() if ship_type else "Fishing",
                callsign=str(vessel_data.get("callsign") or "").strip() or None,
            )
            position = VesselPosition(
                mmsi=mmsi,
                latitude=lat_f,
                longitude=lon_f,
                timestamp=ts,
                speed=float(item.get("speed") or 0.0) if item.get("speed") is not None else None,
                heading=None,
            )
            records.append(AISRecord(vessel=vessel, position=position))

        return records
