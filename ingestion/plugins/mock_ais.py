from datetime import datetime
from typing import Tuple, List, Any
import random

from ingestion.base_plugin import BaseAISScraperPlugin, VesselMetadata, VesselLocation

class MockAISPlugin(BaseAISScraperPlugin):
    """
    A mock plugin that generates fake AIS data for testing the pipeline.
    """
    def authenticate(self) -> bool:
        # Mock authentication always succeeds
        return True

    def fetch_data(self, bbox: Tuple[float, float, float, float], time_range: Tuple[datetime, datetime]) -> Any:
        # Generate some fake JSON-like data based on the bounding box
        min_lon, min_lat, max_lon, max_lat = bbox
        mock_data = []
        
        # Simulate 3 random vessels
        for i in range(3):
            imo = f"IMO{random.randint(1000000, 9999999)}"
            mmsi = f"2{random.randint(10000000, 99999999)}"
            
            mock_data.append({
                "vessel": {
                    "imo": imo,
                    "mmsi": mmsi,
                    "name": f"Mock Vessel {i}",
                    "type": "Cargo",
                    "callsign": f"MOCK{i}"
                },
                "telemetry": [
                    {
                        "lat": random.uniform(min_lat, max_lat),
                        "lon": random.uniform(min_lon, max_lon),
                        "speed": random.uniform(10.0, 25.0),
                        "heading": random.uniform(0.0, 360.0),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                ]
            })
            
        return mock_data

    def parse_data(self, raw_data: Any) -> Tuple[List[VesselMetadata], List[VesselLocation]]:
        vessels = []
        locations = []
        
        for record in raw_data:
            v_data = record["vessel"]
            vessels.append(VesselMetadata(
                imo=v_data["imo"],
                mmsi=v_data["mmsi"],
                vessel_name=v_data.get("name", ""),
                vessel_type=v_data.get("type", ""),
                callsign=v_data.get("callsign", "")
            ))
            
            for t_data in record["telemetry"]:
                locations.append(VesselLocation(
                    mmsi=v_data["mmsi"],
                    latitude=t_data["lat"],
                    longitude=t_data["lon"],
                    speed=t_data["speed"],
                    heading=t_data["heading"],
                    timestamp=datetime.fromisoformat(t_data["timestamp"])
                ))
                
        return vessels, locations
