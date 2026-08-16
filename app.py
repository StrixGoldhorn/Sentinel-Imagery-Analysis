import os
import json
import math
import shutil
import io
import requests
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_from_directory
from pathlib import Path
from datetime import datetime, timezone
import urllib.request
import get_images_area
import basic_classical_cv
from utils.get_token import get_token
import sqlite3
from predict_scans import predict_next_scans_n2yo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# Ensure output directory exists
OUTPUT_BASE = Path("static/output")
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = "static/data.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS aoi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                bbox TEXT NOT NULL,
                next_scan TEXT,
                last_checked TEXT
            )
        ''')
        conn.commit()

# Initialize SQLite on startup
init_db()

def get_area_name(lat, lon):
    """Dynamically resolve a location name using reverse geocoding."""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&accept-language=en"
        req = urllib.request.Request(url, headers={'User-Agent': 'SentinelImageryAnalysis/1.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            addr = data.get('address', {})
            # Priority order for name: city -> town -> county -> state
            name = addr.get('city') or addr.get('town') or addr.get('county') or addr.get('state')
            return name if name else data.get('display_name', 'Open Sea').split(',')[0]
    except Exception:
        return f"Area at {lat:.2f}N, {lon:.2f}E"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/scan', methods=['POST'])
def scan():
    data = request.json
    bbox = data.get('bbox') # [min_lon, min_lat, max_lon, max_lat]
    
    if not bbox or len(bbox) != 4:
        return jsonify({"error": "Invalid Bounding Box"}), 400

    try:
        token = get_token()
        
        # Query for latest metadata to create folder
        sar_datetime = get_images_area.get_latest_sar_datetime(bbox)
        if not sar_datetime:
            return jsonify({"error": "No SAR coverage found for this area in the last 30 days."}), 404
            
        dt_obj = datetime.fromisoformat(sar_datetime.replace('Z', '+00:00'))
        
        # Unique ID to prevent filename collisions for different areas of the same acquisition
        folder_name = f"{dt_obj.strftime('%Y%m%d_%H%M%S')}_{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
        
        # Setup output paths inside static for easy serving
        scan_dir = OUTPUT_BASE / folder_name
        img_dir = scan_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)

        # Calculate center point for geocoding
        center_lat = (bbox[1] + bbox[3]) / 2
        center_lon = (bbox[0] + bbox[2]) / 2

        # Save metadata to persist details for the gallery page
        metadata = {
            "acquisition_datetime": sar_datetime,
            "satellite": "Sentinel-1",
            "settings": {
                "bbox": bbox,
                "evalscript": "EVALSCRIPT_SAR",
                "datasource": "sentinel-1-grd"
            },
            "scraped_datetime": datetime.now(timezone.utc).isoformat(),
            "location": get_area_name(center_lat, center_lon)
        }
        with open(scan_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)

        # Calculate tiles (for simplicity in this UI, we process the grid)
        tiles = get_images_area.calculate_tiles(bbox)
        
        # Collect dimensions to build a master stitched image canvas
        tile_widths = {}
        tile_heights = {}
        for t in tiles:
            _, w, h, x, y = t
            tile_widths[x] = w
            tile_heights[y] = h
            
        total_w = sum(tile_widths.values())
        total_h = sum(tile_heights.values())
        
        stitched_img = Image.new('RGBA', (total_w, total_h), (0, 0, 0, 0))
        
        # Fetch and stitch all tiles together
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        })
        
        for t in tiles:
            tile_bbox, w, h, x, y = t
            sar_payload = get_images_area.build_payload(
                tile_bbox, w, h, 
                get_images_area.EVALSCRIPT_SAR, 
                "sentinel-1-grd"
            )
            
            resp = session.post(get_images_area.API_URL, json=sar_payload, timeout=get_images_area.DEFAULT_TIMEOUT)
            resp.raise_for_status()
            
            tile_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            
            # Calculate pixel offsets. y=0 in geographic maps is bottom, but y=0 in images is top.
            x_offset = sum(tile_widths[i] for i in range(x))
            y_offset = sum(tile_heights[j] for j in range(y + 1, len(tile_heights)))
            
            stitched_img.paste(tile_img, (x_offset, y_offset))
            
        # Check if the stitched image is completely transparent (no data coverage)
        if not stitched_img.getbbox():
            raise ValueError("No valid imagery coverage returned for this specific bounding box.")
            
        image_filename = f"{folder_name}_stitched_sar.png"
        stitched_img.save(img_dir / image_filename)

        return jsonify({
            "status": "success",
            "folderName": folder_name,
            "imageUrl": f"/static/output/{folder_name}/images/{image_filename}",
            "bounds": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
            "datetime": sar_datetime
        })

    except Exception as e:
        # Clean up the directory to prevent corrupt/incomplete scans in the gallery
        if 'scan_dir' in locals() and scan_dir.exists():
            shutil.rmtree(scan_dir, ignore_errors=True)
            
        status_code = 400 if isinstance(e, ValueError) else 500
        return jsonify({"error": str(e)}), status_code

@app.route('/api/update_metadata/<folder_name>', methods=['POST'])
def update_metadata(folder_name):
    """Updates the metadata.json file for a specific scan."""
    data = request.json
    custom_name = data.get('custom_name')
    
    scan_dir = OUTPUT_BASE / folder_name
    metadata_file = scan_dir / "metadata.json"
    
    if not metadata_file.exists():
        return jsonify({"error": "Metadata not found"}), 404
        
    try:
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
            
        metadata['custom_name'] = custom_name
        
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f)
            
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/run_cv/<folder_name>', methods=['POST'])
def run_cv(folder_name):
    """Runs classical computer vision on a scanned area."""
    data = request.json or {}
    threshold = data.get('threshold', 40)
    
    scan_dir = OUTPUT_BASE / folder_name
    if not scan_dir.exists():
        return jsonify({"error": "Scan not found"}), 404
        
    img_dir = scan_dir / "images"
    
    # Try to load stitched image, fallback to normal tile
    images = list(img_dir.glob("*_stitched_sar.png")) or list(img_dir.glob("*_sar.png"))
    if not images:
        return jsonify({"error": "SAR image not found"}), 404
        
    image_path = str(images[0])
    dems = list(img_dir.glob("*_stitched_dem.png")) or list(img_dir.glob("*_dem.png"))
    dem_path = str(dems[0]) if dems else None
    
    try:
        boxes, w, h = basic_classical_cv.get_ship_boxes(image_path, dem_path, threshold=int(threshold))
        return jsonify({
            "status": "success",
            "boxes": boxes,
            "width": w,
            "height": h
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/scan/<folder_name>')
def get_scan_api(folder_name):
    """API endpoint to get specific scan details for map layering."""
    scan_dir = OUTPUT_BASE / folder_name
    metadata_file = scan_dir / "metadata.json"
    
    if not scan_dir.exists() or not metadata_file.exists():
        return jsonify({"error": "Scan not found"}), 404

    try:
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        
        img_dir = scan_dir / "images"
        images = list(img_dir.glob("*.png"))
        if not images:
            return jsonify({"error": "No imagery in folder"}), 404

        bbox = metadata['settings']['bbox']
        return jsonify({
            "imageUrl": f"/static/output/{folder_name}/images/{images[0].name}",
            "bounds": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
            "datetime": metadata['acquisition_datetime'],
            "custom_name": metadata.get('custom_name')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/static/output/<path:filename>')
def serve_output(filename):
    return send_from_directory(OUTPUT_BASE, filename)

@app.route('/gallery')
def gallery():
    """Displays all previously scanned imagery."""
    scans = []
    if OUTPUT_BASE.exists():
        # Sort by modification time to show newest scans first
        for scan_dir in sorted(OUTPUT_BASE.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if scan_dir.is_dir():
                # Load metadata if it exists for the scan
                metadata = {}
                metadata_file = scan_dir / "metadata.json"
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                    except (json.JSONDecodeError, IOError) as e:
                        # Provide fallback labels for corrupted or old metadata files
                        metadata = {
                            "location": "Unknown Location",
                            "acquisition_datetime": "N/A",
                            "satellite": "Unknown",
                            "scraped_datetime": "N/A",
                            "settings": {}
                        }

                img_dir = scan_dir / "images"
                if img_dir.exists():
                    images = [f"/static/output/{scan_dir.name}/images/{img.name}" for img in img_dir.glob("*.png")]
                    if images:
                        scans.append({
                            "folder": scan_dir.name,
                            "images": images,
                            "metadata": metadata
                        })
    return render_template('gallery.html', scans=scans)

# --- Areas of Interest (AOI) Database Routes ---

@app.route('/api/aoi', methods=['GET'])
def get_aois():
    """Fetch all stored Areas of Interest."""
    with get_db_connection() as conn:
        aois = conn.execute('SELECT * FROM aoi ORDER BY name').fetchall()
        # Parse bbox string back to JSON for the frontend
        results = []
        for row in aois:
            row_dict = dict(row)
            row_dict['bbox'] = json.loads(row_dict['bbox'])
            results.append(row_dict)
        return jsonify(results)

@app.route('/api/aoi', methods=['POST'])
def add_aoi():
    """Add a new Area of Interest to track."""
    data = request.json
    bbox = data.get('bbox')
    name = data.get('name')
    
    if not bbox or not name:
        return jsonify({"error": "Missing bbox or name"}), 400
        
    bbox_str = json.dumps(bbox)
    with get_db_connection() as conn:
        cursor = conn.execute('INSERT INTO aoi (name, bbox) VALUES (?, ?)', (name, bbox_str))
        conn.commit()
        return jsonify({"status": "success", "id": cursor.lastrowid})

@app.route('/api/aoi/<int:aoi_id>/predict', methods=['POST'])
def predict_aoi(aoi_id):
    """Run the prediction script for a specific Area of Interest."""
    with get_db_connection() as conn:
        aoi = conn.execute('SELECT * FROM aoi WHERE id = ?', (aoi_id,)).fetchone()
        if not aoi:
            return jsonify({"error": "AOI not found"}), 404
            
        bbox = json.loads(aoi['bbox'])
        
    n2yo_key = os.environ.get("N2YO_API_KEY")
    
    if not n2yo_key:
        return jsonify({"error": "N2YO API key missing. Please configure N2YO_API_KEY in your environment or .env file."}), 400
        
    try:
        predictions = predict_next_scans_n2yo(bbox, n2yo_key)
    except Exception as e:
        return jsonify({"error": f"Prediction failed: {str(e)}"}), 500
        
    if predictions:
        next_scan = predictions[0]['time']
        now_str = datetime.now(timezone.utc).isoformat()
        with get_db_connection() as conn:
            conn.execute('UPDATE aoi SET next_scan = ?, last_checked = ? WHERE id = ?', 
                         (next_scan, now_str, aoi_id))
            conn.commit()
        return jsonify({"status": "success", "next_scan": next_scan, "predictions": predictions})
    else:
        return jsonify({"error": "No upcoming scans found"}), 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)