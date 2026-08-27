"""Reverse-geocoding adapters."""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


class NominatimLocationResolver:
    def __init__(
        self,
        user_agent: str = "SentinelImageryAnalysis/1.0",
        timeout: float = 5,
        opener: Callable[..., object] | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("Nominatim user agent is required")
        if timeout <= 0:
            raise ValueError("Nominatim timeout must be positive")
        self._user_agent = user_agent
        self._timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def resolve(self, latitude: float, longitude: float) -> str:
        query = urllib.parse.urlencode(
            {"format": "json", "lat": latitude, "lon": longitude, "zoom": 10, "accept-language": "en"}
        )
        url = f"https://nominatim.openstreetmap.org/reverse?{query}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": self._user_agent})
            with self._opener(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Nominatim response must be an object")
            address = data.get("address", {})
            if not isinstance(address, dict):
                address = {}
            name = address.get("city") or address.get("town") or address.get("county") or address.get("state")
            display_name = str(data.get("display_name") or "Open Sea")
            return str(name or display_name.split(",")[0])
        except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
            return f"Area at {latitude:.2f}N, {longitude:.2f}E"
