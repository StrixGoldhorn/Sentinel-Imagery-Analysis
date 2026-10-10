"""Operational correlation ID tracing and context management.

Provides context-variable based propagation of correlation IDs across
asynchronous jobs, satellite passes, AOIs, imagery scans, and HTTP requests.
"""

from __future__ import annotations

from contextvars import ContextVar
import uuid
from typing import Optional

_correlation_context: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


def generate_correlation_id(prefix: str = "corr") -> str:
    """Generate a unique correlation ID with an optional prefix."""
    prefix_clean = (prefix or "corr").strip().rstrip("-")
    token = uuid.uuid4().hex[:12]
    return f"{prefix_clean}-{token}"


def get_current_correlation_id() -> Optional[str]:
    """Retrieve the correlation ID active in the current execution context."""
    return _correlation_context.get()


def set_current_correlation_id(correlation_id: Optional[str]) -> None:
    """Set the active correlation ID in the current execution context."""
    _correlation_context.set(correlation_id.strip() if correlation_id and correlation_id.strip() else None)


def ensure_correlation_id(provided_id: Optional[str] = None, prefix: str = "corr") -> str:
    """Return provided correlation ID, active context ID, or generate a fresh one."""
    if provided_id and str(provided_id).strip():
        return str(provided_id).strip()
    current = get_current_correlation_id()
    if current and current.strip():
        return current.strip()
    new_id = generate_correlation_id(prefix=prefix)
    set_current_correlation_id(new_id)
    return new_id
