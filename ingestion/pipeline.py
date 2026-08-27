import sqlite3
import traceback
from typing import List, Dict, Any
from ingestion.plugin_manager import PluginManager

def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    # Enforce SQLite foreign key constraints
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn

def run_pipeline(bbox: List[float], time_range: tuple, db_path: str = "data.db") -> Dict[str, Any]:
    manager = PluginManager()
    plugins = manager.get_plugins()
    results = {"total_inserted": 0, "logs": []}

    with get_db_connection(db_path) as conn:
        for PluginClass in plugins:
            plugin_name = PluginClass.__name__
            plugin = PluginClass(config={})
            records_inserted = 0
            error_message = None
            status = "SUCCESS"

            try:
                plugin.authenticate()
                raw_data = plugin.fetch_data(bbox, time_range)
                parsed_data = plugin.parse_data(raw_data)

                for item in parsed_data:
                    vessel = item["vessel"]
                    location = item["location"]

                    cursor = conn.cursor()
                    # Upsert vessel metadata (SQLite >= 3.24)
                    cursor.execute('''
                        INSERT INTO vessels (imo, mmsi, vessel_name, vessel_type, callsign)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(imo, mmsi) DO UPDATE SET
                            vessel_name=excluded.vessel_name,
                            vessel_type=excluded.vessel_type,
                            callsign=excluded.callsign
                    ''', (
                        vessel.get("imo"), vessel.get("mmsi"), vessel.get("vessel_name"), 
                        vessel.get("vessel_type"), vessel.get("callsign")
                    ))
                    
                    cursor.execute('SELECT id FROM vessels WHERE imo=? AND mmsi=?', (vessel.get("imo"), vessel.get("mmsi")))
                    vessel_id = cursor.fetchone()["id"]

                    # Insert location telemetry
                    cursor.execute('''
                        INSERT INTO vessel_locations (vessel_id, latitude, longitude, speed, heading, timestamp, source_plugin)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        vessel_id, location.get("latitude"), location.get("longitude"), 
                        location.get("speed"), location.get("heading"), location.get("timestamp"), plugin_name
                    ))
                    records_inserted += 1
                
                conn.commit()

            except Exception:
                status = "FAILED" if records_inserted == 0 else "PARTIAL"
                error_message = traceback.format_exc()
                conn.rollback()
            
            # Log execution stats per plugin
            conn.execute('''
                INSERT INTO scraper_logs (plugin_name, status, records_inserted, error_message)
                VALUES (?, ?, ?, ?)
            ''', (plugin_name, status, records_inserted, error_message))
            conn.commit()

            results["total_inserted"] += records_inserted
            results["logs"].append({"plugin": plugin_name, "status": status, "records": records_inserted, "error": error_message})
    return results