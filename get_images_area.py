import os
import argparse
import requests
import time
import math
import io
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Tuple
from pathlib import Path
from PIL import Image

from utils.get_token import get_token

API_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
DEFAULT_TIMEOUT = 300

MAX_IMAGE_SIZE = 2500  # Maximum pixels per dimension
RESOLUTION_M = 10      # 10m per pixel
MAX_TILE_SIZE_M = MAX_IMAGE_SIZE * RESOLUTION_M  # 25000m = 25km

# --- Evalscripts ---
EVALSCRIPT_SAR = """//VERSION=3
function setup() {
  return {
    input: ["VH", "dataMask"],
    output: {bands: 4, sampleType: SampleType.UINT8}
  };
}

function evaluatePixel(sample) {
  let vh = sample.VH * 2;
  let alpha = sample.dataMask === 1 ? 255 : 0;
  
  if (alpha === 0) return [0, 0, 0, 0];
  
  // Simple scaling for visualization
  let grayValue = Math.min(255, Math.round(vh * 255));
  
  return [grayValue, grayValue, grayValue, alpha];
}"""

EVALSCRIPT_DEM_PNG = """//VERSION=3
function setup() {
  return {
    input: ["DEM", "dataMask"],
    output: {bands: 4, sampleType: SampleType.UINT8}
  };
}

function evaluatePixel(sample) {
  let elev = sample.DEM;
  let alpha = sample.dataMask === 1 ? 255 : 0;
  
  if (alpha === 0) return [0, 0, 0, 0];
  
  // Tighter range for coastal areas (-50m to +150m)
  let minElev = -50; 
  let maxElev = 150; 
  
  // Shift so minimum is 0
  let shifted = elev - minElev;
  if (shifted < 0) shifted = 0;
  
  let maxShifted = maxElev - minElev; // 200
  
  // SQUARE ROOT SCALING: Emphasizes small changes near sea level
  let normalized = Math.sqrt(shifted / maxShifted);
  
  let grayValue = Math.round(normalized * 255);
  
  return [grayValue, grayValue, grayValue, alpha];
}"""

def get_latest_sar_datetime(bbox: List[float], days_ago: int = 30) -> str:
    """Query Copernicus catalog for the latest SAR acquisition datetime."""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    dt_now = datetime.now(timezone.utc)
    dt_start = dt_now - timedelta(days=days_ago)
    
    params = {
        "bbox": ",".join(map(str, bbox)),
        "datetime": f"{dt_start.isoformat().replace('+00:00', 'Z')}/{dt_now.isoformat().replace('+00:00', 'Z')}",
        "collections": "sentinel-1-grd",
        "limit": 1,
        "sortby": "-properties.datetime"
    }
    
    try:
        response = requests.get(CATALOG_URL, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        if "features" in data and len(data["features"]) > 0:
            return data["features"][0]["properties"]["datetime"]
        else:
            print("⚠ No SAR data found in catalog")
            return None
    except Exception as e:
        print(f"⚠ Catalog query failed: {e}")
        return None

def calculate_tiles(bbox: List[float]) -> List[Tuple[List[float], int, int, int, int]]:
    """
    Calculate tile grid based on bbox and maximum tile size (25km x 25km).
    Returns list of (tile_bbox, width_px, height_px, tile_idx_x, tile_idx_y)
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    
    # Approximate meters per degree at equator (simplified)
    meters_per_degree_lat = 111320
    meters_per_degree_lon = 111320 * math.cos(math.radians((min_lat + max_lat) / 2))
    
    # Calculate maximum tile size in degrees
    max_tile_size_deg_lon = MAX_TILE_SIZE_M / meters_per_degree_lon
    max_tile_size_deg_lat = MAX_TILE_SIZE_M / meters_per_degree_lat
    
    # Calculate number of tiles needed
    num_tiles_x = math.ceil((max_lon - min_lon) / max_tile_size_deg_lon)
    num_tiles_y = math.ceil((max_lat - min_lat) / max_tile_size_deg_lat)
    
    print(f"Grid: {num_tiles_x} x {num_tiles_y} tiles ({num_tiles_x * num_tiles_y} total)")
    print(f"Each tile covers up to {MAX_TILE_SIZE_M/1000:.1f}km x {MAX_TILE_SIZE_M/1000:.1f}km")
    
    if num_tiles_x * num_tiles_y > 100:
        print(f"⚠ Warning: Large number of tiles ({num_tiles_x * num_tiles_y}). This may take a while.")
    
    tiles = []
    
    for y in range(num_tiles_y):
        for x in range(num_tiles_x):
            tile_min_lon = min_lon + x * max_tile_size_deg_lon
            tile_max_lon = min(tile_min_lon + max_tile_size_deg_lon, max_lon)
            tile_min_lat = min_lat + y * max_tile_size_deg_lat
            tile_max_lat = min(tile_min_lat + max_tile_size_deg_lat, max_lat)
            
            tile_bbox = [tile_min_lon, tile_min_lat, tile_max_lon, tile_max_lat]
            
            # Calculate actual tile size in meters
            tile_width_m = (tile_max_lon - tile_min_lon) * meters_per_degree_lon
            tile_height_m = (tile_max_lat - tile_min_lat) * meters_per_degree_lat
            
            # Calculate pixel dimensions (capped at MAX_IMAGE_SIZE)
            width_px = min(MAX_IMAGE_SIZE, int(round(tile_width_m / RESOLUTION_M)))
            height_px = min(MAX_IMAGE_SIZE, int(round(tile_height_m / RESOLUTION_M)))
            
            # Ensure minimum 1 pixel
            width_px = max(1, width_px)
            height_px = max(1, height_px)
            
            tiles.append((tile_bbox, width_px, height_px, x, y))
    
    return tiles

def build_payload(bbox: List[float], width: int, height: int, evalscript: str, 
                  data_type: str, time_from: str = None, time_to: str = None) -> Dict[str, Any]:
    """Build API request payload."""
    data_filter = {}
    
    if time_from and time_to:
        data_filter["timeRange"] = {"from": time_from, "to": time_to}
    
    data_entry = {
        "dataFilter": data_filter,
        "type": data_type
    }
    
    return {
        "input": {
            "bounds": {"bbox": bbox},
            "data": [data_entry]
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [{
                "identifier": "default",
                "format": {"type": "image/png"}
            }]
        },
        "evalscript": evalscript
    }

def fetch_and_split_image(payload: Dict[str, Any], output_dir: Path, folder_name: str, 
                          product_type: str, chunk_size: int, global_idx_start: int, 
                          token: str) -> int:
    """Fetch image from API, split into chunks, and save. Returns the number of chunks saved."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        
        # Load image from memory
        img = Image.open(io.BytesIO(response.content))
        w, h = img.size
        
        chunks_saved = 0
        current_idx = global_idx_start
        
        # Split image into chunks
        for y in range(0, h, chunk_size):
            for x in range(0, w, chunk_size):
                box = (x, y, min(x + chunk_size, w), min(y + chunk_size, h))
                chunk = img.crop(box)
                
                # Format: <foldername>_<4-digit>_<product>.png
                chunk_name = f"images/{folder_name}_{current_idx:04d}_{product_type}.png"
                chunk_path = output_dir / chunk_name
                chunk.save(chunk_path)
                
                current_idx += 1
                chunks_saved += 1
                
        return chunks_saved
        
    except Exception as e:
        print(f"✗ Error fetching and splitting image: {e}")
        return 0

def main():
    parser = argparse.ArgumentParser(description="Fetch Copernicus SAR and DEM imagery in tiles")
    parser.add_argument("--bbox", type=float, nargs=4, required=True,
                        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                        help="Bounding box coordinates")
    parser.add_argument("--products", type=str, choices=["sar", "dem", "both"], default="both",
                        help="Products to fetch (default: both)")
    parser.add_argument("--days-ago", type=int, default=30,
                        help="Look back period for SAR data in days (default: 30)")
    parser.add_argument("--output-dir", type=str, default=".",
                        help="Output directory (default: current directory)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Delay between requests in seconds (default: 0.5)")
    parser.add_argument("--chunk-size", type=int, default=1000,
                        help="Size in pixels to split the downloaded images into (default: 1000)")
    
    args = parser.parse_args()
    
    bbox = args.bbox
    chunk_size = args.chunk_size
    
    # Get authentication token
    try:
        token = get_token()
    except Exception as e:
        print(f"✗ Authentication failed: {e}")
        return
    
    # Get latest SAR datetime
    print("\nQuerying catalog for latest SAR acquisition...")
    sar_datetime = get_latest_sar_datetime(bbox, args.days_ago)
    if not sar_datetime:
        print("✗ No SAR imagery coverage found for this area in the given timeframe.")
        return
    print(f"Latest SAR datetime: {sar_datetime}")
    
    # Parse datetime for folder name
    dt_obj = datetime.fromisoformat(sar_datetime.replace('Z', '+00:00'))
    folder_name = dt_obj.strftime("%Y%m%d_%H%M%S")
    
    # Create output directory
    output_path = Path(args.output_dir) / folder_name
    output_path.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_path}")
    
    # Calculate tiles
    print(f"\nCalculating tile grid (max size: {MAX_TILE_SIZE_M/1000:.1f}km x {MAX_TILE_SIZE_M/1000:.1f}km, resolution: {RESOLUTION_M}m)...")
    tiles = calculate_tiles(bbox)
    
    # Determine time range for SAR
    dt_now = datetime.now(timezone.utc)
    dt_start = dt_now - timedelta(days=args.days_ago)
    time_from = dt_start.isoformat().replace('+00:00', 'Z')
    time_to = dt_now.isoformat().replace('+00:00', 'Z')
    
    # Fetch images
    total_tiles = len(tiles)
    success_count = 0
    global_chunk_idx = 0
    
    for idx, (tile_bbox, width_px, height_px, tile_x, tile_y) in enumerate(tiles):
        print(f"\nProcessing tile {idx + 1}/{total_tiles} (x={tile_x}, y={tile_y}, {width_px}x{height_px} pixels)...")
        
        # Calculate how many chunks this tile will produce
        num_chunks_x = math.ceil(width_px / chunk_size)
        num_chunks_y = math.ceil(height_px / chunk_size)
        num_chunks_in_tile = num_chunks_x * num_chunks_y
        
        # Fetch SAR
        if args.products in ["sar", "both"]:
            sar_payload = build_payload(
                tile_bbox, width_px, height_px,
                EVALSCRIPT_SAR, "sentinel-1-grd", time_from, time_to
            )
            chunks_saved = fetch_and_split_image(
                sar_payload, output_path, folder_name, "sar", 
                chunk_size, global_chunk_idx, token
            )
            if chunks_saved > 0:
                print(f"  ✓ SAR saved: {chunks_saved} chunks")
                success_count += chunks_saved
            
            time.sleep(args.delay)
        
        # Fetch DEM
        if args.products in ["dem", "both"]:
            dem_payload = build_payload(
                tile_bbox, width_px, height_px,
                EVALSCRIPT_DEM_PNG, "dem"
            )
            chunks_saved = fetch_and_split_image(
                dem_payload, output_path, folder_name, "dem", 
                chunk_size, global_chunk_idx, token
            )
            if chunks_saved > 0:
                print(f"  ✓ DEM saved: {chunks_saved} chunks")
                success_count += chunks_saved
            
            time.sleep(args.delay)
            
        # Increment global chunk index for the next tile to maintain spatial alignment
        global_chunk_idx += num_chunks_in_tile
    
    print(f"\n✓ Completed! Successfully fetched {success_count} image chunks to {output_path}")

if __name__ == "__main__":
    main()