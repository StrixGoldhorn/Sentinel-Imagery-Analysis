"""OpenCV implementation of the ship-detector port with Oriented Bounding Box (OBB) support."""

from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from sentinel_analysis.application.ports.detection import DetectionResult
from sentinel_analysis.domain.entities import ShipDetection
from sentinel_analysis.infrastructure.detection.cfar import (
    ca_cfar_2d,
    fuse_dual_polarization,
    go_cfar_2d,
    so_cfar_2d,
)
from sentinel_analysis.infrastructure.detection.wake import ShipWakeDetector
from sentinel_analysis.infrastructure.imagery.preprocessing import preprocess_sar


class ClassicalShipDetector:
    """Detect bright connected regions in grayscale SAR imagery with Oriented Bounding Boxes (OBB).

    Supports classical global thresholding as well as adaptive Constant False Alarm Rate
    (CA-CFAR, GO-CFAR, SO-CFAR) clutter filtering and dual-polarization (VV/VH) channel fusion.
    """

    def __init__(
        self,
        minimum_area: float = 50,
        maximum_area: float = 5000,
        dilation_iterations: int = 2,
        pixel_spacing_meters: float = 10.0,
        filter_type: str = "none",
        min_area: float | None = None,
        pixel_spacing_m: float | None = None,
        coastal_buffer_pixels: int = 81,
        morph_close_kernel: int = 27,
        detection_method: str = "threshold",
        cfar_guard_size: int = 5,
        cfar_train_size: int = 15,
        cfar_factor: float = 3.5,
        dual_pol_mode: str = "none",
        enable_wake_detection: bool = False,
        wake_detector: Any = None,
        settings_repo: Any = None,
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
        if isinstance(coastal_buffer_pixels, bool) or not isinstance(coastal_buffer_pixels, int) or coastal_buffer_pixels < 0:
            raise ValueError("Coastal buffer pixels must be a non-negative integer")
        if isinstance(morph_close_kernel, bool) or not isinstance(morph_close_kernel, int) or morph_close_kernel < 0:
            raise ValueError("Morph close kernel must be a non-negative integer")
        self._minimum_area = minimum_area
        self._maximum_area = maximum_area
        self._dilation_iterations = dilation_iterations
        self._pixel_spacing_meters = pixel_spacing_meters
        self._filter_type = filter_type
        self._coastal_buffer_pixels = coastal_buffer_pixels
        self._morph_close_kernel = morph_close_kernel
        self._detection_method = detection_method
        self._cfar_guard_size = cfar_guard_size
        self._cfar_train_size = cfar_train_size
        self._cfar_factor = cfar_factor
        self._dual_pol_mode = dual_pol_mode
        self._enable_wake_detection = enable_wake_detection
        self._wake_detector = wake_detector or ShipWakeDetector(pixel_spacing_meters=pixel_spacing_meters)
        self._settings_repo = settings_repo


    def detect(
        self,
        image_path: Path,
        dem_path: Path | None = None,
        threshold: int = 40,
        coastal_buffer: int | None = None,
        detection_method: str | None = None,
        vh_path: Path | None = None,
        enable_wake_detection: bool | None = None,
    ) -> DetectionResult:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
            raise ValueError("Detection threshold must be an integer between 0 and 255")
        if coastal_buffer is not None:
            if isinstance(coastal_buffer, bool) or not isinstance(coastal_buffer, int) or coastal_buffer < 0:
                raise ValueError("Coastal buffer must be a non-negative integer")
            buffer_px = coastal_buffer
        else:
            buffer_px = self._settings_repo.get("coastal_buffer_pixels", self._coastal_buffer_pixels) if self._settings_repo else self._coastal_buffer_pixels

        morph_close_kernel = self._settings_repo.get("morph_close_kernel", self._morph_close_kernel) if self._settings_repo else self._morph_close_kernel
        filter_type = self._settings_repo.get("filter_type", self._filter_type) if self._settings_repo else self._filter_type
        dilation_iterations = self._settings_repo.get("dilation_iterations", self._dilation_iterations) if self._settings_repo else self._dilation_iterations
        minimum_area = self._settings_repo.get("minimum_area", self._minimum_area) if self._settings_repo else self._minimum_area
        maximum_area = self._settings_repo.get("maximum_area", self._maximum_area) if self._settings_repo else self._maximum_area
        pixel_spacing = self._settings_repo.get("pixel_spacing_meters", self._pixel_spacing_meters) if self._settings_repo else self._pixel_spacing_meters

        method = detection_method or (self._settings_repo.get("detection_method", self._detection_method) if self._settings_repo else self._detection_method)
        cfar_guard = self._settings_repo.get("cfar_guard_size", self._cfar_guard_size) if self._settings_repo else self._cfar_guard_size
        cfar_train = self._settings_repo.get("cfar_train_size", self._cfar_train_size) if self._settings_repo else self._cfar_train_size
        cfar_factor = self._settings_repo.get("cfar_factor", self._cfar_factor) if self._settings_repo else self._cfar_factor
        dual_pol_mode = self._settings_repo.get("dual_pol_mode", self._dual_pol_mode) if self._settings_repo else self._dual_pol_mode
        do_wake = (
            enable_wake_detection
            if enable_wake_detection is not None
            else (self._settings_repo.get("enable_wake_detection", self._enable_wake_detection) if self._settings_repo else self._enable_wake_detection)
        )

        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(f"Unable to read SAR image: {image_path}")

        # Check for dual-polarization pair (e.g. VV + VH)
        resolved_vh_path = vh_path
        if resolved_vh_path is None and dual_pol_mode != "none":
            str_path = str(image_path)
            candidate = None
            if "_vv" in str_path:
                candidate = Path(str_path.replace("_vv", "_vh"))
            elif "_vh" in str_path:
                candidate = Path(str_path.replace("_vh", "_vv"))
            if candidate and candidate.exists():
                resolved_vh_path = candidate

        if resolved_vh_path is not None and resolved_vh_path.exists() and dual_pol_mode != "none":
            vh_image = cv2.imread(str(resolved_vh_path), cv2.IMREAD_GRAYSCALE)
            if vh_image is not None:
                image = fuse_dual_polarization(image, vh_image, mode=dual_pol_mode)

        if dem_path is not None:
            image = self._mask_land(image, dem_path, coastal_buffer_pixels=buffer_px, morph_close_kernel=morph_close_kernel)

        if filter_type != "none":
            filtered_image = preprocess_sar(image, filter_type=filter_type)
        else:
            filtered_image = image

        if method == "cfar_ca":
            binary, _ = ca_cfar_2d(filtered_image, guard_size=cfar_guard, train_size=cfar_train, factor=cfar_factor, min_threshold=threshold)
        elif method == "cfar_go":
            binary, _ = go_cfar_2d(filtered_image, guard_size=cfar_guard, train_size=cfar_train, factor=cfar_factor, min_threshold=threshold)
        elif method == "cfar_so":
            binary, _ = so_cfar_2d(filtered_image, guard_size=cfar_guard, train_size=cfar_train, factor=cfar_factor, min_threshold=threshold)
        else:
            _, binary = cv2.threshold(filtered_image, threshold, 255, cv2.THRESH_BINARY)

        kernel = np.ones((5, 5), np.uint8)
        dilated = cv2.dilate(binary, kernel, iterations=dilation_iterations)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[ShipDetection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if minimum_area <= area <= maximum_area:
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

                length_m = max(1.0, length_px * pixel_spacing)
                beam_m = max(1.0, beam_px * pixel_spacing)

                # 4 corner vertices
                box_pts = cv2.boxPoints(((cx, cy), (dim1, dim2), raw_angle))
                polygon_pts = tuple((float(pt[0]), float(pt[1])) for pt in box_pts)

                # Estimate detection confidence based on peak backscatter intensity
                mask = np.zeros(image.shape, dtype=np.uint8)
                cv2.drawContours(mask, [contour], -1, 255, -1)
                mean_val = cv2.mean(image, mask=mask)[0]
                confidence = float(np.clip((mean_val - threshold) / max(1.0, 255.0 - threshold), 0.1, 1.0))

                wake_detected = None
                wake_heading = None
                wake_speed = None
                wake_confidence = None

                if do_wake and self._wake_detector is not None:
                    try:
                        wake_res = self._wake_detector.analyze_detection(
                            image,
                            {
                                "center_x": cx,
                                "center_y": cy,
                                "length": length_m,
                                "beam": beam_m,
                                "angle": angle,
                                "width": width,
                                "height": height,
                            },
                            pixel_spacing_m=pixel_spacing,
                        )
                        if wake_res.wake_detected:
                            wake_detected = True
                            wake_heading = wake_res.true_heading_deg
                            wake_speed = wake_res.estimated_speed_knots
                            wake_confidence = wake_res.wake_confidence
                    except Exception:
                        pass

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
                        wake_detected=wake_detected,
                        wake_heading=wake_heading,
                        wake_speed_knots=wake_speed,
                        wake_confidence=wake_confidence,
                    )
                )

        height, width = image.shape[:2]
        return DetectionResult(detections, width, height)

    @staticmethod
    def _mask_land(
        image: np.ndarray,
        dem_path: Path,
        coastal_buffer_pixels: int = 81,
        morph_close_kernel: int = 27,
    ) -> np.ndarray:
        dem = cv2.imread(str(dem_path), cv2.IMREAD_GRAYSCALE)
        if dem is None:
            raise FileNotFoundError(f"Unable to read DEM image: {dem_path}")
        if dem.shape[:2] != image.shape[:2]:
            dem = cv2.resize(dem, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)

        if np.max(dem) <= 0:
            return image

        _, mask = cv2.threshold(dem, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        if morph_close_kernel > 1:
            k_close = morph_close_kernel if morph_close_kernel % 2 != 0 else morph_close_kernel + 1
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k_close, k_close), np.uint8), iterations=2)
        if coastal_buffer_pixels > 0:
            k_buf = coastal_buffer_pixels if coastal_buffer_pixels % 2 != 0 else coastal_buffer_pixels + 1
            mask = cv2.dilate(mask, np.ones((k_buf, k_buf), np.uint8), iterations=1)
        return cv2.bitwise_and(image, image, mask=cv2.bitwise_not(mask))
