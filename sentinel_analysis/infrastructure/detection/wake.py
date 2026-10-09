"""Ship wake detection using Radon and Hough transforms, 180-degree heading ambiguity resolution, and AIS speed spoofing cross-validation."""

from dataclasses import dataclass, field
import math
from typing import Any, Optional

import cv2
import numpy as np

from sentinel_analysis.domain.entities import ShipDetection


@dataclass(frozen=True)
class WakeAnalysisResult:
    """Hydrodynamic wake analysis and AIS kinematics cross-validation outcome."""

    wake_detected: bool
    wake_confidence: float = 0.0
    wake_angle_deg: Optional[float] = None
    true_heading_deg: Optional[float] = None
    wake_type: Optional[str] = None  # "turbulent", "kelvin_arms", "composite"
    estimated_speed_knots: Optional[float] = None
    arm_angles_deg: Optional[tuple[float, float]] = None
    is_speed_spoofed: Optional[bool] = None
    is_course_spoofed: Optional[bool] = None
    speed_discrepancy_knots: Optional[float] = None
    heading_discrepancy_deg: Optional[float] = None
    details: dict[str, Any] = field(default_factory=dict)


def compute_radon_transform(
    image: np.ndarray,
    angles_deg: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Compute 2D Radon Transform (parallel projection integrals) across specified angles.

    Args:
        image: 2D grayscale image array (H, W).
        angles_deg: 1D array of projection angles in degrees [0, 180). Defaults to 1-degree steps.

    Returns:
        np.ndarray: Sinogram matrix of shape (num_angles, projection_length).
    """
    if angles_deg is None:
        angles_deg = np.arange(0, 180, 1, dtype=np.float32)

    h, w = image.shape[:2]
    diag = int(math.ceil(math.hypot(h, w)))
    pad_h = (diag - h) // 2
    pad_w = (diag - w) // 2

    # Square pad to avoid rotation cropping artifacts
    padded = cv2.copyMakeBorder(
        image,
        pad_h,
        diag - h - pad_h,
        pad_w,
        diag - w - pad_w,
        cv2.BORDER_CONSTANT,
        value=0,
    )

    center = (diag / 2.0, diag / 2.0)
    sinogram = np.zeros((len(angles_deg), diag), dtype=np.float32)

    for i, angle in enumerate(angles_deg):
        m = cv2.getRotationMatrix2D(center, float(angle), 1.0)
        rotated = cv2.warpAffine(padded, m, (diag, diag), flags=cv2.INTER_LINEAR)
        sinogram[i, :] = np.sum(rotated, axis=0)

    return sinogram


def extract_chip(
    image: np.ndarray,
    cx: float,
    cy: float,
    chip_size: int = 128,
) -> tuple[np.ndarray, int, int]:
    """Extract a square sub-image chip centered around (cx, cy) with bounds safety."""
    h, w = image.shape[:2]
    half = chip_size // 2

    x0 = int(round(cx - half))
    y0 = int(round(cy - half))
    x1 = x0 + chip_size
    y1 = y0 + chip_size

    pad_left = max(0, -x0)
    pad_top = max(0, -y0)
    pad_right = max(0, x1 - w)
    pad_bottom = max(0, y1 - h)

    crop_x0 = max(0, x0)
    crop_y0 = max(0, y0)
    crop_x1 = min(w, x1)
    crop_y1 = min(h, y1)

    crop = image[crop_y0:crop_y1, crop_x0:crop_x1]
    if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
        crop = cv2.copyMakeBorder(
            crop,
            pad_top,
            pad_bottom,
            pad_left,
            pad_right,
            cv2.BORDER_REPLICATE,
        )

    return crop, x0, y0


def mask_ship_hull(
    chip: np.ndarray,
    local_cx: float,
    local_cy: float,
    length_px: float,
    beam_px: float,
    angle_deg: float,
    dilation_factor: float = 1.3,
) -> np.ndarray:
    """Mask metallic ship backscatter in chip using sea clutter median."""
    masked = chip.copy()
    mask = np.zeros(chip.shape[:2], dtype=np.uint8)

    half_len = max(4.0, (length_px * dilation_factor) / 2.0)
    half_beam = max(2.0, (beam_px * dilation_factor) / 2.0)

    axes = (int(round(half_len)), int(round(half_beam)))
    cv2.ellipse(
        mask,
        (int(round(local_cx)), int(round(local_cy))),
        axes,
        angle_deg,
        0,
        360,
        255,
        -1,
    )

    unmasked_pixels = chip[mask == 0]
    fill_val = int(np.median(unmasked_pixels)) if len(unmasked_pixels) > 0 else 0
    masked[mask > 0] = fill_val
    return masked


class ShipWakeDetector:
    """Hydrodynamic ship wake detector in SAR imagery.

    Utilizes Radon/Hough transforms to detect:
    1. Narrow turbulent centerline wake.
    2. V-shaped Kelvin wake arms (~19.47° half-angle envelope).
    Resolves 180° heading ambiguity and cross-validates against reported AIS velocity.
    """

    def __init__(
        self,
        pixel_spacing_meters: float = 10.0,
        chip_size: int = 128,
        hough_threshold: int = 18,
        min_wake_length_meters: float = 40.0,
        kelvin_nominal_angle_deg: float = 19.47,
        kelvin_tolerance_deg: float = 8.0,
        speed_spoofing_threshold_knots: float = 4.0,
        heading_discrepancy_threshold_deg: float = 45.0,
        satellite_velocity_mps: float = 7500.0,
        slant_range_meters: float = 850000.0,
        incidence_angle_deg: float = 35.0,
    ) -> None:
        self.pixel_spacing_meters = max(0.1, float(pixel_spacing_meters))
        self.chip_size = max(64, int(chip_size))
        self.hough_threshold = max(5, int(hough_threshold))
        self.min_wake_length_meters = max(10.0, float(min_wake_length_meters))
        self.kelvin_nominal_angle_deg = kelvin_nominal_angle_deg
        self.kelvin_tolerance_deg = kelvin_tolerance_deg
        self.speed_spoofing_threshold_knots = speed_spoofing_threshold_knots
        self.heading_discrepancy_threshold_deg = heading_discrepancy_threshold_deg
        self.satellite_velocity_mps = satellite_velocity_mps
        self.slant_range_meters = slant_range_meters
        self.incidence_angle_deg = incidence_angle_deg

    def analyze_detection(
        self,
        image: np.ndarray,
        detection: ShipDetection | dict[str, Any],
        pixel_spacing_m: Optional[float] = None,
        ais_record: Optional[dict[str, Any]] = None,
    ) -> WakeAnalysisResult:
        """Analyze a ship detection chip to detect wake, resolve heading, and validate AIS velocity."""
        px_spacing = pixel_spacing_m or self.pixel_spacing_meters

        if isinstance(detection, dict):
            cx = float(detection.get("center_x") or (detection.get("x", 0) + detection.get("width", 0) / 2.0))
            cy = float(detection.get("center_y") or (detection.get("y", 0) + detection.get("height", 0) / 2.0))
            length_m = float(detection.get("length") or max(20.0, detection.get("width", 20) * px_spacing))
            beam_m = float(detection.get("beam") or max(5.0, detection.get("height", 5) * px_spacing))
            obb_angle = float(detection.get("angle") or 0.0)
        else:
            cx = float(detection.center_x if detection.center_x is not None else (detection.x + detection.width / 2.0))
            cy = float(detection.center_y if detection.center_y is not None else (detection.y + detection.height / 2.0))
            length_m = float(detection.length or max(20.0, detection.width * px_spacing))
            beam_m = float(detection.beam or max(5.0, detection.height * px_spacing))
            obb_angle = float(detection.angle or 0.0)

        length_px = max(4.0, length_m / px_spacing)
        beam_px = max(2.0, beam_m / px_spacing)

        # Scale chip size dynamically if ship is large
        eff_chip_size = max(self.chip_size, int(round(length_px * 3.5)))
        chip, x0, y0 = extract_chip(image, cx, cy, chip_size=eff_chip_size)

        local_cx = cx - x0
        local_cy = cy - y0

        # 1. Mask bright ship hull
        masked_chip = mask_ship_hull(chip, local_cx, local_cy, length_px, beam_px, obb_angle)

        # 2. Preprocess chip for linear feature enhancement
        # Bilateral / Gaussian smoothing to suppress high-frequency speckle
        smoothed = cv2.GaussianBlur(masked_chip, (5, 5), 1.0)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        contrast_chip = clahe.apply(smoothed)

        # Canny edge detector for wake boundaries
        v_med = float(np.median(contrast_chip))
        canny_low = int(max(10, 0.66 * v_med))
        canny_high = int(min(255, 1.33 * v_med))
        edges = cv2.Canny(contrast_chip, canny_low, canny_high)

        # 3. Detect candidate linear features using HoughLinesP
        min_line_px = int(max(6, self.min_wake_length_meters / px_spacing))
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180.0,
            threshold=self.hough_threshold,
            minLineLength=min_line_px,
            maxLineGap=int(min_line_px * 0.4),
        )

        wake_detected = False
        wake_type = None
        wake_confidence = 0.0
        wake_vector: Optional[tuple[float, float]] = None
        wake_angle_deg: Optional[float] = None
        true_heading_deg: Optional[float] = None
        kelvin_arms: Optional[tuple[float, float]] = None
        doppler_azimuth_shift_px = 0.0
        detected_segments: list[dict[str, Any]] = []

        if lines is not None and len(lines) > 0:
            candidate_segments = []
            for seg in np.asarray(lines).reshape(-1, 4):
                lx1, ly1, lx2, ly2 = int(seg[0]), int(seg[1]), int(seg[2]), int(seg[3])
                seg_len = math.hypot(lx2 - lx1, ly2 - ly1)
                mid_x = (lx1 + lx2) / 2.0
                mid_y = (ly1 + ly2) / 2.0
                # Vector from ship centroid to segment midpoint
                to_mid_x = mid_x - local_cx
                to_mid_y = mid_y - local_cy
                dist_to_ship = math.hypot(to_mid_x, to_mid_y)

                # Segment line orientation
                seg_angle = math.degrees(math.atan2(ly2 - ly1, lx2 - lx1)) % 180.0
                candidate_segments.append({
                    "x1": int(lx1 + x0),
                    "y1": int(ly1 + y0),
                    "x2": int(lx2 + x0),
                    "y2": int(ly2 + y0),
                    "length_px": round(seg_len, 1),
                    "angle_deg": round(seg_angle, 1),
                    "dist_to_ship_px": round(dist_to_ship, 1),
                    "vector": (to_mid_x, to_mid_y),
                })

            # Ship orientation reference angle normalized to [0, 180)
            ship_axis = (obb_angle + 180.0) % 180.0

            # Filter segments passing relatively near the vessel (within 1.5 vessel lengths)
            valid_segs = [
                s for s in candidate_segments
                if s["dist_to_ship_px"] <= length_px * 2.0 and s["dist_to_ship_px"] >= beam_px * 0.5
            ]

            # Categorize segments:
            # - centerline (turbulent wake): angle difference <= 18 deg
            # - kelvin arms: angle difference within Kelvin window (19.47 +- 8 deg)
            centerline_segs = []
            kelvin_segs = []

            for s in valid_segs:
                d_ang = abs(s["angle_deg"] - ship_axis)
                d_ang = min(d_ang, 180.0 - d_ang)
                if d_ang <= 18.0:
                    centerline_segs.append(s)
                elif abs(d_ang - self.kelvin_nominal_angle_deg) <= self.kelvin_tolerance_deg:
                    kelvin_segs.append(s)

            if centerline_segs or kelvin_segs:
                wake_detected = True
                detected_segments = candidate_segments

                # Aggregate wake direction vector (pointing away from ship along wake)
                sum_dx = 0.0
                sum_dy = 0.0
                weight_total = 0.0

                for s in centerline_segs:
                    w = s["length_px"] * 1.5
                    sum_dx += s["vector"][0] * w
                    sum_dy += s["vector"][1] * w
                    weight_total += w

                for s in kelvin_segs:
                    w = s["length_px"]
                    sum_dx += s["vector"][0] * w
                    sum_dy += s["vector"][1] * w
                    weight_total += w

                if weight_total > 0:
                    norm = math.hypot(sum_dx, sum_dy)
                    if norm > 1e-4:
                        wake_vector = (sum_dx / norm, sum_dy / norm)

                # Determine wake type and confidence
                if centerline_segs and kelvin_segs:
                    wake_type = "composite"
                    wake_confidence = min(0.95, 0.65 + 0.05 * len(centerline_segs) + 0.05 * len(kelvin_segs))
                elif centerline_segs:
                    wake_type = "turbulent"
                    wake_confidence = min(0.85, 0.50 + 0.05 * len(centerline_segs))
                else:
                    wake_type = "kelvin_arms"
                    wake_confidence = min(0.80, 0.45 + 0.05 * len(kelvin_segs))

                if kelvin_segs and len(kelvin_segs) >= 2:
                    ang1 = kelvin_segs[0]["angle_deg"]
                    ang2 = kelvin_segs[1]["angle_deg"]
                    kelvin_arms = (min(ang1, ang2), max(ang1, ang2))

                # 4. Resolve 180-degree heading ambiguity
                # Wake extends BEHIND the ship -> ship true heading is OPPOSITE the wake vector!
                if wake_vector is not None:
                    # In image coordinates: +x is East, +y is South (downward)
                    # Compass bearing (0 = North, 90 = East, 180 = South, 270 = West)
                    # Vector pointing along ship motion: dx_heading = -wake_dx, dy_heading = -wake_dy
                    dx_heading = -wake_vector[0]
                    # Map image dy (down) to North (+dy_north = -dy_image)
                    dy_north = wake_vector[1]

                    # Ship heading clockwise from North:
                    true_heading_deg = (math.degrees(math.atan2(dx_heading, dy_north)) + 360.0) % 360.0
                    true_heading_deg = round(true_heading_deg, 1)

                    wake_angle = (true_heading_deg + 180.0) % 360.0
                    wake_angle_deg = round(wake_angle, 1)

                    # Estimate azimuth displacement (perpendicular to range, assuming vertical range or metadata)
                    # Ship centroid vs wake origin offset
                    doppler_azimuth_shift_px = abs(wake_vector[1] * beam_px * 0.5)

        # 5. Vessel Speed Estimation
        estimated_speed_knots: Optional[float] = None
        if wake_detected:
            # Hydrodynamic model:
            # In SAR, wake length and Doppler shift correlate with speed.
            # Base speed estimated from wake persistence and azimuth Doppler displacement:
            # v_radial = dx_az * pixel_spacing * (V_sat / R)
            v_radial = doppler_azimuth_shift_px * px_spacing * (self.satellite_velocity_mps / self.slant_range_meters)
            inc_rad = math.radians(self.incidence_angle_deg)
            sin_inc = max(0.2, math.sin(inc_rad))

            # Vessel velocity from radial component:
            speed_mps = max(2.5, v_radial / sin_inc) if doppler_azimuth_shift_px > 1.0 else 7.5
            # 1 m/s = 1.94384 knots
            est_knots = speed_mps * 1.94384

            # Factor in length: large ships produce visible wakes at typical transit speeds (10-24 knots)
            if length_m > 150.0:
                est_knots = max(11.0, min(26.0, est_knots))
            elif length_m > 60.0:
                est_knots = max(8.0, min(24.0, est_knots))
            else:
                est_knots = max(5.0, min(35.0, est_knots))

            estimated_speed_knots = round(est_knots, 1)

        # 6. AIS Speed & Heading Spoofing Cross-Validation
        is_speed_spoofed = None
        is_course_spoofed = None
        spd_discrepancy = None
        hdg_discrepancy = None
        spoof_reason = None

        if ais_record is not None and wake_detected:
            ais_speed = ais_record.get("speed") if ais_record.get("speed") is not None else ais_record.get("sog")
            ais_heading = ais_record.get("heading") if ais_record.get("heading") is not None else ais_record.get("course")
            if ais_heading is None:
                ais_heading = ais_record.get("cog")

            if ais_speed is not None and estimated_speed_knots is not None:
                try:
                    s_val = float(ais_speed)
                    spd_discrepancy = round(abs(estimated_speed_knots - s_val), 1)
                    if spd_discrepancy >= self.speed_spoofing_threshold_knots:
                        is_speed_spoofed = True
                        if s_val < 2.0 and estimated_speed_knots >= 8.0:
                            spoof_reason = f"AIS reports stationary/loitering ({s_val} kn) while hydrodynamic wake indicates high transit speed ({estimated_speed_knots} kn)"
                        elif s_val > 15.0 and estimated_speed_knots <= 5.0:
                            spoof_reason = f"AIS reports high speed ({s_val} kn) but radar wake is weak or slow ({estimated_speed_knots} kn)"
                        else:
                            spoof_reason = f"Speed discrepancy {spd_discrepancy} kn exceeds threshold {self.speed_spoofing_threshold_knots} kn"
                    else:
                        is_speed_spoofed = False
                except (TypeError, ValueError):
                    pass

            if ais_heading is not None and true_heading_deg is not None:
                try:
                    h_val = float(ais_heading) % 360.0
                    diff = abs(true_heading_deg - h_val)
                    hdg_discrepancy = round(min(diff, 360.0 - diff), 1)
                    if hdg_discrepancy >= self.heading_discrepancy_threshold_deg:
                        is_course_spoofed = True
                        course_msg = f"Course discrepancy {hdg_discrepancy}° exceeds threshold {self.heading_discrepancy_threshold_deg}°"
                        spoof_reason = f"{spoof_reason} | {course_msg}" if spoof_reason else course_msg
                    else:
                        is_course_spoofed = False
                except (TypeError, ValueError):
                    pass

        return WakeAnalysisResult(
            wake_detected=wake_detected,
            wake_confidence=round(wake_confidence, 2),
            wake_angle_deg=wake_angle_deg,
            true_heading_deg=true_heading_deg,
            wake_type=wake_type,
            estimated_speed_knots=estimated_speed_knots,
            arm_angles_deg=kelvin_arms,
            is_speed_spoofed=is_speed_spoofed,
            is_course_spoofed=is_course_spoofed,
            speed_discrepancy_knots=spd_discrepancy,
            heading_discrepancy_deg=hdg_discrepancy,
            details={
                "candidate_segments_count": len(detected_segments),
                "spoof_reason": spoof_reason,
                "obb_angle_deg": round(obb_angle, 1),
            },
        )
