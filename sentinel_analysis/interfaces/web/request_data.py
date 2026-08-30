"""Strict parsing helpers for the JSON HTTP boundary."""

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

