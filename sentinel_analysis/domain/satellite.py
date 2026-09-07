"""Satellite domain models and catalog constants."""

from typing import Any

SATELLITE_CATALOG: dict[str, dict[str, Any]] = {
    "Sentinel-1A": {
        "norad_id": 39634,
        "status": "OPERATIONAL",
        "description": "Primary Sentinel-1 C-SAR (Sun-synchronous dusk/dawn orbit)",
        "launched": "2014-04-03",
    },
    "Sentinel-1B": {
        "norad_id": 41456,
        "status": "DECOMMISSIONED",
        "description": "Retired August 2022 due to power anomaly",
        "launched": "2016-04-25",
    },
    "Sentinel-1C": {
        "norad_id": 62232,
        "status": "OPERATIONAL",
        "description": "Operational C-SAR launched Dec 2024, 180° phased constellation partner to S1A",
        "launched": "2024-12-04",
    },
    "Sentinel-1D": {
        "norad_id": 66315,
        "status": "UPCOMING",
        "description": "Planned C-SAR mission (Sentinel-1 constellation continuity)",
        "launched": "2025-11-04",
    },
}

ALL_SATELLITE_NAMES: list[str] = list(SATELLITE_CATALOG.keys())

DEFAULT_ENABLED_SATELLITES: list[str] = [
    sat for sat, info in SATELLITE_CATALOG.items() if info["status"] == "OPERATIONAL"
]

SATELLITE_NAME_TO_NORAD: dict[str, int] = {
    sat: info["norad_id"] for sat, info in SATELLITE_CATALOG.items()
}

NORAD_TO_SATELLITE_NAME: dict[int, str] = {
    info["norad_id"]: sat for sat, info in SATELLITE_CATALOG.items()
}
