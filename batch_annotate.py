import cv2
import numpy as np
import time
import argparse
import os
import json
import re
from pathlib import Path

def load_img(image_path):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        print(f"Error: Image not found at {image_path}.")
        return None
    return img

def mask_img_with_dem(img, dem_path):
    """
    Masks imagery. Land masses to be coloured WHITE, water bodies to be BLACK.
    Returns masked image as well as mask used.
    """
    dem = load_img(dem_path)
    if dem is None: 
        return img, None
    
    _, mask = cv2.threshold(dem, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # Clean up rough edges
    kernel = np.ones((27, 27), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    # Expand slightly
    mask_kernel = np.ones((81, 81), np.uint8) 
    expanded_mask = cv2.dilate(mask, mask_kernel, iterations=2)

    # Apply masking
    mask_inv = cv2.bitwise_not(expanded_mask)
    img = cv2.bitwise_and(img, img, mask=mask_inv)
    return img, mask

def run_basic_cv_detection(img, min_area=50, max_area=5000):
    """
    Replicates the exact logic from basic_classical_cv.py to find rough ship boxes.
    Returns list of (x, y, w, h)
    """
    # Exact threshold from your script
    thresh_val = 40
    _, binary_mask = cv2.threshold(img, thresh_val, 255, cv2.THRESH_BINARY)

    # Exact dilation from your script
    kernel = np.ones((5, 5), np.uint8)
    dilated = cv2.dilate(binary_mask, kernel, iterations=2)

    # Exact contour extraction from your script
    contours, hierarchy = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    detected_boxes = []
    for cnt in contours:
        area = cv2.contourArea(cnt)

        # Exact area filters from your script
        if area < min_area: 
            continue
        if area > max_area: 
            continue

        # Exact bounding box extraction
        x, y, w, h = cv2.boundingRect(cnt)
        detected_boxes.append((x, y, w, h))

    return detected_boxes

def apply_lee_filter(image, window_size=5, noise_var=0.25):
    """
    Applies a Lee filter to reduce SAR speckle noise.
    
    Args:
        image (np.ndarray): Input image (can be uint8, float32, etc.)
        window_size (int): Size of the sliding window (must be odd, e.g., 3, 5, 7).
        noise_var (float): Estimated variance of the speckle noise. 
                           0.25 is standard for SAR, but can be tuned.
                           
    Returns:
        np.ndarray: Filtered image in the same dtype as the input.
    """
    # Convert to float64 to prevent overflow/underflow during math
    img_float = image.astype(np.float64)
    
    # Calculate local statistics using OpenCV's fast box filter
    ksize = (window_size, window_size)
    local_mean = cv2.boxFilter(img_float, -1, ksize)
    local_mean_sq = cv2.boxFilter(img_float**2, -1, ksize)
    
    # Variance = E[X^2] - (E[X])^2
    local_var = local_mean_sq - local_mean**2
    
    # Prevent negative variance due to floating point errors
    local_var = np.maximum(local_var, 0)
    
    # Apply the Lee filter formula for multiplicative noise
    # K = (Var - mean^2 * noise_var) / (Var + mean^2 * noise_var)
    numerator = local_var - (local_mean**2 * noise_var)
    denominator = local_var + (local_mean**2 * noise_var)
    
    # Safely divide, setting K to 0 where denominator is 0
    K = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator!=0)
    
    # Calculate final filtered pixel
    filtered_img = local_mean + K * (img_float - local_mean)
    
    # Return in the exact same data type as the original image
    return filtered_img.astype(image.dtype)

class ImageAnnotator:
    def __init__(self, img, initial_boxes, output_txt_path):
        self.img = img
        self.boxes = [[b[0], b[1], b[0]+b[2], b[1]+b[3]] for b in initial_boxes]
        self.output_txt_path = output_txt_path
        
        # 1. Define window dimensions FIRST
        self.win_w, self.win_h = 1280, 720 
        
        # 2. Calculate zoom to fit the full image
        h, w = self.img.shape[:2]
        self.zoom = min(self.win_w / w, self.win_h / h)
        
        # 3. Center the image in the window
        self.pan_x = (self.win_w - w * self.zoom) / 2
        self.pan_y = (self.win_h - h * self.zoom) / 2
        
        self.is_panning = False
        self.is_drawing = False
        self.start_x, self.start_y = 0, 0
        self.current_x, self.current_y = 0, 0
        
        self.window_name = 'Batch SAR Annotator (Scroll=Zoom | Mid=Pan | S=Next | Q=Quit)'
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window_name, self.win_w, self.win_h)
        cv2.setMouseCallback(self.window_name, self.mouse_callback)

    def get_img_coords(self, display_x, display_y):
        img_x = (display_x - self.pan_x) / self.zoom
        img_y = (display_y - self.pan_y) / self.zoom
        return img_x, img_y

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_MOUSEWHEEL:
            old_zoom = self.zoom
            self.zoom *= 1.2 if flags > 0 else 1/1.2
            self.zoom = np.clip(self.zoom, 0.1, 10.0)
            self.pan_x = x - (x - self.pan_x) * (self.zoom / old_zoom)
            self.pan_y = y - (y - self.pan_y) * (self.zoom / old_zoom)
        elif event == cv2.EVENT_MBUTTONDOWN:
            self.is_panning = True
            self.pan_start_x = x - self.pan_x
            self.pan_start_y = y - self.pan_y
        elif event == cv2.EVENT_MOUSEMOVE:
            if self.is_panning:
                self.pan_x = x - self.pan_start_x
                self.pan_y = y - self.pan_start_y
            elif self.is_drawing:
                self.current_x, self.current_y = x, y
        elif event == cv2.EVENT_MBUTTONUP:
            self.is_panning = False
        elif event == cv2.EVENT_LBUTTONDOWN:
            self.is_drawing = True
            self.start_x, self.start_y = x, y
            self.current_x, self.current_y = x, y
        elif event == cv2.EVENT_LBUTTONUP:
            self.is_drawing = False
            img_x1, img_y1 = self.get_img_coords(self.start_x, self.start_y)
            img_x2, img_y2 = self.get_img_coords(self.current_x, self.current_y)
            x1, y1 = min(img_x1, img_x2), min(img_y1, img_y2)
            x2, y2 = max(img_x1, img_x2), max(img_y1, img_y2)
            if (x2 - x1) > 5 and (y2 - y1) > 5:
                self.boxes.append([int(x1), int(y1), int(x2), int(y2)])
        elif event == cv2.EVENT_RBUTTONDOWN:
            img_x, img_y = self.get_img_coords(x, y)
            for i in range(len(self.boxes) - 1, -1, -1):
                bx1, by1, bx2, by2 = self.boxes[i]
                if bx1 <= img_x <= bx2 and by1 <= img_y <= by2:
                    self.boxes.pop(i)
                    break

    def render(self):
        h, w = self.img.shape[:2]
        canvas = np.zeros((self.win_h, self.win_w, 3), dtype=np.uint8)

        x1 = int(-self.pan_x / self.zoom)
        y1 = int(-self.pan_y / self.zoom)
        x2 = x1 + int(self.win_w / self.zoom)
        y2 = y1 + int(self.win_h / self.zoom)

        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 > x1 and y2 > y1:
            crop = self.img[y1:y2, x1:x2]
            if len(crop.shape) == 2: 
                crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            c_w = max(1, int((x2-x1)*self.zoom))
            c_h = max(1, int((y2-y1)*self.zoom))
            crop_resized = cv2.resize(crop, (c_w, c_h))

            canvas_x = int(self.pan_x + x1 * self.zoom)
            canvas_y = int(self.pan_y + y1 * self.zoom)

            dest_x1 = max(0, canvas_x)
            dest_y1 = max(0, canvas_y)
            dest_x2 = min(self.win_w, canvas_x + c_w)
            dest_y2 = min(self.win_h, canvas_y + c_h)

            if dest_x2 > dest_x1 and dest_y2 > dest_y1:
                src_x1 = max(0, dest_x1 - canvas_x)
                src_y1 = max(0, dest_y1 - canvas_y)
                src_x2 = src_x1 + (dest_x2 - dest_x1)
                src_y2 = src_y1 + (dest_y2 - dest_y1)
                canvas[dest_y1:dest_y2, dest_x1:dest_x2] = crop_resized[src_y1:src_y2, src_x1:src_x2]

        for i, box in enumerate(self.boxes):
            bx1, by1, bx2, by2 = box
            cx1 = int(bx1 * self.zoom + self.pan_x)
            cy1 = int(by1 * self.zoom + self.pan_y)
            cx2 = int(bx2 * self.zoom + self.pan_x)
            cy2 = int(by2 * self.zoom + self.pan_y)
            thickness = max(1, int(2 * self.zoom))
            cv2.rectangle(canvas, (cx1, cy1), (cx2, cy2), (255, 0, 0), thickness)
            cv2.putText(canvas, f"{i+1}", (cx1, cy1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * self.zoom, (255, 0, 0), thickness)

        if self.is_drawing:
            cv2.rectangle(canvas, (self.start_x, self.start_y), (self.current_x, self.current_y), (0, 255, 0), 2)

        return canvas

    def run(self):
        """
        Returns True if saved and ready for next image.
        Returns False if user quit.
        """
        print("\n--- Controls ---")
        print("[Scroll] Zoom  | [Mid-Drag] Pan  | [Left-Drag] Draw  | [Right-Click] Delete")
        print("[s] SAVE & NEXT IMAGE  | [z] Undo  | [q / Esc] QUIT & SAVE PROGRESS")
        print("----------------\n")

        while True:
            display_img = self.render()
            cv2.imshow(self.window_name, display_img)
            key = cv2.waitKey(1) & 0xFF

            if key == ord('s'):
                with open(self.output_txt_path, 'w') as f:
                    for box in self.boxes:
                        f.write(f"0 {box[0]} {box[1]} {box[2]} {box[3]}\n")
                print(f"Saved {len(self.boxes)} boxes. Moving to next...")
                return True
                
            elif key == ord('z'):
                if self.boxes: 
                    self.boxes.pop()
                
            elif key == ord('q') or key == 27:
                print("Quitting batch process. Progress saved.")
                return False

        cv2.destroyAllWindows()

def save_progress(progress_file, current_index, total_images):
    with open(progress_file, 'w') as f:
        json.dump({"current_index": current_index, "total_images": total_images}, f)

def load_progress(progress_file):
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            return json.load(f)
    return {"current_index": 0, "total_images": 0}

def find_tile_pairs(tile_folder):
    """
    Find all SAR/DEM pairs in the tile folder.
    Returns list of (tile_id, sar_path, dem_path) tuples.
    """
    tile_folder = Path(tile_folder)
    
    # Find all SAR images
    sar_files = sorted(tile_folder.glob("*_sar.png"))
    
    pairs = []
    for sar_file in sar_files:
        # Extract tile ID (e.g., "0000" from "0000_sar.png")
        tile_id = sar_file.stem.replace("_sar", "")
        
        # Look for corresponding DEM
        dem_file = tile_folder / f"{tile_id}_dem.png"
        
        if dem_file.exists():
            pairs.append((tile_id, str(sar_file), str(dem_file)))
        else:
            print(f"⚠ Warning: No DEM found for {sar_file.name}, processing without DEM masking")
            pairs.append((tile_id, str(sar_file), None))
    
    return pairs

def batch_annotate(tile_folder, start_idx=0, progress_file=None, use_lee_filter=False):
    """
    Batch annotate SAR tiles from copernicus_get_image.py output.
    
    Args:
        tile_folder: Path to the datetime folder (e.g., "20260625_103045")
        start_idx: Force start at specific index (overrides saved progress)
        progress_file: Path to progress tracking file (default: auto-generated in tile folder)
        use_lee_filter: Apply Lee filter before detection (default: True)
    """
    tile_folder = Path(tile_folder)
    
    if not tile_folder.exists():
        print(f"Error: Tile folder not found at {tile_folder}")
        return
    
    # Create labels subfolder
    label_dir = tile_folder / "labels"
    label_dir.mkdir(exist_ok=True)
    
    # Set default progress file location
    if progress_file is None:
        progress_file = tile_folder / "progress.json"
    
    # Find all SAR/DEM pairs
    print(f"\nScanning {tile_folder} for SAR/DEM pairs...")
    tile_pairs = find_tile_pairs(tile_folder)
    
    if not tile_pairs:
        print(f"No SAR/DEM pairs found in {tile_folder}")
        return
    
    total = len(tile_pairs)
    print(f"Found {total} tile pairs")
    
    # Load or set progress
    progress = load_progress(progress_file)
    
    # If user specified a start index via CLI, use it. Otherwise use saved progress.
    if start_idx > 0:
        current_idx = start_idx
    else:
        current_idx = progress.get("current_index", 0)
    
    print(f"Starting at index {current_idx} (tile {tile_pairs[current_idx][0] if current_idx < total else 'END'})")
    
    # Main Loop
    for i in range(current_idx, total):
        tile_id, sar_path, dem_path = tile_pairs[i]
        txt_name = f"{tile_id}.txt"
        txt_path = label_dir / txt_name
        
        print(f"\n[{i+1}/{total}] Processing tile {tile_id}")
        print(f"  SAR: {Path(sar_path).name}")
        if dem_path:
            print(f"  DEM: {Path(dem_path).name}")
        
        # Load SAR image
        img = load_img(sar_path)
        if img is None: 
            continue
        
        original = img
        
        # Apply DEM masking if available
        if dem_path:
            masked_img, _ = mask_img_with_dem(img, dem_path)
            img = masked_img if masked_img is not None else img
        
        # Apply Lee filter for speckle reduction
        if use_lee_filter:
            img = apply_lee_filter(img, window_size=7, noise_var=0.75)
        
        # Run rough detection using basic_classical_cv logic
        detected_boxes = run_basic_cv_detection(img)
        print(f"  Auto-detected {len(detected_boxes)} rough ships")
        
        # Open Annotator
        annotator = ImageAnnotator(original, detected_boxes, str(txt_path))
        should_continue = annotator.run()
        
        # Save progress after every image
        save_progress(progress_file, i + 1, total)
        
        if not should_continue:
            break
    
    if current_idx >= total:
        print(f"\n✓ All {total} tiles have been annotated!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Batch SAR Annotation for Copernicus Tiles')
    parser.add_argument('tile_folder', type=str, 
                        help='Path to the datetime folder from copernicus_get_image.py (e.g., 20260625_103045)')
    parser.add_argument('--start_idx', type=int, default=0, 
                        help='Force start at specific index (overrides saved progress)')
    parser.add_argument('--progress', type=str, default=None, 
                        help='Progress tracking file (default: auto in tile folder)')
    parser.add_argument('--no-lee-filter', action='store_true',
                        help='Disable Lee filter before detection')
    
    args = parser.parse_args()
    
    batch_annotate(
        tile_folder=args.tile_folder,
        start_idx=args.start_idx,
        progress_file=args.progress,
        use_lee_filter=not args.no_lee_filter
    )