from typing import List, Dict, Any
from datetime import datetime, timezone
from ingestion.base_plugin import BaseAISScraperPlugin

class MockPublicAISPlugin(BaseAISScraperPlugin):
    def authenticate(self) -> None:
        # Perform HTTP session setups, token retrievals, etc.
        pass

    def fetch_data(self, bbox: List[float], time_range: tuple) -> Any:
        # Returning sample mock payload data typically gathered from a raw API request
        return [
            {
                "imo_number": "9175535",
                "mmsi_number": "311029200",
                "ship_name": "CARGO KING",
                "ship_type": "Cargo",
                "call_sign": "C6YM7",
                "lat": (bbox[1] + bbox[3]) / 2,
                "lon": (bbox[0] + bbox[2]) / 2,
                "sog": 14.5,
                "cog": 120.0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]

    def parse_data(self, raw_data: Any) -> List[Dict[str, Any]]:
        parsed = []
        for item in raw_data:
            parsed.append({
                "vessel": {
                    "imo": item["imo_number"], "mmsi": item["mmsi_number"], 
                    "vessel_name": item["ship_name"], "vessel_type": item["ship_type"], "callsign": item["call_sign"]
                },
                "location": {
                    "latitude": item["lat"], "longitude": item["lon"],
                    "speed": item["sog"], "heading": item["cog"], "timestamp": item["timestamp"]
                }
            })
        return parsed