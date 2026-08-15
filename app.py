import os
from flask import Flask, render_template, request, jsonify, send_from_directory
from pathlib import Path
from datetime import datetime, timezone
import get_images_area
from utils.get_token import get_token

app = Flask(__name__)

# Ensure output directory exists
OUTPUT_BASE = Path("static/output")
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

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

        # Calculate tiles (for simplicity in this UI, we process the grid)
        tiles = get_images_area.calculate_tiles(bbox)
        
        # Fetch first tile/chunk for layering
        # In a production app, you might stitch these or return a list
        tile_bbox, w, h, tx, ty = tiles[0]
        
        sar_payload = get_images_area.build_payload(
            tile_bbox, w, h, 
            get_images_area.EVALSCRIPT_SAR, 
            "sentinel-1-grd"
        )
        
        # We use a chunk size equal to tile size to get one image per tile for layering
        chunks_saved = get_images_area.fetch_and_split_image(
            sar_payload, scan_dir, folder_name, "sar", 
            max(w, h), 0, token
        )

        if chunks_saved > 0:
            # Return the path to the first image and its specific bbox for Leaflet layering
            image_path = f"/static/output/{folder_name}/images/{folder_name}_0000_sar.png"
            return jsonify({
                "status": "success",
                "imageUrl": image_path,
                "bounds": [[tile_bbox[1], tile_bbox[0]], [tile_bbox[3], tile_bbox[2]]],
                "datetime": sar_datetime
            })
        
        return jsonify({"error": "No imagery retrieved"}), 500

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
                img_dir = scan_dir / "images"
                if img_dir.exists():
                    images = [f"/static/output/{scan_dir.name}/images/{img.name}" for img in img_dir.glob("*.png")]
                    if images:
                        scans.append({
                            "folder": scan_dir.name,
                            "images": images
                        })
    return render_template('gallery.html', scans=scans)

if __name__ == '__main__':
    app.run(debug=True, port=5000)