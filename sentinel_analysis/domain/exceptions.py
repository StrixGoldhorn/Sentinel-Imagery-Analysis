"""Application-level exceptions that adapters translate into."""


class SentinelAnalysisError(Exception):
    """Base class for expected application errors."""


class AuthenticationError(SentinelAnalysisError):
    pass


class ExternalServiceError(SentinelAnalysisError):
    pass


class NoImageryFoundError(SentinelAnalysisError):
    pass


class ScanNotFoundError(SentinelAnalysisError):
    pass

