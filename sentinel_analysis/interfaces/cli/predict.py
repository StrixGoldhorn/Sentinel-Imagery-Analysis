"""Command-line interface for satellite-pass prediction."""

import argparse
import os

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.satellite.n2yo import N2YOPassPredictor


def predict_next_scans_n2yo(bbox, api_key: str):
    return N2YOPassPredictor().predict(BoundingBox.from_sequence(bbox), api_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict Sentinel-1 passes for a bounding box")
    parser.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    parser.add_argument("--n2yo-key", default=None, help="N2YO API key")
    args = parser.parse_args()
    key = args.n2yo_key or os.environ.get("N2YO_API_KEY")
    if not key:
        parser.error("Provide --n2yo-key or set N2YO_API_KEY")
    for index, prediction in enumerate(predict_next_scans_n2yo(args.bbox, key), 1):
        print(f"{index}. {prediction['time']} | max elevation: {prediction['max_elevation']}")


if __name__ == "__main__":
    main()

