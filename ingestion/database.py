import sqlite3
import os
from pathlib import Path
from typing import List, Tuple
from datetime import datetime

from .base_plugin import VesselMetadata, VesselLocation

DB_PATH = Path("ais_data.db")

def init_db():
    """Initializes the SQLite database with the required schema."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Create vessels table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vessels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imo TEXT NOT NULL,
            mmsi TEXT NOT NULL,
            vessel_name TEXT,
            vessel_type TEXT,
            callsign TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (imo, mmsi)
        )
    ''')

    # Create vessel_locations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vessel_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vessel_id INTEGER NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            speed REAL,
            heading REAL,
            timestamp DATETIME NOT NULL,
            source_plugin TEXT NOT NULL,
            FOREIGN KEY (vessel_id) REFERENCES vessels (id)
        )
    ''')

    # Create scraper_logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scraper_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plugin_name TEXT NOT NULL,
            status TEXT NOT NULL,
            records_inserted INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT
        )
    ''')

    conn.commit()
    conn.close()

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def insert_vessels_and_locations(
    vessels: List[VesselMetadata], 
    locations: List[VesselLocation], 
    plugin_name: str
) -> int:
    """
    Upserts vessels and inserts locations.
    Returns the number of location records successfully inserted.
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    records_inserted = 0
    
    try:
        # Upsert vessels and build a mapping of mmsi -> vessel_id
        mmsi_to_id = {}
        for v in vessels:
            cursor.execute('''
                INSERT INTO vessels (imo, mmsi, vessel_name, vessel_type, callsign)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(imo, mmsi) DO UPDATE SET
                    vessel_name=excluded.vessel_name,
                    vessel_type=excluded.vessel_type,
                    callsign=excluded.callsign
            ''', (v.imo, v.mmsi, v.vessel_name, v.vessel_type, v.callsign))
            
            # Retrieve the ID (either newly inserted or existing)
            cursor.execute('SELECT id FROM vessels WHERE imo = ? AND mmsi = ?', (v.imo, v.mmsi))
            row = cursor.fetchone()
            if row:
                mmsi_to_id[v.mmsi] = row[0]

        # Insert locations
        for loc in locations:
            vessel_id = mmsi_to_id.get(loc.mmsi)
            if vessel_id is None:
                # If we don't have the vessel_id, we can't link it (shouldn't happen if parsing is correct)
                continue
                
            cursor.execute('''
                INSERT INTO vessel_locations (vessel_id, latitude, longitude, speed, heading, timestamp, source_plugin)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (vessel_id, loc.latitude, loc.longitude, loc.speed, loc.heading, loc.timestamp, plugin_name))
            records_inserted += 1
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
        
    return records_inserted

def log_scraper_execution(plugin_name: str, status: str, records_inserted: int = 0, error_message: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO scraper_logs (plugin_name, status, records_inserted, error_message)
        VALUES (?, ?, ?, ?)
    ''', (plugin_name, status, records_inserted, error_message))
    conn.commit()
    conn.close()
