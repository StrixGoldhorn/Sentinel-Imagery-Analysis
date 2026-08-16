import os
import argparse
import requests
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def predict_next_scans_n2yo(bbox, api_key):
    """
    Predicts upcoming Sentinel-1 passes using the N2YO API.
    This uses actual TLE (Two-Line Element) orbital data for high accuracy.
    """
    print(f"Using N2YO API to predict Sentinel-1A passes...\n")
    
    # Calculate the center of the bounding box to use as the observer location
    lat = (bbox[1] + bbox[3]) / 2
    lon = (bbox[0] + bbox[2]) / 2
    alt = 0
    days = 10
    min_elev = 15  # Minimum elevation in degrees (Sentinel-1 looks sideways, so 15+ is safe)
    sat_id = 39634 # NORAD ID for Sentinel-1A
    
    # Use ?apiKey= to ensure strict URL parameter standards
    url = f"https://api.n2yo.com/rest/v1/satellite/radiopasses/{sat_id}/{lat}/{lon}/{alt}/{days}/{min_elev}/?apiKey={api_key}"
    
    try:
        response = requests.get(url, timeout=30)
        
        try:
            data = response.json()
        except ValueError:
            response.raise_for_status()
            raise ValueError("Invalid JSON response from N2YO API.")
            
        if "error" in data:
            raise ValueError(f"N2YO API Error: {data['error']}")
            
        response.raise_for_status()
        
        if "info" not in data:
            raise ValueError("Invalid response format from N2YO. Please check your API key.")
            
        passes = data.get("passes", [])
        
        if not passes:
            print(f"❌ No upcoming passes predicted for Sentinel-1A over this location in the next {days} days.")
            return []
            
        print(f"✅ Found {len(passes)} upcoming passes for Sentinel-1A (NORAD {sat_id}):\n")
        
        dt_now = datetime.now(timezone.utc)
        predictions = []
        
        for i, p in enumerate(passes, 1):
            # N2YO returns Unix timestamps
            max_utc = p.get("maxUTC")
            if not max_utc:
                continue
                
            pass_time = datetime.fromtimestamp(max_utc, tz=timezone.utc)
            time_until = pass_time - dt_now
            days_until, seconds = time_until.days, time_until.seconds
            hours = seconds // 3600
            
            print(f"  {i}. {pass_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print(f"     In: {days_until} days, {hours} hours | Max Elevation: {p.get('maxElev')}°\n")
            
            predictions.append({
                "time": pass_time.isoformat(),
                "max_elevation": p.get('maxElev')
            })
        return predictions
    except Exception as e:
        print(f"✗ Request to N2YO failed: {e}")
        raise e

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Predict next Sentinel-1 scan times for a bounding box.")
    parser.add_argument("--bbox", type=float, nargs=4, required=True,
                        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
                        help="Bounding box coordinates (e.g., 103.74 1.22 103.85 1.31)")
    parser.add_argument("--n2yo-key", type=str, default=None,
                        help="N2YO API key for real-time tracking predictions (optional)")
    
    args = parser.parse_args()
    
    # Check if N2YO key is provided via CLI argument or environment variable
    n2yo_key = args.n2yo_key or os.environ.get("N2YO_API_KEY")
    
    if n2yo_key:
        predict_next_scans_n2yo(args.bbox, n2yo_key)
    else:
        print("❌ No N2YO API key provided. Please provide one via --n2yo-key or the N2YO_API_KEY environment variable.")