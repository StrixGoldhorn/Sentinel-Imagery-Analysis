"""Errors belonging to domain construction and validation."""


class SentinelAnalysisError(Exception):
    """Base class for expected application errors."""


class DomainValidationError(SentinelAnalysisError, ValueError):
    """Raised when an entity or value object would be created in an invalid state."""
