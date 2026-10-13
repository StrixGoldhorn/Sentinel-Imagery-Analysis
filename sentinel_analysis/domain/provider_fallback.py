"""Domain models and value objects for automatic provider fallback and failover chains."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sentinel_analysis.domain.exceptions import DomainValidationError


class FallbackReason(str, Enum):
    """Reason why an external data provider was bypassed or failed over."""

    UNHEALTHY_STATUS = "UNHEALTHY_STATUS"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    TIMEOUT_OR_NETWORK_ERROR = "TIMEOUT_OR_NETWORK_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    EMPTY_OR_CORRUPT_RESPONSE = "EMPTY_OR_CORRUPT_RESPONSE"
    PRIMARY_SUCCEEDED = "PRIMARY_SUCCEEDED"


@dataclass(frozen=True)
class FallbackAttempt:
    """Record of an individual provider dispatch attempt during a fallback sequence."""

    provider_id: str
    attempted_at: datetime
    success: bool
    latency_ms: float = 0.0
    reason: Optional[FallbackReason] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "attempted_at": self.attempted_at.isoformat(),
            "success": self.success,
            "latency_ms": round(self.latency_ms, 1),
            "reason": self.reason.value if self.reason else None,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class FallbackResolution:
    """Outcome of a multi-provider fallback dispatch."""

    modality: str  # "SAR", "OPTICAL", "PASS_PREDICTION"
    primary_provider_id: str
    selected_provider_id: str
    fallback_occurred: bool
    attempts: list[FallbackAttempt] = field(default_factory=list)
    total_attempts: int = 1
    dispatched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_chain(self) -> list[str]:
        return [a.provider_id for a in self.attempts]

    def to_dict(self) -> dict[str, Any]:
        return {
            "modality": self.modality,
            "primary_provider_id": self.primary_provider_id,
            "selected_provider_id": self.selected_provider_id,
            "fallback_occurred": self.fallback_occurred,
            "attempts": [a.to_dict() for a in self.attempts],
            "total_attempts": len(self.attempts),
            "dispatched_at": self.dispatched_at.isoformat(),
            "metadata": self.metadata,
        }


class AllProvidersExhaustedError(DomainValidationError):
    """Raised when all candidates in a provider fallback chain fail or are unavailable."""

    def __init__(self, message: str, resolution: Optional[FallbackResolution] = None) -> None:
        super().__init__(message)
        self.resolution = resolution
