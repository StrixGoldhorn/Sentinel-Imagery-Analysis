import cv2
import numpy as np
import matplotlib.pyplot as plt
import time
import argparse

def load_img(image_path):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)

    if img is None:
        print("Error: Image not found.")
        return
    return img

def mask_img_with_dem(img, dem_path):
    '''
    Masks imagery. Land masses to be coloured WHITE, water bodies to be BLACK.
    Returns masked image as well as mask used.
    '''
    dem = load_img(dem_path)
    _, mask = cv2.threshold(dem, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # clean up rough edges
    kernel = np.ones((27,27), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    # expand slightly
    mask_kernel = np.ones((81, 81), np.uint8) 
    expanded_mask = cv2.dilate(mask, mask_kernel, iterations=2)

    # finally apply masking
    mask_inv = cv2.bitwise_not(expanded_mask) # <- "bitwise not": cs2100 reference?!
    img = cv2.bitwise_and(img, img, mask=mask_inv) # <- "bitwise and": cs2100 reference?!
    return img, mask

def detect_ships_basic(image_path, dem_path=None, output_path="detected_ships.jpg"):

    timer_start = time.time()

    img = load_img(image_path)
    if img is None:
        return

    original = img
    masked_img = None

    timer_temp = time.time() - timer_start
    print(f"Loaded {timer_temp:.2f}s")

    # create mask to ignore land
    if dem_path:
        masked_img, mask = mask_img_with_dem(img, dem_path)

        fig, axes = plt.subplots(2, 2, figsize=(8, 8))

        # original image
        axes[0, 0].imshow(original, cmap='gray')
        axes[0, 0].set_title("1. Original SAR Image")
        axes[0, 0].axis('off')

        dem = load_img(dem_path)
        # dem
        axes[1, 0].imshow(dem, cmap='gray')
        axes[1, 0].set_title(f"2. Digital Elevation Model")
        axes[1, 0].axis('off')

        # masking image
        axes[1, 1].imshow(mask, cmap='gray')
        axes[1, 1].set_title(f"3. Masking Image")
        axes[1, 1].axis('off')

        # masked image
        axes[0, 1].imshow(masked_img, cmap='gray')
        axes[0, 1].set_title(f"4. Masked SAR Image")
        axes[0, 1].axis('off')

        plt.tight_layout()

    img = masked_img if masked_img is not None else img

    thresh_val = 40
    _, binary_mask = cv2.threshold(img, thresh_val, 255, cv2.THRESH_BINARY)

    timer_temp = time.time() - timer_start
    print(f"Binary Mask {timer_temp:.2f}s")

    # blur things together
    kernel = np.ones((5, 5), np.uint8)
    dilated = cv2.dilate(binary_mask, kernel, iterations=2)

    timer_temp = time.time() - timer_start
    print(f"Dilated {timer_temp:.2f}s")

    # get contours to find connected components
    contours, hierarchy = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    timer_temp = time.time() - timer_start
    print(f"Contour extracted {timer_temp:.2f}s")

    img_color = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    ship_count = 0
    detected_boxes = []

    for cnt in contours:
        area = cv2.contourArea(cnt)

        # too small, ignore
        if area < 50: continue

        # too big, ignore
        if area > 5000: continue

        # bounding box
        x, y, w, h = cv2.boundingRect(cnt)

        ship_count += 1
        detected_boxes.append((x, y, w, h))

        # label detection
        cv2.rectangle(img_color, (x, y), (x+w, y+h), (0, 255, 0), 2)
        cv2.putText(img_color, f"Ship {ship_count}", (x, y-5), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)


    timer_temp = time.time() - timer_start
    print(f"Finished {timer_temp:.2f}s")
    print(f"Detected {ship_count} possible ships.")

    cv2.imwrite(output_path, img_color)

    # Plotting for visualization
    fig, axes = plt.subplots(2, 2, figsize=(8, 8))

    # original image
    axes[0, 0].imshow(original, cmap='gray')
    axes[0, 0].set_title("1. Original SAR Image")
    axes[0, 0].axis('off')

    # final detection
    img_rgb = cv2.cvtColor(img_color, cv2.COLOR_BGR2RGB)
    axes[0, 1].imshow(img_rgb)
    axes[0, 1].set_title(f"4. Final Detection ({ship_count} ships)")
    axes[0, 1].axis('off')

    # mask 
    axes[1, 0].imshow(binary_mask, cmap='gray')
    axes[1, 0].set_title("2. Mask")
    axes[1, 0].axis('off')

    # dilated mask
    axes[1, 1].imshow(dilated, cmap='gray')
    axes[1, 1].set_title("3. Dilated Mask")
    axes[1, 1].axis('off')

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Detect ships in SAR images')
    parser.add_argument('image_path', type=str, help='Path to the SAR image file')
    parser.add_argument('--dem', type=str, default=None, help='Path to the DEM (Digital Elevation Model) file (optional)')
    parser.add_argument('--output', type=str, default='detected_ships.jpg', help='Output file path (default: detected_ships.jpg)')

    args = parser.parse_args()

    detect_ships_basic(args.image_path, args.dem, args.output)
