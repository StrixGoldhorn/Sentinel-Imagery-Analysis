"""Errors raised by application orchestration and business workflows."""

from sentinel_analysis.domain.exceptions import SentinelAnalysisError


class ApplicationError(SentinelAnalysisError):
    """Base class for expected use-case failures."""


class AreaOfInterestNotFoundError(ApplicationError, LookupError):
    """Raised when an area-of-interest command targets a missing record."""


class PluginNotFoundError(ApplicationError, ValueError):
    """Raised when AIS ingestion requests an unconfigured plugin."""


class InvalidPredictionError(ApplicationError, ValueError):
    """Raised when a pass provider violates its application contract."""
