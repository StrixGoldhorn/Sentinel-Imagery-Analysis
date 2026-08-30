"""UDP NMEA 0183 AIS stream receiver plugin ported from SeaSentry."""

import logging
import queue
import socket
import threading
from datetime import datetime, timezone
from typing import Any

from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition

logger = logging.getLogger(__name__)


def _background_udp_listener(
    host: str,
    port: int,
    stop_event: threading.Event,
    msg_buffer: queue.Queue[str],
) -> None:
    """Listens to UDP socket and buffers incoming NMEA lines."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        sock.settimeout(1.0)
        logger.info("Continuous AIS UDP listener started on %s:%d", host, port)
        while not stop_event.is_set():
            try:
                data, _ = sock.recvfrom(4096)
                lines = data.decode("utf-8", errors="ignore").splitlines()
                for line in lines:
                    msg = line.strip()
                    if msg:
                        msg_buffer.put(msg)
            except socket.timeout:
                continue
            except Exception as exc:
                if not stop_event.is_set():
                    logger.error("Socket error in AIS UDP listener: %s", exc)
                break
    except Exception as exc:
        logger.error("Failed to start AIS UDP socket on %s:%d: %s", host, port, exc)
    finally:
        sock.close()
        logger.info("AIS UDP listener stopped.")


class UDPListenerPlugin:
    """Listens for and decodes local/network NMEA 0183 AIS broadcast messages."""

    name = "UDPListenerPlugin"

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 10110,
        auto_start: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self._msg_buffer: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._vessel_cache: dict[str, dict[str, Any]] = {}
        if auto_start:
            self.start_listener()

    def start_listener(self) -> None:
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return
        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=_background_udp_listener,
            args=(self.host, self.port, self._stop_event, self._msg_buffer),
            daemon=True,
        )
        self._listener_thread.start()

    def stop_listener(self) -> None:
        self._stop_event.set()
        if self._listener_thread is not None and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=2.0)
        self._listener_thread = None

    def push_message(self, nmea_sentence: str) -> None:
        """Inject an NMEA sentence directly (useful for testing and integration)."""
        if nmea_sentence and nmea_sentence.strip():
            self._msg_buffer.put(nmea_sentence.strip())

    def authenticate(self) -> None:
        """UDP stream requires no authentication."""
        return None

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange = (None, None),
    ) -> list[AISRecord]:
        raw_messages: list[str] = []
        while not self._msg_buffer.empty():
            try:
                raw_messages.append(self._msg_buffer.get_nowait())
            except queue.Empty:
                break

        records = self.parse_data(raw_messages)
        # Filter records to bounding box
        return [
            rec
            for rec in records
            if (
                bbox.min_latitude <= rec.position.latitude <= bbox.max_latitude
                and bbox.min_longitude <= rec.position.longitude <= bbox.max_longitude
            )
        ]

    def parse_data(self, messages: list[str]) -> list[AISRecord]:
        records: list[AISRecord] = []
        for message in messages:
            try:
                dict_msg = self._decode_nmea(message)
                if not dict_msg:
                    continue

                msg_type = dict_msg.get("msg_type")
                mmsi = dict_msg.get("mmsi")
                if not mmsi:
                    continue
                mmsi_str = str(mmsi)

                # Static metadata update (Type 5 or 24)
                if msg_type in (5, 24):
                    ship_name = dict_msg.get("shipname") or dict_msg.get("name")
                    imo = dict_msg.get("imo")
                    ship_type = dict_msg.get("type_and_cargo")
                    self._vessel_cache[mmsi_str] = {
                        "name": str(ship_name).strip() if ship_name else None,
                        "imo": str(imo).strip() if imo else None,
                        "vessel_type": str(ship_type).strip() if ship_type else None,
                    }
                    continue

                # Position updates (Types 1, 2, 3, 18, 19, 27)
                lat = dict_msg.get("lat")
                lon = dict_msg.get("lon")
                if lat is None or lon is None:
                    continue

                lat_f = float(lat)
                lon_f = float(lon)
                if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
                    continue

                speed_raw = dict_msg.get("speed")
                speed: float | None = None
                if speed_raw is not None and float(speed_raw) < 102.3:
                    speed = max(0.0, float(speed_raw))

                heading_raw = dict_msg.get("heading")
                heading: float | None = None
                if heading_raw is not None and float(heading_raw) < 511:
                    heading = float(heading_raw) % 360

                course_raw = dict_msg.get("course")
                if heading is None and course_raw is not None and float(course_raw) < 360:
                    heading = float(course_raw) % 360

                cached = self._vessel_cache.get(mmsi_str, {})
                imo_val = cached.get("imo") or dict_msg.get("imo")
                imo_str = str(imo_val) if imo_val else f"UNKNOWN-{mmsi_str}"
                name_val = cached.get("name") or dict_msg.get("shipname")

                vessel = Vessel(
                    imo=imo_str,
                    mmsi=mmsi_str,
                    name=str(name_val).strip() if name_val else None,
                    vessel_type=cached.get("vessel_type"),
                    callsign=dict_msg.get("callsign"),
                )
                position = VesselPosition(
                    mmsi=mmsi_str,
                    latitude=lat_f,
                    longitude=lon_f,
                    timestamp=datetime.now(timezone.utc),
                    speed=speed,
                    heading=heading,
                )
                records.append(AISRecord(vessel, position))

            except Exception as exc:
                logger.debug("Failed to parse NMEA AIS sentence %s: %s", message, exc)

        return records

    @staticmethod
    def _decode_nmea(sentence: str) -> dict[str, Any] | None:
        """Decode NMEA sentence using pyais if available, with graceful fallback."""
        try:
            from pyais import decode

            decoded = decode(sentence)
            return decoded.asdict()
        except ImportError:
            pass
        except Exception:
            pass

        # Lightweight fallback parser for basic AIVDM/AIVDO position reports
        return _fallback_nmea_decode(sentence)


def _fallback_nmea_decode(sentence: str) -> dict[str, Any] | None:
    """Basic fallback 6-bit ASCII decoder for type 1/2/3 AIS messages when pyais is not installed."""
    parts = sentence.strip().split(",")
    if len(parts) < 6 or not parts[0].endswith(("VDM", "VDO")):
        return None

    payload = parts[5]
    if not payload:
        return None

    bit_string = ""
    for char in payload:
        ascii_val = ord(char) - 48
        if ascii_val > 40:
            ascii_val -= 8
        if not (0 <= ascii_val < 64):
            return None
        bit_string += f"{ascii_val:06b}"

    if len(bit_string) < 38:
        return None

    msg_type = int(bit_string[0:6], 2)
    mmsi = int(bit_string[8:38], 2)

    if msg_type in (1, 2, 3) and len(bit_string) >= 168:
        sog_raw = int(bit_string[46:56], 2)
        lon_raw = int(bit_string[61:89], 2)
        lat_raw = int(bit_string[89:116], 2)
        cog_raw = int(bit_string[116:128], 2)
        hdg_raw = int(bit_string[128:137], 2)

        # 2's complement conversion
        if lon_raw >= (1 << 27):
            lon_raw -= 1 << 28
        if lat_raw >= (1 << 26):
            lat_raw -= 1 << 27

        lon = lon_raw / 600000.0
        lat = lat_raw / 600000.0
        speed = sog_raw / 10.0 if sog_raw < 1023 else None
        course = cog_raw / 10.0 if cog_raw < 3600 else None
        heading = hdg_raw if hdg_raw < 511 else None

        return {
            "msg_type": msg_type,
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "speed": speed,
            "course": course,
            "heading": heading,
        }

    return {"msg_type": msg_type, "mmsi": mmsi}
