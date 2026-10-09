"""Deep Learning Oriented Bounding Box (OBB) ship detector and SAR-CNN classification engine.

Provides pluggable ONNX Runtime and OpenCV DNN inference backends with sliding-window
tiling, Rotated Non-Maximum Suppression (Rotated NMS), multi-class vessel taxonomy
(Cargo, Tanker, Fishing, Passenger, Tug, Military, Other), and optional wake cross-validation.
"""

from abc import ABC, abstractmethod
import logging
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from sentinel_analysis.application.ports.detection import DetectionResult
from sentinel_analysis.domain.entities import ShipDetection
from sentinel_analysis.infrastructure.detection.wake import ShipWakeDetector
from sentinel_analysis.infrastructure.imagery.preprocessing import preprocess_sar

logger = logging.getLogger(__name__)

VESSEL_CLASSES: dict[int, str] = {
    0: "Cargo",
    1: "Tanker",
    2: "Fishing",
    3: "Passenger",
    4: "Tug",
    5: "Military",
    6: "Other",
}


class InferenceBackend(ABC):
    """Abstract inference backend for running neural network models."""

    @abstractmethod
    def infer(self, blob: np.ndarray) -> np.ndarray | list[np.ndarray]:
        """Run forward inference on preprocessed tensor."""

    def close(self) -> None:
        """Release any allocated engine resources."""


class OpenCVDNNBackend(InferenceBackend):
    """OpenCV DNN inference engine for ONNX models."""

    def __init__(self, model_path: Path | str, prefer_cuda: bool = False) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"ONNX model file not found: {self.model_path}")

        self.net = cv2.dnn.readNetFromONNX(str(self.model_path))
        if prefer_cuda and cv2.cuda.getCudaEnabledDeviceCount() > 0:
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
        else:
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    def infer(self, blob: np.ndarray) -> np.ndarray | list[np.ndarray]:
        self.net.setInput(blob)
        out_names = self.net.getUnconnectedOutLayersNames()
        if len(out_names) == 1:
            return self.net.forward(out_names[0])
        return self.net.forward(out_names)


class ONNXRuntimeBackend(InferenceBackend):
    """ONNX Runtime inference engine supporting CPU and CUDA Execution Providers."""

    def __init__(
        self,
        model_path: Path | str,
        providers: Optional[Sequence[str]] = None,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"ONNX model file not found: {self.model_path}")

        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "onnxruntime is required for ONNXRuntimeBackend. Install with 'pip install onnxruntime'."
            ) from exc

        available = ort.get_available_providers()
        selected_providers = [p for p in (providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]) if p in available]
        if not selected_providers:
            selected_providers = ["CPUExecutionProvider"]

        self.session = ort.InferenceSession(str(self.model_path), providers=selected_providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]

    def infer(self, blob: np.ndarray) -> np.ndarray | list[np.ndarray]:
        outputs = self.session.run(self.output_names, {self.input_name: blob.astype(np.float32)})
        if len(outputs) == 1:
            return outputs[0]
        return outputs


class MockONNXBackend(InferenceBackend):
    """Synthetic backend returning simulated deep learning predictions for testing."""

    def __init__(
        self,
        simulated_predictions: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self.simulated_predictions = simulated_predictions or []

    def infer(self, blob: np.ndarray) -> np.ndarray:
        # Returns empty or dummy tensor shape [batch, 7, num_predictions]
        return np.zeros((1, 7, len(self.simulated_predictions)), dtype=np.float32)


class SARCNNClassifier:
    """Classify SAR vessel chips into vessel categories (Cargo, Tanker, Fishing, etc.)."""

    def __init__(
        self,
        model_path: Optional[Path | str] = None,
        backend: Optional[InferenceBackend] = None,
        vessel_classes: Optional[dict[int, str]] = None,
    ) -> None:
        self.classes = vessel_classes or VESSEL_CLASSES
        self.backend = backend
        if self.backend is None and model_path is not None:
            p = Path(model_path)
            if p.exists():
                try:
                    self.backend = OpenCVDNNBackend(p)
                except Exception as exc:
                    logger.warning("Failed to initialize OpenCV DNN classifier backend: %s", exc)

    def classify_chip(
        self,
        chip: np.ndarray,
        length_m: float,
        beam_m: float,
        mean_intensity: float,
        peak_intensity: float,
    ) -> tuple[str, float]:
        """Classify a ship target using neural network inference or SAR physical heuristics."""
        if self.backend is not None and chip.size > 0:
            try:
                resized = cv2.resize(chip, (64, 64)).astype(np.float32) / 255.0
                blob = resized[np.newaxis, np.newaxis, :, :]  # [1, 1, 64, 64]
                raw_out = self.backend.infer(blob)
                logits = raw_out[0] if isinstance(raw_out, list) else raw_out
                logits = logits.flatten()
                if len(logits) >= len(self.classes):
                    # Softmax
                    exp_l = np.exp(logits - np.max(logits))
                    probs = exp_l / np.sum(exp_l)
                    best_idx = int(np.argmax(probs))
                    confidence = float(probs[best_idx])
                    predicted_class = self.classes.get(best_idx, "Other")
                    return predicted_class, round(confidence, 3)
            except Exception as exc:
                logger.debug("Neural classification fallback due to: %s", exc)

        return self._classify_physical(length_m, beam_m, mean_intensity, peak_intensity)

    @staticmethod
    def _classify_physical(
        length_m: float,
        beam_m: float,
        mean_intensity: float,
        peak_intensity: float,
    ) -> tuple[str, float]:
        """Physical rule-based SAR ship classification based on dimensions and radar cross-section."""
        aspect_ratio = length_m / max(1.0, beam_m)

        # High radar return with sharp angular features & streamlined aspect ratio (corvettes, frigates, patrol)
        if 40.0 <= length_m < 160.0 and peak_intensity >= 230 and aspect_ratio >= 4.8:
            return "Military", 0.80

        # Very large vessels: Tankers / Bulkers / Large Container
        if length_m >= 180.0:
            if aspect_ratio >= 5.5:
                return "Cargo", 0.90
            return "Tanker", 0.88

        # Medium-large commercial vessels (70m - 180m)
        if length_m >= 70.0:
            if aspect_ratio < 3.8 and mean_intensity > 120:
                return "Passenger", 0.82
            if aspect_ratio >= 4.0:
                return "Cargo", 0.85
            return "Tanker", 0.80

        # Squat working vessels (Tugs, Workboats)
        if length_m < 45.0 and aspect_ratio <= 2.8:
            return "Tug", 0.82

        # Small fishing craft
        if length_m < 50.0:
            return "Fishing", 0.86

        return "Other", 0.70


class DeepLearningShipDetector:
    """Deep learning ship detector supporting Oriented Bounding Boxes (OBB) and SAR-CNN classification.

    Features:
    - Pluggable ONNX backends (OpenCV DNN or ONNX Runtime)
    - Sliding window tiling for large SAR scenes
    - Rotated Non-Maximum Suppression (Rotated NMS) via cv2.dnn.NMSBoxesRotated
    - Oriented Bounding Box geometry with corner vertices and aspect ratio
    - Multi-class vessel classification (Cargo, Tanker, Fishing, Passenger, Tug, Military, Other)
    - Optional SAR wake analysis integration
    """

    def __init__(
        self,
        model_path: Optional[Path | str] = None,
        classifier_path: Optional[Path | str] = None,
        backend: str = "auto",
        confidence_threshold: float = 0.35,
        nms_threshold: float = 0.4,
        tile_size: int = 512,
        tile_overlap: int = 128,
        pixel_spacing_meters: float = 10.0,
        vessel_classes: Optional[dict[int, str]] = None,
        enable_wake_detection: bool = False,
        wake_detector: Any = None,
        settings_repo: Any = None,
    ) -> None:
        self._model_path = Path(model_path) if model_path is not None else None
        self._classifier_path = Path(classifier_path) if classifier_path is not None else None
        self._backend_type = backend
        self._confidence_threshold = confidence_threshold
        self._nms_threshold = nms_threshold
        self._tile_size = tile_size
        self._tile_overlap = tile_overlap
        self._pixel_spacing_meters = pixel_spacing_meters
        self._vessel_classes = vessel_classes or VESSEL_CLASSES
        self._enable_wake_detection = enable_wake_detection
        self._wake_detector = wake_detector or ShipWakeDetector(pixel_spacing_meters=pixel_spacing_meters)
        self._settings_repo = settings_repo

        # Initialize inference backends
        self._detection_backend = self._init_detection_backend()
        self._classifier = SARCNNClassifier(
            model_path=self._classifier_path,
            vessel_classes=self._vessel_classes,
        )

    def _init_detection_backend(self) -> Optional[InferenceBackend]:
        if self._model_path is None or not self._model_path.exists():
            if self._backend_type == "mock":
                return MockONNXBackend()
            return None

        if self._backend_type in ("auto", "onnxruntime"):
            try:
                return ONNXRuntimeBackend(self._model_path)
            except Exception as exc:
                if self._backend_type == "onnxruntime":
                    raise
                logger.debug("Falling back to OpenCV DNN backend: %s", exc)

        if self._backend_type in ("auto", "opencv"):
            try:
                return OpenCVDNNBackend(self._model_path)
            except Exception as exc:
                logger.warning("Failed to initialize OpenCV DNN backend for %s: %s", self._model_path, exc)
                return None

        return None

    def detect(
        self,
        image_path: Path,
        dem_path: Optional[Path] = None,
        threshold: int = 40,
        coastal_buffer: Optional[int] = None,
        enable_wake_detection: Optional[bool] = None,
    ) -> DetectionResult:
        """Detect ships in SAR imagery with Oriented Bounding Boxes (OBB)."""
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(f"Unable to read SAR image: {image_path}")

        buffer_px = (
            coastal_buffer
            if coastal_buffer is not None
            else (self._settings_repo.get("coastal_buffer_pixels", 81) if self._settings_repo else 81)
        )
        pixel_spacing = (
            self._settings_repo.get("pixel_spacing_meters", self._pixel_spacing_meters)
            if self._settings_repo
            else self._pixel_spacing_meters
        )
        conf_thresh = (
            self._settings_repo.get("deep_learning_confidence", self._confidence_threshold)
            if self._settings_repo
            else self._confidence_threshold
        )
        nms_thresh = (
            self._settings_repo.get("deep_learning_nms_threshold", self._nms_threshold)
            if self._settings_repo
            else self._nms_threshold
        )
        do_wake = (
            enable_wake_detection
            if enable_wake_detection is not None
            else (
                self._settings_repo.get("enable_wake_detection", self._enable_wake_detection)
                if self._settings_repo
                else self._enable_wake_detection
            )
        )

        if dem_path is not None:
            image = self._mask_land(image, dem_path, coastal_buffer_pixels=buffer_px)

        img_h, img_w = image.shape[:2]

        # Candidate accumulation
        raw_candidates: list[dict[str, Any]] = []

        if self._detection_backend is not None and not isinstance(self._detection_backend, MockONNXBackend):
            # End-to-end sliding window OBB deep learning inference
            raw_candidates = self._run_tiled_inference(image, conf_thresh)
        else:
            # High-sensitivity candidate proposal stage followed by SAR-CNN classification
            raw_candidates = self._run_proposal_candidates(image, threshold, conf_thresh, pixel_spacing)

        # Apply Rotated Non-Maximum Suppression
        final_detections = self._apply_rotated_nms(
            image,
            raw_candidates,
            conf_thresh=conf_thresh,
            nms_thresh=nms_thresh,
            pixel_spacing=pixel_spacing,
            do_wake=do_wake,
        )

        return DetectionResult(final_detections, img_w, img_h)

    def _run_tiled_inference(self, image: np.ndarray, conf_thresh: float) -> list[dict[str, Any]]:
        """Run sliding-window inference over the image using the detection neural network."""
        img_h, img_w = image.shape[:2]
        step = max(64, self._tile_size - self._tile_overlap)
        candidates = []

        y_starts = list(range(0, max(1, img_h - self._tile_size + 1), step))
        if y_starts[-1] + self._tile_size < img_h:
            y_starts.append(img_h - self._tile_size)

        x_starts = list(range(0, max(1, img_w - self._tile_size + 1), step))
        if x_starts[-1] + self._tile_size < img_w:
            x_starts.append(img_w - self._tile_size)

        for y0 in y_starts:
            for x0 in x_starts:
                tile = image[y0 : y0 + self._tile_size, x0 : x0 + self._tile_size]
                if tile.shape[0] != self._tile_size or tile.shape[1] != self._tile_size:
                    padded = np.zeros((self._tile_size, self._tile_size), dtype=np.uint8)
                    padded[: tile.shape[0], : tile.shape[1]] = tile
                    tile = padded

                norm_tile = tile.astype(np.float32) / 255.0
                blob = norm_tile[np.newaxis, np.newaxis, :, :]  # [1, 1, H, W]

                try:
                    preds = self._detection_backend.infer(blob)
                    parsed = self._decode_obb_predictions(preds, x0, y0, conf_thresh)
                    candidates.extend(parsed)
                except Exception as exc:
                    logger.debug("Inference failed on tile at (%d, %d): %s", x0, y0, exc)

        return candidates

    def _run_proposal_candidates(
        self,
        image: np.ndarray,
        threshold: int,
        conf_thresh: float,
        pixel_spacing: float,
    ) -> list[dict[str, Any]]:
        """Extract ship candidate proposals with oriented bounding boxes."""
        _, binary = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        dilated = cv2.dilate(binary, kernel, iterations=1)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 30 or area > 100000:
                continue

            (cx, cy), (dim1, dim2), raw_angle = cv2.minAreaRect(cnt)
            if dim1 < dim2:
                beam_px, length_px = dim1, dim2
                angle = raw_angle + 90.0
            else:
                beam_px, length_px = dim2, dim1
                angle = raw_angle

            while angle > 90.0:
                angle -= 180.0
            while angle < -90.0:
                angle += 180.0

            length_m = max(1.0, length_px * pixel_spacing)
            beam_m = max(1.0, beam_px * pixel_spacing)

            # Intensity stats within contour
            mask = np.zeros(image.shape, dtype=np.uint8)
            cv2.drawContours(mask, [cnt], -1, 255, -1)
            mean_val = float(cv2.mean(image, mask=mask)[0])
            min_val, max_val, _, _ = cv2.minMaxLoc(image, mask=mask)

            vals = image[mask == 255]
            if len(vals) > 0:
                peak_val = float(max_val)
                target_pixels = vals[vals >= threshold]
                if len(target_pixels) == 0:
                    target_pixels = vals[vals >= np.percentile(vals, 80)]
                core_mean = float(np.mean(target_pixels)) if len(target_pixels) > 0 else peak_val
                denom = max(1.0, 255.0 - float(threshold))
                peak_contrast = (peak_val - float(threshold)) / denom
                core_contrast = (core_mean - float(threshold)) / denom
                det_conf = float(np.clip(0.5 * peak_contrast + 0.5 * core_contrast, 0.1, 1.0))
            else:
                det_conf = 0.5

            if det_conf < conf_thresh:
                continue

            # Chip crop for classifier
            x, y, w, h = cv2.boundingRect(cnt)
            pad = 10
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(image.shape[1], x + w + pad), min(image.shape[0], y + h + pad)
            chip = image[y1:y2, x1:x2]

            v_class, class_conf = self._classifier.classify_chip(
                chip=chip,
                length_m=length_m,
                beam_m=beam_m,
                mean_intensity=mean_val,
                peak_intensity=max_val,
            )

            candidates.append({
                "center_x": cx,
                "center_y": cy,
                "width_px": beam_px,
                "height_px": length_px,
                "angle": angle,
                "confidence": det_conf,
                "vessel_class": v_class,
                "classification_confidence": class_conf,
                "length_m": length_m,
                "beam_m": beam_m,
                "contour": cnt,
            })

        return candidates

    def _decode_obb_predictions(
        self,
        preds: np.ndarray | list[np.ndarray],
        x_offset: int,
        y_offset: int,
        conf_thresh: float,
    ) -> list[dict[str, Any]]:
        """Decode raw neural network output into oriented bounding box candidates."""
        candidates = []
        out = preds[0] if isinstance(preds, list) else preds
        if out is None or out.size == 0:
            return candidates

        # Flatten batch dimension if present
        if out.ndim == 3 and out.shape[0] == 1:
            out = out[0]

        # Format: [num_boxes, 7+] or [7+, num_boxes]
        if out.shape[0] < out.shape[1] and out.shape[0] >= 6:
            out = out.T

        for row in out:
            if len(row) < 6:
                continue
            cx_local, cy_local, w_box, h_box = float(row[0]), float(row[1]), float(row[2]), float(row[3])
            score = float(row[4])
            if score < conf_thresh:
                continue

            angle_rad = float(row[5])
            angle_deg = float(np.degrees(angle_rad))
            while angle_deg > 90.0:
                angle_deg -= 180.0
            while angle_deg < -90.0:
                angle_deg += 180.0

            class_id = int(row[6]) if len(row) > 6 else 0
            v_class = self._vessel_classes.get(class_id, "Other")

            candidates.append({
                "center_x": cx_local + x_offset,
                "center_y": cy_local + y_offset,
                "width_px": w_box,
                "height_px": h_box,
                "angle": angle_deg,
                "confidence": score,
                "vessel_class": v_class,
                "classification_confidence": score,
                "length_m": max(1.0, max(w_box, h_box) * self._pixel_spacing_meters),
                "beam_m": max(1.0, min(w_box, h_box) * self._pixel_spacing_meters),
            })

        return candidates

    def _apply_rotated_nms(
        self,
        image: np.ndarray,
        candidates: list[dict[str, Any]],
        conf_thresh: float,
        nms_thresh: float,
        pixel_spacing: float,
        do_wake: bool,
    ) -> list[ShipDetection]:
        """Apply Rotated NMS and build final ShipDetection domain entities."""
        if not candidates:
            return []

        # Prepare OpenCV rotated boxes: ((cx, cy), (width, height), angle_deg)
        boxes = []
        scores = []
        for c in candidates:
            cx = float(c["center_x"])
            cy = float(c["center_y"])
            w = float(c["width_px"])
            h = float(c["height_px"])
            angle = float(c["angle"])
            boxes.append(((cx, cy), (w, h), angle))
            scores.append(float(c["confidence"]))

        indices = cv2.dnn.NMSBoxesRotated(boxes, scores, conf_thresh, nms_thresh)
        if len(indices) == 0:
            return []

        if isinstance(indices, np.ndarray):
            keep_indices = [int(i) for i in indices.flatten()]
        else:
            keep_indices = [int(i[0] if isinstance(i, (list, tuple, np.ndarray)) else i) for i in indices]

        detections: list[ShipDetection] = []
        for idx in keep_indices:
            c = candidates[idx]
            cx = float(c["center_x"])
            cy = float(c["center_y"])
            w_px = float(c["width_px"])
            h_px = float(c["height_px"])
            angle = float(c["angle"])
            conf = float(c["confidence"])
            v_class = c.get("vessel_class")
            cls_conf = c.get("classification_confidence")

            # Corner points of rotated box
            pts_box = cv2.boxPoints(((cx, cy), (w_px, h_px), angle))
            polygon_pts = tuple((float(pt[0]), float(pt[1])) for pt in pts_box)

            # Axis-aligned bounding box bounding rectangle
            x_min = max(0, int(np.min(pts_box[:, 0])))
            y_min = max(0, int(np.min(pts_box[:, 1])))
            x_max = min(image.shape[1], int(np.max(pts_box[:, 0])))
            y_max = min(image.shape[0], int(np.max(pts_box[:, 1])))
            bbox_w = max(1, x_max - x_min)
            bbox_h = max(1, y_max - y_min)

            # Dimension in meters
            dim_longer = max(w_px, h_px)
            dim_shorter = min(w_px, h_px)
            length_m = max(1.0, dim_longer * pixel_spacing)
            beam_m = max(1.0, dim_shorter * pixel_spacing)

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
                            "width": bbox_w,
                            "height": bbox_h,
                        },
                        pixel_spacing_m=pixel_spacing,
                    )
                    if wake_res.wake_detected:
                        wake_detected = True
                        wake_heading = wake_res.true_heading_deg
                        wake_speed = wake_res.estimated_speed_knots
                        wake_confidence = wake_res.wake_confidence
                except Exception as exc:
                    logger.debug("Wake detection error: %s", exc)

            detections.append(
                ShipDetection(
                    x=x_min,
                    y=y_min,
                    width=bbox_w,
                    height=bbox_h,
                    confidence=round(conf, 3),
                    angle=round(angle, 1),
                    length=round(length_m, 1),
                    beam=round(beam_m, 1),
                    center_x=round(cx, 1),
                    center_y=round(cy, 1),
                    polygon_points=polygon_pts,
                    vessel_class=v_class,
                    classification_confidence=round(cls_conf, 3) if cls_conf is not None else None,
                    wake_detected=wake_detected,
                    wake_heading=wake_heading,
                    wake_speed_knots=wake_speed,
                    wake_confidence=wake_confidence,
                )
            )

        return detections

    @staticmethod
    def _mask_land(
        image: np.ndarray,
        dem_path: Path,
        coastal_buffer_pixels: int = 81,
        morph_close_kernel: int = 27,
    ) -> np.ndarray:
        """Mask out land pixels using digital elevation model data."""
        dem = cv2.imread(str(dem_path), cv2.IMREAD_GRAYSCALE)
        if dem is None:
            return image

        if dem.shape != image.shape:
            dem = cv2.resize(dem, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)

        _, land_mask = cv2.threshold(dem, 1, 255, cv2.THRESH_BINARY)
        if morph_close_kernel > 0:
            k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_close_kernel, morph_close_kernel))
            land_mask = cv2.morphologyEx(land_mask, cv2.MORPH_CLOSE, k_close)

        if coastal_buffer_pixels > 0:
            k_buff = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (coastal_buffer_pixels, coastal_buffer_pixels))
            land_mask = cv2.dilate(land_mask, k_buff, iterations=1)

        masked_image = image.copy()
        masked_image[land_mask > 0] = 0
        return masked_image
