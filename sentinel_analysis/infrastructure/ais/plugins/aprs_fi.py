"""APRS.fi AIS scraper plugin ported from SeaSentry using Playwright."""

import json
import logging
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition
from sentinel_analysis.infrastructure.ais.zone_splitter import deduplicate_ais_records, split_into_zones

logger = logging.getLogger(__name__)


class PlaywrightAprsSession:
    """Manages an automated, stealth Chromium browser session to access aprs.fi XML2 API."""

    def __init__(self, headless: bool = True) -> None:
        self.temp_profile = tempfile.mkdtemp(prefix="sentinel_aprs_")
        self.headless = headless
        self.pw: Any = None
        self.context: Any = None
        self.page: Any = None
        self._is_running = False

    def start(self) -> None:
        if self._is_running:
            return

        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
        ]

        try:
            self.context = self.pw.chromium.launch_persistent_context(
                user_data_dir=self.temp_profile,
                headless=self.headless,
                channel="chrome",
                args=launch_args,
            )
        except Exception:
            self.context = self.pw.chromium.launch_persistent_context(
                user_data_dir=self.temp_profile,
                headless=self.headless,
                args=launch_args,
            )

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        try:
            from playwright_stealth import Stealth

            Stealth().apply_stealth_sync(self.page)
        except ImportError:
            try:
                from playwright_stealth import stealth_sync

                stealth_sync(self.page)
            except Exception:
                pass

        self.page.goto("https://aprs.fi/", wait_until="domcontentloaded")
        try:
            self.page.wait_for_selector("div#map", timeout=10000)
        except Exception as exc:
            logger.debug("aprs.fi map wait notice: %s", exc)

        self._is_running = True

    def fetch_xml2(self, coords: dict[str, float], timerange: int = 86400, tail: int = 0) -> str:
        if not self._is_running:
            self.start()

        base_url = "https://aprs.fi/xml2"
        req_url = (
            f"{base_url}?box={coords['lat_min']}%2C{coords['long_min']}%2C"
            f"{coords['lat_max']}%2C{coords['long_max']}&timerange={timerange}&tail={tail}"
        )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://aprs.fi/",
            "Origin": "https://aprs.fi",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        try:
            response = self.page.request.get(req_url, headers=headers)
            if not response.ok:
                logger.debug("Fetching aprs.fi XML2 URL failed with status: %s", response.status)
                return ""
            return response.text()
        except Exception as exc:
            logger.error("Exception fetching aprs.fi xml2: %s", exc)
            return ""

    def cleanup(self) -> None:
        try:
            if self.context:
                self.context.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass

        shutil.rmtree(self.temp_profile, ignore_errors=True)
        self._is_running = False


class AprsFiPlugin:
    """Scraper for aprs.fi using Playwright browser stealth ported from SeaSentry."""

    name = "AprsFiPlugin"

    def __init__(
        self,
        session_factory: Callable[[], PlaywrightAprsSession] | None = None,
    ) -> None:
        self._session_factory = session_factory

    def authenticate(self) -> None:
        """Browser handles sessions and cookies upon startup."""
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        zones = split_into_zones(bbox, zone_size_nm=10.0)
        session = self._session_factory() if self._session_factory else PlaywrightAprsSession()
        try:
            session.start()
            all_records: list[AISRecord] = []
            for zone in zones:
                coords = {
                    "lat_min": zone.min_latitude,
                    "lat_max": zone.max_latitude,
                    "long_min": zone.min_longitude,
                    "long_max": zone.max_longitude,
                }
                xml_data = session.fetch_xml2(coords)
                if xml_data:
                    all_records.extend(self.parse_data(xml_data, time_range))
            return deduplicate_ais_records(all_records)
        except Exception as exc:
            logger.error("Error fetching APRS data: %s", exc)
            return []
        finally:
            session.cleanup()

    def parse_data(
        self,
        data: str,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        if not data or not data.strip():
            return []

        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            logger.warning("Failed to parse XML data from aprs.fi: %s", exc)
            return []

        records: list[AISRecord] = []
        for child in root:
            text = (child.text or "").strip()
            if text.startswith("it("):
                match = re.search(r"it\((.*)\);?", text, re.DOTALL)
                if match:
                    try:
                        vessel_json = json.loads(match.group(1))
                        record = self._convert_to_record(vessel_json, time_range)
                        if record is not None:
                            records.append(record)
                    except (json.JSONDecodeError, ValueError) as exc:
                        logger.debug("Failed to decode APRS vessel item: %s", exc)

        return records

    @staticmethod
    def _convert_to_record(
        vessel: dict[str, Any],
        time_range: AISTimeRange = (None, None),
    ) -> AISRecord | None:
        raw_mmsi = vessel.get("name") or vessel.get("mmsi")
        if not raw_mmsi:
            return None
        mmsi_str = str(raw_mmsi).strip()
        if not mmsi_str:
            return None

        lat = vessel.get("lat")
        lon = vessel.get("lng")
        if lat is None or lon is None:
            return None

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            return None

        imo_val = vessel.get("imo")
        imo_str = str(imo_val).strip() if imo_val else f"UNKNOWN-{mmsi_str}"

        ship_name = vessel.get("showname") or vessel.get("name")
        ship_name_str = str(ship_name).strip() if ship_name else None

        raw_time = vessel.get("time")
        if raw_time:
            try:
                ts = datetime.fromtimestamp(float(raw_time), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                ts = datetime.now(timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        start_time, end_time = time_range
        if start_time is not None and ts < start_time:
            return None
        if end_time is not None and ts > end_time:
            return None

        speed_val = vessel.get("speed")
        speed: float | None = None
        if speed_val is not None:
            try:
                speed = max(0.0, float(speed_val))
            except (TypeError, ValueError):
                pass

        course_val = vessel.get("course")
        heading: float | None = None
        if course_val is not None:
            try:
                h_val = float(course_val)
                if 0 <= h_val <= 360:
                    heading = h_val % 360
            except (TypeError, ValueError):
                pass

        v_obj = Vessel(
            imo=imo_str,
            mmsi=mmsi_str,
            name=ship_name_str,
            vessel_type=None,
            callsign=None,
        )
        p_obj = VesselPosition(
            mmsi=mmsi_str,
            latitude=lat_f,
            longitude=lon_f,
            timestamp=ts,
            speed=speed,
            heading=heading,
        )
        return AISRecord(v_obj, p_obj)
