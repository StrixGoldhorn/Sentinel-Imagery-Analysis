"""Expected errors shared by the inner application layers."""


class SentinelAnalysisError(Exception):
    """Base class for expected application errors."""


class DomainValidationError(SentinelAnalysisError, ValueError):
    """Raised when an entity or value object would be created in an invalid state."""


class AuthenticationError(SentinelAnalysisError):
    """Raised when an external service rejects or cannot obtain credentials."""


class ExternalServiceError(SentinelAnalysisError):
    """Raised when an external dependency fails or returns unusable data."""


class NoImageryFoundError(SentinelAnalysisError):
    """Raised when no imagery acquisition covers the requested search."""


class ScanNotFoundError(SentinelAnalysisError):
    """Raised when a requested scan does not exist."""
