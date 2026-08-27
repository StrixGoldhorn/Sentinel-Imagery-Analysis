import argparse
from datetime import datetime, timedelta
from ingestion.pipeline import run_pipeline

def parse_args():
    parser = argparse.ArgumentParser(description="AIS Data Scraper Pipeline")
    parser.add_argument("--bbox", type=float, nargs=4, required=True, 
                        help="Bounding box: min_lon min_lat max_lon max_lat", metavar=('MIN_LON', 'MIN_LAT', 'MAX_LON', 'MAX_LAT'))
    parser.add_argument("--plugin", type=str, 
                        help="Name of the specific plugin to run. If omitted, runs all discovered plugins.")
    parser.add_argument("--hours", type=int, default=24, 
                        help="Time range in hours to look back (default: 24)")
    return parser.parse_args()

def main():
    args = parse_args()
    
    bbox = tuple(args.bbox)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=args.hours)
    time_range = (start_time, end_time)
    
    print(f"Running pipeline for bbox: {bbox}")
    print(f"Time range: {start_time.isoformat()} to {end_time.isoformat()}")
    if args.plugin:
        print(f"Targeting plugin: {args.plugin}")
        
    results = run_pipeline(bbox, time_range, target_plugin=args.plugin)
    
    print("\n--- Execution Results ---")
    for plugin, status in results.items():
        print(f"{plugin}: {status}")

if __name__ == "__main__":
    main()
