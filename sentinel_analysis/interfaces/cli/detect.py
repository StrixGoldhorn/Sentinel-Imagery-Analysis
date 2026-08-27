"""Command-line interface for classical ship detection."""

import argparse
from pathlib import Path

import cv2

from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector


def get_ship_boxes(image_path: str, dem_path: str | None = None, threshold: int = 40):
    detections, width, height = ClassicalShipDetector().detect(
        Path(image_path), Path(dem_path) if dem_path else None, threshold,
    )
    return [(item.x, item.y, item.width, item.height) for item in detections], width, height


def detect_ships_basic(
    image_path: str,
    dem_path: str | None = None,
    output_path: str = "detected_ships.jpg",
) -> int:
    detections, _, _ = ClassicalShipDetector().detect(
        Path(image_path), Path(dem_path) if dem_path else None,
    )
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Unable to read SAR image: {image_path}")
    output = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    for index, item in enumerate(detections, 1):
        cv2.rectangle(output, (item.x, item.y), (item.x + item.width, item.y + item.height), (0, 255, 0), 2)
        cv2.putText(output, f"Ship {index}", (item.x, max(0, item.y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    if not cv2.imwrite(output_path, output):
        raise OSError(f"Unable to write detection image: {output_path}")
    print(f"Detected {len(detections)} possible ships.")
    return len(detections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect ships in SAR images")
    parser.add_argument("image_path", help="Path to the SAR image file")
    parser.add_argument("--dem", default=None, help="Optional DEM image")
    parser.add_argument("--output", default="detected_ships.jpg", help="Output image path")
    args = parser.parse_args()
    detect_ships_basic(args.image_path, args.dem, args.output)


if __name__ == "__main__":
    main()

