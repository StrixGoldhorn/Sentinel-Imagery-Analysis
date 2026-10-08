"""Use cases for managing application and feature configuration."""

import re
from typing import Any

from sentinel_analysis.application.ports.settings_repository import SettingsRepository
from sentinel_analysis.domain.satellite import ALL_SATELLITE_NAMES


ENV_ONLY_SETTING_KEYS = {
    ("imagery", "copernicus_username"),
    ("imagery", "copernicus_password"),
    ("scheduler", "n2yo_api_key"),
    ("system", "port"),
    ("system", "debug"),
    ("system", "database_path"),
    ("system", "output_root"),
    ("system", "cache_root"),
}


class GetSettings:
    """Retrieve system settings organized by feature section."""

    def __init__(self, settings_repo: SettingsRepository) -> None:
        self._settings_repo = settings_repo

    def execute(
        self,
        section: str | None = None,
        include_definitions: bool = False,
        mask_secrets: bool = True,
    ) -> dict[str, Any]:
        if include_definitions and hasattr(self._settings_repo, "get_all_definitions"):
            return self._settings_repo.get_all_definitions(mask_secrets=mask_secrets)
        if section:
            return self._settings_repo.get_section(section)
        return self._settings_repo.get_all()


class UpdateSettings:
    """Validate and update system settings."""

    def __init__(self, settings_repo: SettingsRepository) -> None:
        self._settings_repo = settings_repo

    def execute(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Settings payload must be a JSON object")

        validated: dict[str, dict[str, Any]] = {}

        for section, key_values in payload.items():
            if not isinstance(key_values, dict):
                raise ValueError(f"Section '{section}' must contain a key-value mapping")

            validated[section] = {}
            for key, val in key_values.items():
                if (section, key) in ENV_ONLY_SETTING_KEYS:
                    continue
                validated_val = self._validate_field(section, key, val)
                validated[section][key] = validated_val

        self._settings_repo.update_bulk(validated)

    def _validate_field(self, section: str, key: str, value: Any) -> Any:
        # Numeric / range validations
        if key == "coastal_buffer_pixels":
            try:
                ival = int(value)
                if ival < 0:
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Coastal Noise Buffer must be a non-negative integer (pixels)")

        if key == "morph_close_kernel":
            try:
                ival = int(value)
                if ival < 1:
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Inland Gap Closing Kernel must be a positive integer")

        if key == "threshold":
            try:
                ival = int(value)
                if not (1 <= ival <= 255):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Detection threshold must be an integer between 1 and 255")

        if key in ("minimum_area", "maximum_area"):
            try:
                fval = float(value)
                if fval <= 0:
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a positive number")

        if key == "dilation_iterations":
            try:
                ival = int(value)
                if ival < 0:
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Dilation iterations must be a non-negative integer")

        if key == "pixel_spacing_meters":
            try:
                fval = float(value)
                if fval <= 0:
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Pixel spacing meters must be a positive number")

        if key == "detection_method":
            sval = str(value).lower()
            if sval not in ("threshold", "cfar_ca", "cfar_go", "cfar_so", "cfar_os"):
                raise ValueError("Detection method must be one of: threshold, cfar_ca, cfar_go, cfar_so, cfar_os")
            return sval

        if key == "cfar_guard_size":
            try:
                ival = int(value)
                if not (1 <= ival <= 31):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("CFAR Guard Window Size must be an integer between 1 and 31")

        if key == "cfar_train_size":
            try:
                ival = int(value)
                if not (3 <= ival <= 61):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("CFAR Background Window Size must be an integer between 3 and 61")

        if key == "cfar_factor":
            try:
                fval = float(value)
                if not (1.0 <= fval <= 15.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("CFAR Threshold Multiplier must be between 1.0 and 15.0")

        if key == "dual_pol_mode":
            sval = str(value).lower()
            if sval not in ("none", "ratio", "difference", "product", "enhanced"):
                raise ValueError("Dual-pol fusion mode must be one of: none, ratio, difference, product, enhanced")
            return sval

        if key == "detector_engine":
            sval = str(value).lower()
            if sval not in ("classical", "deep_learning"):
                raise ValueError("Detector engine must be one of: classical, deep_learning")
            return sval

        if key == "deep_learning_confidence":
            try:
                fval = float(value)
                if not (0.1 <= fval <= 1.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Deep learning confidence must be between 0.1 and 1.0")

        if key == "wake_speed_spoofing_threshold_knots":
            try:
                fval = float(value)
                if not (0.5 <= fval <= 20.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Wake speed spoofing threshold must be between 0.5 and 20.0 knots")

        if key == "ais_interpolation_method":
            sval = str(value).lower()
            if sval not in ("spline", "ekf", "linear"):
                raise ValueError("AIS interpolation method must be one of: spline, ekf, linear")
            return sval

        if key == "ais_covariance_confidence":
            try:
                fval = float(value)
                if not (0.50 <= fval <= 0.99):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("AIS covariance confidence must be between 0.50 and 0.99")

        if key == "dark_vessel_solas_length_threshold":
            try:
                fval = float(value)
                if not (10.0 <= fval <= 100.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Dark vessel SOLAS length threshold must be between 10.0 and 100.0 meters")

        if key == "dark_vessel_critical_length_threshold":
            try:
                fval = float(value)
                if not (50.0 <= fval <= 300.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Dark vessel critical length threshold must be between 50.0 and 300.0 meters")

        if key == "ais_correlation_distance_meters":
            try:
                fval = float(value)
                if not (0.0 <= fval <= 5000.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("AIS correlation distance must be a number between 0 and 5000 meters")

        if key == "ais_dead_reckoning_enabled":
            return bool(value)

        if key == "ais_max_dead_reckoning_seconds":
            try:
                ival = int(value)
                if not (60 <= ival <= 86400):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("AIS max dead reckoning seconds must be an integer between 60 and 86400")

        if key == "resolution_meters":
            try:
                fval = float(value)
                if fval <= 0:
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Resolution must be a positive number of meters")

        if key == "search_window_days":
            try:
                ival = int(value)
                if not (1 <= ival <= 365):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Acquisition Search Window must be an integer between 1 and 365 days")

        if key == "max_image_size":
            try:
                ival = int(value)
                if not (100 <= ival <= 10000):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Max Tile Image Size must be between 100 and 10000 pixels")

        if key in ("poll_interval_seconds", "sar_scan_interval_seconds"):
            try:
                fval = float(value)
                if fval < 1.0:
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be at least 1.0 second")

        if key == "aoi_check_interval_seconds":
            try:
                fval = float(value)
                if fval < 1.0:
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("AOI check interval must be at least 1.0 second")

        if key == "post_pass_max_wait_hours":
            try:
                fval = float(value)
                if not (1.0 <= fval <= 168.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Post-pass wait window must be between 1 and 168 hours")

        if key == "post_pass_worker_count":
            try:
                ival = int(value)
                if not (1 <= ival <= 16):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Post-pass worker count must be an integer between 1 and 16")

        if key == "default_zoom":
            try:
                ival = int(value)
                if not (1 <= ival <= 22):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Default Map Zoom must be between 1 and 22")

        if key == "sar_opacity":
            try:
                fval = float(value)
                if not (0.0 <= fval <= 1.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("SAR Opacity must be between 0.0 and 1.0")

        if key == "default_lat":
            try:
                fval = float(value)
                if not (-90.0 <= fval <= 90.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Default Latitude must be between -90 and 90 degrees")

        if key == "default_lng":
            try:
                fval = float(value)
                if not (-180.0 <= fval <= 180.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Default Longitude must be between -180 and 180 degrees")

        if key in (
            "dem_land_mask_enabled",
            "auto_capture_default",
            "debug",
            "enabled",
            "show_info",
            "show_success",
            "show_warning",
            "show_error",
            "ais_overlay_default_enabled",
            "nautical_chart_default_enabled",
            "wake_detection_enabled",
            "umbra_enabled",
            "asf_enabled",
            "optical_validation_enabled",
            "scheduler_leader_election_enabled",
        ):
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)

        if key == "umbra_stac_api_url":
            sval = str(value).strip()
            if not sval.startswith(("http://", "https://")):
                raise ValueError("Umbra STAC catalog URL must start with http:// or https://")
            return sval

        if key == "umbra_max_results":
            try:
                ival = int(value)
                if not (1 <= ival <= 100):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Umbra max results must be an integer between 1 and 100")

        if key == "asf_api_url":
            sval = str(value).strip()
            if not sval.startswith(("http://", "https://")):
                raise ValueError("NASA ASF API endpoint must start with http:// or https://")
            return sval

        if key == "asf_flight_direction":
            sval = str(value).upper()
            if sval not in ("BOTH", "ASCENDING", "DESCENDING"):
                raise ValueError("ASF flight direction must be one of: BOTH, ASCENDING, DESCENDING")
            return sval

        if key == "asf_polarization":
            sval = str(value).upper()
            if sval not in ("VV+VH", "HH+HV", "VV", "HH", "ALL"):
                raise ValueError("ASF polarization must be one of: VV+VH, HH+HV, VV, HH, ALL")
            return sval

        if key == "asf_max_results":
            try:
                ival = int(value)
                if not (1 <= ival <= 250):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("ASF max results must be an integer between 1 and 250")

        if key == "optical_max_cloud_cover":
            try:
                fval = float(value)
                if not (1.0 <= fval <= 100.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Optical max cloud cover must be between 1.0 and 100.0 percent")

        if key == "optical_time_window_hours":
            try:
                fval = float(value)
                if not (1.0 <= fval <= 72.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Optical match window must be between 1.0 and 72.0 hours")

        if key == "scheduler_leader_backend":
            sval = str(value).lower()
            if sval not in ("sqlite", "redis", "postgres"):
                raise ValueError("Leader election backend must be one of: sqlite, redis, postgres")
            return sval

        if key == "scheduler_heartbeat_interval_seconds":
            try:
                fval = float(value)
                if not (1.0 <= fval <= 60.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Scheduler heartbeat interval must be between 1.0 and 60.0 seconds")

        if key == "scheduler_lease_ttl_seconds":
            try:
                fval = float(value)
                if not (5.0 <= fval <= 120.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Scheduler lease TTL must be between 5.0 and 120.0 seconds")

        if key == "duration_seconds":
            try:
                fval = float(value)
                if not (1.0 <= fval <= 30.0):
                    raise ValueError
                return fval
            except (TypeError, ValueError):
                raise ValueError("Notification duration must be between 1 and 30 seconds")

        if key == "port":
            try:
                ival = int(value)
                if not (1 <= ival <= 65535):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Port must be between 1 and 65535")

        if key == "filter_type":
            sval = str(value).lower()
            if sval not in ("none", "lee", "frost"):
                raise ValueError("Filter type must be one of: none, lee, frost")
            return sval

        if key == "default_evalscript":
            sval = str(value).upper()
            if sval not in ("SAR", "SAR_DUAL_POL"):
                raise ValueError("Default Evalscript must be one of: SAR, SAR_DUAL_POL")
            return sval

        if key == "enabled_satellites":
            if isinstance(value, str):
                sats = [s.strip() for s in value.split(",") if s.strip()]
            elif isinstance(value, (list, tuple, set)):
                sats = [str(s).strip() for s in value if str(s).strip()]
            else:
                sats = []
            valid_sats = set(ALL_SATELLITE_NAMES)
            invalid = [s for s in sats if s not in valid_sats]
            if invalid:
                raise ValueError(f"Unknown satellite(s): {', '.join(invalid)}. Supported: {', '.join(ALL_SATELLITE_NAMES)}")
            return sats

        if key in ("color_cv_detection", "color_obb_detection", "color_inside_box_detection", "color_outside_box_detection", "color_dark_vessel"):
            sval = str(value).strip()
            if not re.match(r"^#[0-9a-fA-F]{6}$", sval):
                raise ValueError(f"{key} must be a valid 6-character hex color (e.g. #ff3333)")
            return sval

        if isinstance(value, str):
            return value.strip()

        return value


class ResetSettings:
    """Reset configuration back to default values."""

    def __init__(self, settings_repo: SettingsRepository) -> None:
        self._settings_repo = settings_repo

    def execute(self, section: str | None = None) -> None:
        self._settings_repo.reset_section(section)
