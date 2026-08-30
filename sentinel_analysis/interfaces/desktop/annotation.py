"""OpenCV implementation of the interactive annotation editor port."""

from pathlib import Path

import cv2
import numpy as np

from sentinel_analysis.application.ports.annotation import AnnotationTile


def load_image(image_path: Path | str) -> np.ndarray:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Unable to read image: {image_path}")
    return image


def mask_image_with_dem(image: np.ndarray, dem_path: Path | str) -> tuple[np.ndarray, np.ndarray]:
    dem = load_image(dem_path)
    if dem.shape != image.shape:
        dem = cv2.resize(dem, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)
    _, mask = cv2.threshold(dem, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((27, 27), np.uint8), iterations=2)
    expanded = cv2.dilate(mask, np.ones((81, 81), np.uint8), iterations=2)
    return cv2.bitwise_and(image, image, mask=cv2.bitwise_not(expanded)), mask


def rough_ship_boxes(
    image: np.ndarray,
    minimum_area: float = 50,
    maximum_area: float = 5000,
    threshold: int = 40,
) -> list[tuple[int, int, int, int]]:
    if minimum_area < 0 or maximum_area <= minimum_area:
        raise ValueError("Detection area limits must be ordered positive values")
    if not 0 <= threshold <= 255:
        raise ValueError("Detection threshold must be between 0 and 255")
    _, binary = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
    dilated = cv2.dilate(binary, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if minimum_area <= area <= maximum_area:
            boxes.append(cv2.boundingRect(contour))
    return boxes


def load_boxes_from_file(label_path: Path | str) -> list[list[int]]:
    path = Path(label_path)
    if not path.is_file():
        return []
    boxes: list[list[int]] = []
    try:
        content = path.read_text(encoding="utf-8").strip()
        for line in content.splitlines():
            parts = line.strip().split()
            if len(parts) == 5:
                # Format: class_id x1 y1 x2 y2
                try:
                    boxes.append([int(float(parts[1])), int(float(parts[2])), int(float(parts[3])), int(float(parts[4]))])
                except ValueError:
                    continue
            elif len(parts) == 4:
                # Format: x1 y1 x2 y2
                try:
                    boxes.append([int(float(parts[0])), int(float(parts[1])), int(float(parts[2])), int(float(parts[3]))])
                except ValueError:
                    continue
    except OSError:
        pass
    return boxes


def lee_filter(image: np.ndarray, window_size: int = 5, noise_variance: float = 0.25) -> np.ndarray:
    if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size <= 0 or window_size % 2 == 0:
        raise ValueError("Lee-filter window size must be a positive odd integer")
    if noise_variance < 0:
        raise ValueError("Lee-filter noise variance cannot be negative")
    image_float = image.astype(np.float64)
    kernel_size = (window_size, window_size)
    local_mean = cv2.boxFilter(image_float, -1, kernel_size)
    local_mean_squared = cv2.boxFilter(image_float**2, -1, kernel_size)
    local_variance = np.maximum(local_mean_squared - local_mean**2, 0)
    noise = local_mean**2 * noise_variance
    weight = np.divide(
        local_variance - noise,
        local_variance + noise,
        out=np.zeros_like(local_variance),
        where=(local_variance + noise) != 0,
    )
    return (local_mean + weight * (image_float - local_mean)).astype(image.dtype)


class OpenCVBoxEditor:
    """Edit rectangular labels with zoom, pan, add, delete, undo, and clear controls."""

    def __init__(
        self,
        image: np.ndarray,
        initial_boxes: list[tuple[int, int, int, int]] | list[list[int]],
        output_path: Path | str,
        window_size: tuple[int, int] = (1280, 720),
    ) -> None:
        if image.size == 0:
            raise ValueError("Annotation image cannot be empty")
        self.image = image
        self.boxes: list[list[int]] = []
        for box in initial_boxes:
            if len(box) == 4:
                x1, y1, third, fourth = box
                # If width/height format, convert to x2, y2
                if third < x1 or fourth < y1 or (third <= (image.shape[1] - x1) and fourth <= (image.shape[0] - y1) and third < image.shape[1] // 2 and fourth < image.shape[0] // 2):
                    self.boxes.append([int(x1), int(y1), int(x1 + third), int(y1 + fourth)])
                else:
                    self.boxes.append([int(x1), int(y1), int(third), int(fourth)])
        self.output_path = Path(output_path)
        self.window_width, self.window_height = window_size
        height, width = image.shape[:2]
        self.zoom = min(self.window_width / width, self.window_height / height)
        self.pan_x = (self.window_width - width * self.zoom) / 2
        self.pan_y = (self.window_height - height * self.zoom) / 2
        self.is_panning = False
        self.is_drawing = False
        self.start_x = self.start_y = self.current_x = self.current_y = 0
        self.pan_start_x = self.pan_start_y = 0
        self.window_name = "SAR Annotator (Wheel=Zoom | Mid=Pan | L-Drag=Draw | R-Click=Del | Z=Undo | C=Clear | S=Save | Q=Quit)"

    def _image_coordinates(self, display_x: int, display_y: int) -> tuple[float, float]:
        return (display_x - self.pan_x) / self.zoom, (display_y - self.pan_y) / self.zoom

    def mouse_callback(self, event: int, x: int, y: int, flags: int, parameter: object) -> None:
        if event == cv2.EVENT_MOUSEWHEEL:
            old_zoom = self.zoom
            self.zoom = float(np.clip(self.zoom * (1.2 if flags > 0 else 1 / 1.2), 0.1, 10.0))
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
            self.start_x = self.current_x = x
            self.start_y = self.current_y = y
        elif event == cv2.EVENT_LBUTTONUP:
            self.is_drawing = False
            first_x, first_y = self._image_coordinates(self.start_x, self.start_y)
            second_x, second_y = self._image_coordinates(self.current_x, self.current_y)
            height, width = self.image.shape[:2]
            x1 = max(0, min(width, int(min(first_x, second_x))))
            y1 = max(0, min(height, int(min(first_y, second_y))))
            x2 = max(0, min(width, int(max(first_x, second_x))))
            y2 = max(0, min(height, int(max(first_y, second_y))))
            if x2 - x1 > 4 and y2 - y1 > 4:
                self.boxes.append([x1, y1, x2, y2])
        elif event == cv2.EVENT_RBUTTONDOWN:
            image_x, image_y = self._image_coordinates(x, y)
            for index in range(len(self.boxes) - 1, -1, -1):
                x1, y1, x2, y2 = self.boxes[index]
                if x1 <= image_x <= x2 and y1 <= image_y <= y2:
                    self.boxes.pop(index)
                    break

    def render(self) -> np.ndarray:
        image_height, image_width = self.image.shape[:2]
        canvas = np.zeros((self.window_height, self.window_width, 3), dtype=np.uint8)
        source_x1 = max(0, int(-self.pan_x / self.zoom))
        source_y1 = max(0, int(-self.pan_y / self.zoom))
        source_x2 = min(image_width, source_x1 + int(self.window_width / self.zoom))
        source_y2 = min(image_height, source_y1 + int(self.window_height / self.zoom))

        if source_x2 > source_x1 and source_y2 > source_y1:
            crop = self.image[source_y1:source_y2, source_x1:source_x2]
            if crop.ndim == 2:
                crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            crop_width = max(1, int((source_x2 - source_x1) * self.zoom))
            crop_height = max(1, int((source_y2 - source_y1) * self.zoom))
            resized = cv2.resize(crop, (crop_width, crop_height))
            canvas_x = int(self.pan_x + source_x1 * self.zoom)
            canvas_y = int(self.pan_y + source_y1 * self.zoom)
            destination_x1 = max(0, canvas_x)
            destination_y1 = max(0, canvas_y)
            destination_x2 = min(self.window_width, canvas_x + crop_width)
            destination_y2 = min(self.window_height, canvas_y + crop_height)
            if destination_x2 > destination_x1 and destination_y2 > destination_y1:
                input_x1 = max(0, destination_x1 - canvas_x)
                input_y1 = max(0, destination_y1 - canvas_y)
                input_x2 = input_x1 + destination_x2 - destination_x1
                input_y2 = input_y1 + destination_y2 - destination_y1
                canvas[destination_y1:destination_y2, destination_x1:destination_x2] = resized[
                    input_y1:input_y2,
                    input_x1:input_x2,
                ]

        box_thickness = max(1, min(3, int(round(1.5 * self.zoom))))
        for index, (x1, y1, x2, y2) in enumerate(self.boxes, 1):
            first = int(x1 * self.zoom + self.pan_x), int(y1 * self.zoom + self.pan_y)
            second = int(x2 * self.zoom + self.pan_x), int(y2 * self.zoom + self.pan_y)
            # High-visibility bounding box (Cyan / Blue)
            cv2.rectangle(canvas, first, second, (255, 128, 0), box_thickness)
            
            # Text badge with background for crisp readability
            label_text = f"#{index}"
            (text_w, text_h), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            badge_y1 = max(0, first[1] - text_h - 6)
            badge_y2 = max(text_h + 4, first[1])
            cv2.rectangle(canvas, (first[0], badge_y1), (first[0] + text_w + 6, badge_y2), (255, 128, 0), -1)
            cv2.putText(canvas, label_text, (first[0] + 3, badge_y2 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

        if self.is_drawing:
            cv2.rectangle(
                canvas,
                (self.start_x, self.start_y),
                (self.current_x, self.current_y),
                (0, 255, 0),
                2,
            )
        return canvas

    def _save(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output_path.with_name(f"{self.output_path.name}.tmp")
        try:
            content = "".join(f"0 {x1} {y1} {x2} {y2}\n" for x1, y1, x2, y2 in self.boxes)
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(self.output_path)
        finally:
            temporary.unlink(missing_ok=True)

    def run(self) -> bool:
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window_name, self.window_width, self.window_height)
        cv2.setMouseCallback(self.window_name, self.mouse_callback)
        try:
            while True:
                cv2.imshow(self.window_name, self.render())
                key = cv2.waitKey(1) & 0xFF
                if key == ord("s"):
                    self._save()
                    return True
                if key == ord("z") and self.boxes:
                    self.boxes.pop()
                if key == ord("c"):
                    self.boxes.clear()
                if key in {ord("q"), 27}:
                    return False
        finally:
            cv2.destroyWindow(self.window_name)


class OpenCVAnnotationEditor:
    def edit(self, tile: AnnotationTile, label_path: Path, use_lee_filter: bool) -> bool:
        original = load_image(tile.sar_path)
        
        # If label file already exists and has annotations, load them
        existing_boxes = load_boxes_from_file(label_path)
        if existing_boxes:
            initial_boxes = existing_boxes
        else:
            detection_image = original
            if tile.dem_path is not None:
                detection_image, _ = mask_image_with_dem(detection_image, tile.dem_path)
            if use_lee_filter:
                detection_image = lee_filter(detection_image, window_size=7, noise_variance=0.75)
            initial_boxes = rough_ship_boxes(detection_image)

        return OpenCVBoxEditor(original, initial_boxes, label_path).run()

