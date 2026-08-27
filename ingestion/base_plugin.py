from abc import ABC, abstractmethod
from typing import List, Dict, Any

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