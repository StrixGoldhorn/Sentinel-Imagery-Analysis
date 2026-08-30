"""VesselFinder AIS scraper plugin ported from SeaSentry using Playwright."""

import logging
import math
import random
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition

logger = logging.getLogger(__name__)


class PlaywrightVesselFinderSession:
    """Manages an automated, stealth Chromium browser session to pass WAF challenges."""

    def __init__(self, headless: bool = True) -> None:
        self.temp_profile = tempfile.mkdtemp(prefix="sentinel_vf_")
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

        # Try chrome channel first; fallback to standard chromium executable
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

        self.page.goto("https://www.vesselfinder.com/", wait_until="domcontentloaded")
        try:
            self.page.wait_for_selector("div#map-container", timeout=10000)
            time.sleep(2)
        except Exception as exc:
            logger.warning("VesselFinder WAF map wait notice: %s", exc)

        self._is_running = True

    def fetch_mp2(self, coords: dict[str, float], zoom: int = 15) -> bytes:
        if not self._is_running:
            self.start()

        long_min = round(coords["long_min"] * 600000)
        lat_min = round(coords["lat_min"] * 600000)
        long_max = round(coords["long_max"] * 600000)
        lat_max = round(coords["lat_max"] * 600000)

        bbox = f"{long_min}%2C{lat_min}%2C{long_max}%2C{lat_max}"
        req_url = f"https://www.vesselfinder.com/api/pub/mp2?bbox={bbox}&zoom={zoom}&mmsi=0"

        headers = {
            "User-Agent": (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Host": "www.vesselfinder.com",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            response = self.page.request.get(req_url, headers=headers)
            if not response.ok:
                logger.warning("Failed to fetch %s. Status: %s", req_url, response.status)
                return b""
            return response.body()
        except Exception as exc:
            logger.error("Exception fetching VesselFinder mp2 data: %s", exc)
            return b""

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


class VesselFinderPlugin:
    """Scraper for vesselfinder.com using Playwright browser stealth ported from SeaSentry."""

    name = "VesselFinderPlugin"

    SHIP_TYPE_MAP: dict[int, str | None] = {
        0: None,
        1: None,
        2: "Tug",
        3: "Passenger",
        4: "Cargo",
        5: "Fishing",
        6: "Tanker",
        7: "Military",
        8: "Sailing",
    }

    def __init__(
        self,
        session_factory: Callable[[], PlaywrightVesselFinderSession] | None = None,
    ) -> None:
        self._session_factory = session_factory

    def authenticate(self) -> None:
        """Authentication / WAF challenges are handled during browser startup."""
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        coords = {
            "lat_min": bbox.min_latitude,
            "lat_max": bbox.max_latitude,
            "long_min": bbox.min_longitude,
            "long_max": bbox.max_longitude,
        }

        chunks = self._fetch_all_chunks(coords)
        return self.parse_data(chunks)

    def _fetch_all_chunks(self, coords: dict[str, float]) -> list[bytes]:
        session = self._session_factory() if self._session_factory else PlaywrightVesselFinderSession()
        try:
            session.start()
            lat_min, lat_max = coords["lat_min"], coords["lat_max"]
            lon_min, lon_max = coords["long_min"], coords["long_max"]

            lat_step = 10 / 60.0
            avg_lat = (lat_min + lat_max) / 2
            cos_lat = math.cos(math.radians(avg_lat))
            lon_step = 10 / (60.0 * cos_lat) if cos_lat > 0 else lat_step

            all_chunks: list[bytes] = []
            curr_lat = lat_min
            while curr_lat < lat_max:
                next_lat = min(curr_lat + lat_step, lat_max)
                curr_lon = lon_min
                while curr_lon < lon_max:
                    next_lon = min(curr_lon + lon_step, lon_max)
                    chunk_coords = {
                        "lat_min": curr_lat,
                        "lat_max": next_lat,
                        "long_min": curr_lon,
                        "long_max": next_lon,
                    }
                    data = session.fetch_mp2(chunk_coords)
                    if data:
                        all_chunks.append(data)
                    curr_lon += lon_step
                curr_lat += lat_step
            return all_chunks
        except Exception as exc:
            logger.error("Error fetching VesselFinder data: %s", exc)
            return []
        finally:
            session.cleanup()

    def parse_data(self, data: bytes | list[bytes]) -> list[AISRecord]:
        if not data:
            return []

        data_list = data if isinstance(data, list) else [data]
        records: list[AISRecord] = []
        mmsi_seen: set[str] = set()
        zoom_level = 15

        for blob in data_list:
            if not blob or len(blob) < 13:
                continue

            try:
                text_data = blob.decode("utf-8")
                if text_data.startswith("{") or text_data.startswith("["):
                    logger.warning("Received JSON instead of binary VesselFinder data.")
                    continue
            except UnicodeDecodeError:
                pass

            idx = 12
            while idx < len(blob):
                try:
                    if idx + 2 > len(blob):
                        break
                    flags = int.from_bytes(blob[idx : idx + 2], byteorder="big", signed=False)
                    idx += 2

                    ship_type_code = (flags & 0x00F0) >> 4
                    ship_type = self.SHIP_TYPE_MAP.get(ship_type_code, None)

                    if idx + 4 > len(blob):
                        break
                    mmsi_int = int.from_bytes(blob[idx : idx + 4], "big")
                    mmsi_str = str(mmsi_int)
                    idx += 4

                    if idx + 4 > len(blob):
                        break
                    lat = int.from_bytes(blob[idx : idx + 4], "big", signed=True) / 600000.0
                    idx += 4

                    if idx + 4 > len(blob):
                        break
                    lon = int.from_bytes(blob[idx : idx + 4], "big", signed=True) / 600000.0
                    idx += 4

                    if idx + 1 > len(blob):
                        break
                    time_delta_byte = int.from_bytes(blob[idx : idx + 1], "big", signed=False)
                    idx += 1

                    is_negative = (time_delta_byte & 0x80) != 0
                    magnitude = time_delta_byte & 0x7F

                    if is_negative:
                        if magnitude >= 24:
                            days = round(magnitude / 24)
                            minutes_ago = days * 24 * 60
                        else:
                            minutes_ago = magnitude * 60
                    else:
                        minutes_ago = time_delta_byte

                    vessel_timestamp = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)

                    if idx + 1 > len(blob):
                        break
                    ship_name_length = blob[idx]
                    idx += 1

                    if idx + ship_name_length > len(blob):
                        break
                    ship_name_raw = blob[idx : idx + ship_name_length].decode("utf-8", errors="ignore")
                    ship_name = ship_name_raw.replace("\x00", "").strip() or None
                    idx += ship_name_length

                    if zoom_level >= 14:
                        if idx + 10 > len(blob):
                            break
                        idx += 10

                    if mmsi_str not in mmsi_seen:
                        mmsi_seen.add(mmsi_str)
                        vessel = Vessel(
                            imo=f"UNKNOWN-{mmsi_str}",
                            mmsi=mmsi_str,
                            name=ship_name,
                            vessel_type=ship_type,
                            callsign=None,
                        )
                        position = VesselPosition(
                            mmsi=mmsi_str,
                            latitude=lat,
                            longitude=lon,
                            timestamp=vessel_timestamp,
                            speed=None,
                            heading=None,
                        )
                        records.append(AISRecord(vessel, position))

                except Exception as exc:
                    logger.debug("Error parsing vessel data blob at index %d: %s", idx, exc)
                    break

        return records
