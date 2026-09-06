"""Use cases for managing application and feature configuration."""

from typing import Any

from sentinel_analysis.application.ports.settings_repository import SettingsRepository


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

        if key == "dem_land_mask_enabled" or key == "auto_capture_default" or key == "debug":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)

        if key == "port":
            try:
                ival = int(value)
                if not (1 <= ival <= 65535):
                    raise ValueError
                return ival
            except (TypeError, ValueError):
                raise ValueError("Port must be between 1 and 65535")

        return value


class ResetSettings:
    """Reset configuration back to default values."""

    def __init__(self, settings_repo: SettingsRepository) -> None:
        self._settings_repo = settings_repo

    def execute(self, section: str | None = None) -> None:
        self._settings_repo.reset_section(section)
