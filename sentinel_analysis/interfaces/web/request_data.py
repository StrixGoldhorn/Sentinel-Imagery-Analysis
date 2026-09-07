from datetime import datetime, timezone
from typing import Any

from flask import request
from werkzeug.utils import secure_filename

from sentinel_analysis.domain.entities import BoundingBox


class RequestValidationError(ValueError):
    """Raised when an HTTP request does not satisfy the endpoint contract."""


def json_object() -> dict[str, Any]:
    payload = request.get_json(silent=False)
    if not isinstance(payload, dict):
        raise RequestValidationError("JSON request body must be an object")
    return payload


def required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(f"{field} must be a non-empty string")
    return value.strip()


def optional_string(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestValidationError(f"{field} must be a string or null")
    return value.strip() or None


def integer(payload: dict[str, Any], field: str, default: int) -> int:
    value = payload.get(field, default)
    if isinstance(value, bool):
        raise RequestValidationError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise RequestValidationError(f"{field} must be an integer") from exc
    raise RequestValidationError(f"{field} must be an integer")


def bounding_box(payload: dict[str, Any], field: str = "bbox") -> BoundingBox:
    value = payload.get(field)
    if not isinstance(value, (list, tuple)):
        raise RequestValidationError(f"{field} must be an array of four coordinates")
    return BoundingBox.from_sequence(value)


def boolean(payload: dict[str, Any], field: str, default: bool = False) -> bool:
    value = payload.get(field, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise RequestValidationError(f"{field} must be a boolean")


def safe_folder_name(folder_name: str) -> str:
    if (
        not folder_name
        or folder_name in {".", ".."}
        or secure_filename(folder_name) != folder_name
    ):
        raise RequestValidationError("Invalid scan folder name")
    return folder_name


def optional_datetime(
    payload: dict[str, Any],
    field: str,
    default: datetime | None = None,
    is_end_of_day: bool = False,
    time_field: str | None = None,
) -> datetime | None:
    value = payload.get(field)
    time_val = payload.get(time_field) if time_field else None

    if value is None and time_val is None:
        return default
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return default

    val_str = value.strip()
    if time_val and isinstance(time_val, str) and time_val.strip():
        t_str = time_val.strip()
        if "T" not in val_str and " " not in val_str:
            val_str = f"{val_str}T{t_str}"

    try:
        if len(val_str) == 10 and val_str.count("-") == 2:
            d = datetime.strptime(val_str, "%Y-%m-%d")
            if is_end_of_day:
                return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
            return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)

        normalized = val_str.replace("Z", "+00:00")
        if " " in normalized and "T" not in normalized:
            normalized = normalized.replace(" ", "T")
        dt = datetime.fromisoformat(normalized)
        if dt.utcoffset() is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as exc:
        raise RequestValidationError(f"Invalid datetime format for field '{field}': {val_str}") from exc


