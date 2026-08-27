from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any


@dataclass(frozen=True)
class VesselMetadata:
    """Legacy normalized vessel metadata used by older plugins."""

    imo: str
    mmsi: str
    vessel_name: str = ""
    vessel_type: str = ""
    callsign: str = ""


@dataclass(frozen=True)
class VesselLocation:
    """Legacy normalized telemetry record used by older plugins."""

    mmsi: str
    latitude: float
    longitude: float
    speed: float | None
    heading: float | None
    timestamp: datetime

class BaseAISScraperPlugin(ABC):
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def authenticate(self) -> None:
        """Handle provider-specific authentication or session setup."""
        pass

    @abstractmethod
    def fetch_data(self, bbox: List[float], time_range: tuple) -> Any:
        """
        Retrieve raw AIS data for a specific Area of Interest (AOI) and time window.
        """
        pass

    @abstractmethod
    def parse_data(self, raw_data: Any) -> List[Dict[str, Any]]:
        """
        Normalize the provider's specific data format into standard Python dictionaries.
        Expected dictionary format inside the returned list:
        {
            "vessel": {"imo": str, "mmsi": str, "vessel_name": str, "vessel_type": str, "callsign": str},
            "location": {"latitude": float, "longitude": float, "speed": float, "heading": float, "timestamp": str}
        }
        """
        pass
