"""Reverse-geocoding adapters."""

import json
import urllib.request


class NominatimLocationResolver:
    def __init__(self, user_agent: str = "SentinelImageryAnalysis/1.0", timeout: float = 5) -> None:
        self._user_agent = user_agent
        self._timeout = timeout

    def resolve(self, latitude: float, longitude: float) -> str:
        url = (
            "https://nominatim.openstreetmap.org/reverse"
            f"?format=json&lat={latitude}&lon={longitude}&zoom=10&accept-language=en"
        )
        try:
            request = urllib.request.Request(url, headers={"User-Agent": self._user_agent})
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            address = data.get("address", {})
            name = address.get("city") or address.get("town") or address.get("county") or address.get("state")
            return name or data.get("display_name", "Open Sea").split(",")[0]
        except Exception:
            return f"Area at {latitude:.2f}N, {longitude:.2f}E"

