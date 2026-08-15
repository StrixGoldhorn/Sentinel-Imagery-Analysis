import os
import json
import math
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

app = Flask(__name__)

# Ensure output directory exists
OUTPUT_BASE = Path("static/output")
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

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
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        for t in tiles:
            tile_bbox, w, h, x, y = t
            sar_payload = get_images_area.build_payload(
                tile_bbox, w, h, 
                get_images_area.EVALSCRIPT_SAR, 
                "sentinel-1-grd"
            )
            
            resp = requests.post(get_images_area.API_URL, headers=headers, json=sar_payload, timeout=get_images_area.DEFAULT_TIMEOUT)
            resp.raise_for_status()
            
            tile_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            
            # Calculate pixel offsets. y=0 in geographic maps is bottom, but y=0 in images is top.
            x_offset = sum(tile_widths[i] for i in range(x))
            y_offset = sum(tile_heights[j] for j in range(y + 1, len(tile_heights)))
            
            stitched_img.paste(tile_img, (x_offset, y_offset))
            
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
        return jsonify({"error": str(e)}), 500

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

@app.route('/api/run_cv/<folder_name>', methods=['POST'])
def run_cv(folder_name):
    """Runs classical computer vision on a scanned area."""
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
        boxes, w, h = basic_classical_cv.get_ship_boxes(image_path, dem_path)
        return jsonify({
            "status": "success",
            "boxes": boxes,
            "width": w,
            "height": h
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)