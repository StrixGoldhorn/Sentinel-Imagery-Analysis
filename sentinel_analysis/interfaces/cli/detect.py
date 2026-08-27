"""Command-line interface for classical ship detection."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import cv2

from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.interfaces.cli.common import CLICommand


def get_ship_boxes(
    image_path: str,
    dem_path: str | None = None,
    threshold: int = 40,
    *,
    use_case: DetectShips | None = None,
) -> tuple[list[tuple[int, int, int, int]], int, int]:
    result = (use_case or DetectShips(ClassicalShipDetector())).execute(
        Path(image_path), Path(dem_path) if dem_path else None, threshold,
    )
    boxes = [(item.x, item.y, item.width, item.height) for item in result.detections]
    return boxes, result.image_width, result.image_height


def detect_ships_basic(
    image_path: str,
    dem_path: str | None = None,
    output_path: str = "detected_ships.jpg",
    threshold: int = 40,
    *,
    use_case: DetectShips | None = None,
    stdout: TextIO | None = None,
) -> int:
    result = (use_case or DetectShips(ClassicalShipDetector())).execute(
        Path(image_path), Path(dem_path) if dem_path else None, threshold,
    )
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Unable to read SAR image: {image_path}")
    output = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    for index, item in enumerate(result.detections, 1):
        cv2.rectangle(output, (item.x, item.y), (item.x + item.width, item.y + item.height), (0, 255, 0), 2)
        cv2.putText(output, f"Ship {index}", (item.x, max(0, item.y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), output):
        raise OSError(f"Unable to write detection image: {destination}")
    print(f"Detected {len(result.detections)} possible ships.", file=stdout or sys.stdout)
    return len(result.detections)


class DetectCommand(CLICommand):
    def __init__(self, use_case: DetectShips | None = None) -> None:
        self._use_case = use_case or DetectShips(ClassicalShipDetector())

    def create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Detect ships in SAR images")
        parser.add_argument("image_path", help="Path to the SAR image file")
        parser.add_argument("--dem", default=None, help="Optional DEM image")
        parser.add_argument("--output", default="detected_ships.jpg", help="Output image path")
        parser.add_argument("--threshold", type=int, default=40, help="Brightness threshold from 0 to 255")
        return parser

    def execute(self, args: argparse.Namespace, stdout: TextIO) -> int:
        detect_ships_basic(
            args.image_path,
            args.dem,
            args.output,
            args.threshold,
            use_case=self._use_case,
            stdout=stdout,
        )
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    return DetectCommand().run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
