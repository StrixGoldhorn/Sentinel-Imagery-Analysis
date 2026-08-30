"""OpenCV implementation of the ship-detector port with Oriented Bounding Box (OBB) support."""

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from sentinel_analysis.application.ports.detection import DetectionResult
from sentinel_analysis.domain.entities import ShipDetection
from sentinel_analysis.infrastructure.imagery.preprocessing import preprocess_sar


class ClassicalShipDetector:
    """Detect bright connected regions in grayscale SAR imagery with Oriented Bounding Boxes (OBB)."""

    def __init__(
        self,
        minimum_area: float = 50,
        maximum_area: float = 5000,
        dilation_iterations: int = 2,
        pixel_spacing_meters: float = 10.0,
        filter_type: str = "none",
        min_area: float | None = None,
        pixel_spacing_m: float | None = None,
    ) -> None:
        if min_area is not None:
            minimum_area = min_area
        if pixel_spacing_m is not None:
            pixel_spacing_meters = pixel_spacing_m
        if minimum_area < 0 or maximum_area <= minimum_area:
            raise ValueError("Detection area limits must be ordered positive values")
        if isinstance(dilation_iterations, bool) or not isinstance(dilation_iterations, int) or dilation_iterations < 0:
            raise ValueError("Dilation iterations must be a non-negative integer")
        if pixel_spacing_meters <= 0:
            raise ValueError("Pixel spacing meters must be positive")
        self._minimum_area = minimum_area
        self._maximum_area = maximum_area
        self._dilation_iterations = dilation_iterations
        self._pixel_spacing_meters = pixel_spacing_meters
        self._filter_type = filter_type


    def detect(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
    ) -> DetectionResult:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
            raise ValueError("Detection threshold must be an integer between 0 and 255")
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(f"Unable to read SAR image: {image_path}")

        if dem_path is not None:
            image = self._mask_land(image, dem_path)

        if self._filter_type != "none":
            filtered_image = preprocess_sar(image, filter_type=self._filter_type)
        else:
            filtered_image = image

        _, binary = cv2.threshold(filtered_image, threshold, 255, cv2.THRESH_BINARY)
        kernel = np.ones((5, 5), np.uint8)
        dilated = cv2.dilate(binary, kernel, iterations=self._dilation_iterations)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[ShipDetection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if self._minimum_area <= area <= self._maximum_area:
                x, y, width, height = cv2.boundingRect(contour)

                # Oriented Bounding Box (OBB)
                (cx, cy), (dim1, dim2), raw_angle = cv2.minAreaRect(contour)
                if dim1 < dim2:
                    beam_px, length_px = dim1, dim2
                    angle = raw_angle + 90.0
                else:
                    beam_px, length_px = dim2, dim1
                    angle = raw_angle

                # Normalize angle to [-90, 90]
                while angle > 90.0:
                    angle -= 180.0
                while angle < -90.0:
                    angle += 180.0

                length_m = max(1.0, length_px * self._pixel_spacing_meters)
                beam_m = max(1.0, beam_px * self._pixel_spacing_meters)

                # 4 corner vertices
                box_pts = cv2.boxPoints(((cx, cy), (dim1, dim2), raw_angle))
                polygon_pts = tuple((float(pt[0]), float(pt[1])) for pt in box_pts)

                # Estimate detection confidence based on peak backscatter intensity
                mask = np.zeros(image.shape, dtype=np.uint8)
                cv2.drawContours(mask, [contour], -1, 255, -1)
                mean_val = cv2.mean(image, mask=mask)[0]
                confidence = float(np.clip((mean_val - threshold) / max(1.0, 255.0 - threshold), 0.1, 1.0))

                detections.append(
                    ShipDetection(
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        confidence=round(confidence, 3),
                        angle=round(float(angle), 1),
                        length=round(float(length_m), 1),
                        beam=round(float(beam_m), 1),
                        center_x=round(float(cx), 1),
                        center_y=round(float(cy), 1),
                        polygon_points=polygon_pts,
                    )
                )

        height, width = image.shape[:2]
        return DetectionResult(detections, width, height)

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
