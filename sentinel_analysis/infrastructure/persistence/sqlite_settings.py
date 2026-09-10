"""SQLite implementation of the settings repository."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


# These values belong to the process environment and must never be exposed as
# editable, database-backed application settings.
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

DEFAULT_SETTINGS_DEFINITIONS: dict[str, dict[str, dict[str, Any]]] = {
    "cv": {
        "coastal_buffer_pixels": {
            "value": 81,
            "type": "integer",
            "label": "Coastal Noise Buffer (Pixels)",
            "description": "Dilation kernel buffer radius in pixels (~800m) applied to DEM land mask to eliminate breaking surf, docks, and coastal noise.",
            "min": 0,
            "max": 500,
        },
        "morph_close_kernel": {
            "value": 27,
            "type": "integer",
            "label": "Inland Gap Closing Kernel (Pixels)",
            "description": "Kernel size for closing morphological gaps (rivers, bays, lakes) in landmasses.",
            "min": 3,
            "max": 101,
        },
        "dem_land_mask_enabled": {
            "value": True,
            "type": "boolean",
            "label": "Enable DEM Land Masking",
            "description": "Automatically mask out land and coastal areas using Copernicus DEM elevation data.",
        },
        "threshold": {
            "value": 40,
            "type": "integer",
            "label": "Detection Brightness Threshold",
            "description": "SAR backscatter pixel brightness threshold (0-255) for vessel hull extraction.",
            "min": 1,
            "max": 255,
        },
        "filter_type": {
            "value": "none",
            "type": "select",
            "options": ["none", "lee", "frost"],
            "label": "Speckle Denoising Filter",
            "description": "Preprocessing radar speckle filter applied over open sea.",
        },
        "minimum_area": {
            "value": 50.0,
            "type": "number",
            "label": "Minimum Vessel Area (Pixels)",
            "description": "Exclude detection contours smaller than this pixel area threshold.",
            "min": 1.0,
        },
        "maximum_area": {
            "value": 5000.0,
            "type": "number",
            "label": "Maximum Vessel Area (Pixels)",
            "description": "Exclude detection contours larger than this pixel area threshold.",
            "min": 100.0,
        },
        "dilation_iterations": {
            "value": 2,
            "type": "integer",
            "label": "Blob Dilation Iterations",
            "description": "Morphological iterations to merge fractured radar returns from a single vessel.",
            "min": 0,
            "max": 10,
        },
        "pixel_spacing_meters": {
            "value": 10.0,
            "type": "number",
            "label": "Ground Pixel Spacing (Meters)",
            "description": "Physical ground resolution in meters per pixel for vessel dimension calculations.",
            "min": 1.0,
        },
    },
    "imagery": {
        "default_evalscript": {
            "value": "SAR",
            "type": "select",
            "options": ["SAR", "SAR_DUAL_POL"],
            "label": "Default SAR Evalscript",
            "description": "Copernicus Sentinel Hub Process API evalscript for SAR visualization.",
        },
        "resolution_meters": {
            "value": 10.0,
            "type": "number",
            "label": "Tile Grid Resolution (Meters)",
            "description": "Spatial resolution used for tile grid calculations.",
            "min": 5.0,
        },
        "max_image_size": {
            "value": 2500,
            "type": "integer",
            "label": "Max Tile Image Size (Pixels)",
            "description": "Maximum width/height of individual tiles fetched from Process API.",
            "min": 500,
            "max": 2500,
        },
        "search_window_days": {
            "value": 30,
            "type": "integer",
            "label": "Acquisition Search Window (Days)",
            "description": "Maximum days to look back when searching for recent Sentinel-1 passes.",
            "min": 1,
            "max": 365,
        },
    },
    "scheduler": {
        "aoi_check_interval_seconds": {
            "value": 30.0,
            "type": "number",
            "label": "AOI Scan Check Interval (Seconds)",
            "description": "Frequency in seconds at which the background worker checks if any AOI has an active flypast or needs scanning (default: 30s).",
            "min": 5.0,
            "max": 3600.0,
        },
        "auto_capture_default": {
            "value": True,
            "type": "boolean",
            "label": "Auto-Capture by Default",
            "description": "Automatically enable automated scan capture when creating new AOIs.",
        },
        "satellite_norad_ids": {
            "value": "39634, 41456, 62232, 66315",
            "type": "string",
            "label": "Sentinel-1 NORAD IDs",
            "description": "Comma-separated NORAD tracking IDs for Sentinel-1A, 1B, 1C, 1D.",
        },
        "enabled_satellites": {
            "value": ["Sentinel-1A", "Sentinel-1C"],
            "type": "multiselect",
            "options": ["Sentinel-1A", "Sentinel-1B", "Sentinel-1C", "Sentinel-1D"],
            "label": "Monitored Satellites",
            "description": "Select which Sentinel-1 satellites to track, schedule passes for, and ingest imagery from.",
        },
    },
    "map_ui": {
        "default_lat": {
            "value": 1.290270,
            "type": "number",
            "label": "Default Map Latitude",
            "description": "Initial latitude when loading the map scan view.",
        },
        "default_lng": {
            "value": 103.851959,
            "type": "number",
            "label": "Default Map Longitude",
            "description": "Initial longitude when loading the map scan view.",
        },
        "default_zoom": {
            "value": 10,
            "type": "integer",
            "label": "Default Map Zoom",
            "description": "Initial zoom level for the map view.",
            "min": 2,
            "max": 18,
        },
        "sar_opacity": {
            "value": 1.0,
            "type": "number",
            "label": "Default SAR Opacity",
            "description": "Default transparency opacity for SAR radar overlays.",
            "min": 0.1,
            "max": 1.0,
        },
        "color_cv_detection": {
            "value": "#ff3333",
            "type": "color",
            "label": "Standard Bounding Box Color",
            "description": "Border color for standard AABB ship detections on the map.",
        },
        "color_obb_detection": {
            "value": "#e67e22",
            "type": "color",
            "label": "OBB Oriented Box Color",
            "description": "Border color for oriented bounding box ship detections with heading.",
        },
    },
    "notifications": {
        "enabled": {
            "value": True,
            "type": "boolean",
            "label": "Enable In-App Notifications",
            "description": "Show operational feedback messages in the dashboard.",
        },
        "duration_seconds": {
            "value": 3.0,
            "type": "number",
            "label": "Notification Display Duration (Seconds)",
            "description": "How long temporary notifications remain visible before dismissing automatically.",
            "min": 1.0,
            "max": 30.0,
        },
        "show_info": {
            "value": True,
            "type": "boolean",
            "label": "Informational Messages",
            "description": "Show routine progress and navigation messages.",
        },
        "show_success": {
            "value": True,
            "type": "boolean",
            "label": "Success Messages",
            "description": "Show completed action confirmations.",
        },
        "show_warning": {
            "value": True,
            "type": "boolean",
            "label": "Warning Messages",
            "description": "Show caution messages that do not prevent continued use.",
        },
        "show_error": {
            "value": True,
            "type": "boolean",
            "label": "Error Messages",
            "description": "Show failed-action and connection error messages.",
        },
    },
}


class SQLiteSettingsRepository:
    """SQLite implementation of the settings repository with feature-based partitioning."""

    def __init__(self, database_path: Path | str, timeout: float = 5) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        """Seed defaults into the database if not already present."""
        with self._database.connection(rows=True) as connection:
            existing = {
                row["key"]: row
                for row in connection.execute("SELECT key, section, value_json FROM system_settings").fetchall()
            }

            # Remove legacy environment-backed values if an older database has
            # them. They are owned by .env and are not application settings.
            for section, key in ENV_ONLY_SETTING_KEYS:
                connection.execute(
                    "DELETE FROM system_settings WHERE section = ? AND key = ?",
                    (section, key),
                )

            for section, keys in DEFAULT_SETTINGS_DEFINITIONS.items():
                for key, definition in keys.items():
                    if key not in existing:
                        default_val = definition["value"]
                        connection.execute(
                            """
                            INSERT INTO system_settings (key, section, value_json, description)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                key,
                                section,
                                json.dumps(default_val),
                                definition.get("description", ""),
                            ),
                        )

    def get(self, key: str, default: Any = None) -> Any:
        if any(env_key == key for _, env_key in ENV_ONLY_SETTING_KEYS):
            return default
        with self._database.connection(rows=True) as connection:
            row = connection.execute(
                "SELECT value_json FROM system_settings WHERE key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                return json.loads(row["value_json"])
            return default

    def get_section(self, section: str) -> dict[str, Any]:
        with self._database.connection(rows=True) as connection:
            rows = connection.execute(
                "SELECT key, value_json FROM system_settings WHERE section = ?",
                (section,),
            ).fetchall()
            return {
                row["key"]: json.loads(row["value_json"])
                for row in rows
                if (section, row["key"]) not in ENV_ONLY_SETTING_KEYS
            }

    def get_all(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        with self._database.connection(rows=True) as connection:
            rows = connection.execute(
                "SELECT section, key, value_json FROM system_settings ORDER BY section, key"
            ).fetchall()
            for row in rows:
                section = row["section"]
                if (section, row["key"]) in ENV_ONLY_SETTING_KEYS:
                    continue
                if section not in result:
                    result[section] = {}
                result[section][row["key"]] = json.loads(row["value_json"])
        return result

    def get_all_definitions(self, mask_secrets: bool = True) -> dict[str, dict[str, dict[str, Any]]]:
        """Return rich definitions with schema and stored values for UI rendering."""
        stored = self.get_all()
        definitions = deepcopy(DEFAULT_SETTINGS_DEFINITIONS)

        for section, keys in definitions.items():
            section_stored = stored.get(section, {})
            for key, field in keys.items():
                if key in section_stored:
                    val = section_stored[key]
                    if mask_secrets and field.get("secret") and val:
                        field["value"] = "********"
                        field["is_set"] = True
                    else:
                        field["value"] = val
                        field["is_set"] = bool(val)

        return definitions

    def set(self, section: str, key: str, value: Any, description: str | None = None) -> None:
        if (section, key) in ENV_ONLY_SETTING_KEYS:
            return
        with self._database.connection(rows=True) as connection:
            connection.execute(
                """
                INSERT INTO system_settings (key, section, value_json, description, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    section = excluded.section,
                    description = COALESCE(excluded.description, system_settings.description),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, section, json.dumps(value), description),
            )

    def update_bulk(self, settings: dict[str, dict[str, Any]]) -> None:
        with self._database.connection(rows=True) as connection:
            for section, key_values in settings.items():
                for key, value in key_values.items():
                    if (section, key) in ENV_ONLY_SETTING_KEYS:
                        continue
                    # If this is a secret and hasn't changed (passed as masked), don't overwrite
                    field_def = DEFAULT_SETTINGS_DEFINITIONS.get(section, {}).get(key, {})
                    if field_def.get("secret") and value == "********":
                        continue
                    connection.execute(
                        """
                        INSERT INTO system_settings (key, section, value_json, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(key) DO UPDATE SET
                            value_json = excluded.value_json,
                            section = excluded.section,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (key, section, json.dumps(value)),
                    )

    def reset_section(self, section: str | None = None) -> None:
        with self._database.connection(rows=True) as connection:
            if section:
                connection.execute("DELETE FROM system_settings WHERE section = ?", (section,))
            else:
                connection.execute("DELETE FROM system_settings")
        self._seed_defaults()
