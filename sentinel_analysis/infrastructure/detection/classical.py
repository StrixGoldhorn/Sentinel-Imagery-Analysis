"""OpenCV implementation of the ship-detector port."""

from pathlib import Path

import cv2
import numpy as np

from sentinel_analysis.domain.entities import ShipDetection


class ClassicalShipDetector:
    """Detect bright connected regions in grayscale SAR imagery."""

    def detect(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
    ) -> tuple[list[ShipDetection], int, int]:
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(f"Unable to read SAR image: {image_path}")

        if dem_path is not None:
            image = self._mask_land(image, dem_path)

        _, binary = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
        kernel = np.ones((5, 5), np.uint8)
        dilated = cv2.dilate(binary, kernel, iterations=2)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[ShipDetection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if 50 <= area <= 5000:
                x, y, width, height = cv2.boundingRect(contour)
                detections.append(ShipDetection(x, y, width, height))

        height, width = image.shape[:2]
        return detections, width, height

    @staticmethod
    def _mask_land(image: np.ndarray, dem_path: Path) -> np.ndarray:
        dem = cv2.imread(str(dem_path), cv2.IMREAD_GRAYSCALE)
        if dem is None:
            raise FileNotFoundError(f"Unable to read DEM image: {dem_path}")
        if dem.shape != image.shape:
            raise ValueError("SAR and DEM images must have identical dimensions")

        _, mask = cv2.threshold(dem, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((27, 27), np.uint8), iterations=2)
        mask = cv2.dilate(mask, np.ones((81, 81), np.uint8), iterations=2)
        return cv2.bitwise_and(image, image, mask=cv2.bitwise_not(mask))

