"""Domain entities and value objects for satellite provider health and quota tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sentinel_analysis.domain.exceptions import DomainValidationError


class ProviderStatus(str, Enum):
    """Operational health status of a satellite/geospatial data provider."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    MAINTENANCE = "MAINTENANCE"


@dataclass(frozen=True)
class ProviderHealthRecord:
    """Telemetry and health status for an external provider."""

    provider_id: str
    name: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    latency_ms: float = 0.0
    error_rate_pct: float = 0.0
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    consecutive_failures: int = 0
    status_message: str = "Operational"
    endpoint_url: Optional[str] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_id.strip():
            raise DomainValidationError("Provider ID must not be empty")
        if not self.name or not self.name.strip():
            raise DomainValidationError("Provider name must not be empty")
        if self.latency_ms < 0:
            raise DomainValidationError("Latency must be non-negative")
        if not (0.0 <= self.error_rate_pct <= 100.0):
            raise DomainValidationError("Error rate percentage must be between 0 and 100")
        if self.consecutive_failures < 0:
            raise DomainValidationError("Consecutive failures must be non-negative")

    @property
    def is_available(self) -> bool:
        """Indicates whether provider can be tasked or queried."""
        return self.status in (ProviderStatus.HEALTHY, ProviderStatus.DEGRADED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 1),
            "error_rate_pct": round(self.error_rate_pct, 1),
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "consecutive_failures": self.consecutive_failures,
            "status_message": self.status_message,
            "endpoint_url": self.endpoint_url,
            "is_available": self.is_available,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class ProviderQuotaRecord:
    """Operational rate limits, usage quotas, and commercial credit balances."""

    provider_id: str
    daily_request_limit: int = 1000
    daily_requests_used: int = 0
    monthly_request_limit: Optional[int] = None
    monthly_requests_used: int = 0
    rate_limit_per_minute: int = 60
    credit_balance_usd: Optional[float] = None
    reset_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_id.strip():
            raise DomainValidationError("Provider ID must not be empty")
        if self.daily_request_limit < 0 or self.daily_requests_used < 0:
            raise DomainValidationError("Daily limits and usage must be non-negative")
        if self.monthly_request_limit is not None and self.monthly_request_limit < 0:
            raise DomainValidationError("Monthly limit must be non-negative")
        if self.rate_limit_per_minute < 0:
            raise DomainValidationError("Rate limit per minute must be non-negative")
        if self.credit_balance_usd is not None and self.credit_balance_usd < 0:
            raise DomainValidationError("Credit balance cannot be negative")

    @property
    def daily_requests_remaining(self) -> int:
        return max(0, self.daily_request_limit - self.daily_requests_used)

    @property
    def monthly_requests_remaining(self) -> Optional[int]:
        if self.monthly_request_limit is None:
            return None
        return max(0, self.monthly_request_limit - self.monthly_requests_used)

    @property
    def has_quota_available(self) -> bool:
        if self.daily_requests_remaining <= 0:
            return False
        if self.monthly_request_limit is not None and (self.monthly_requests_remaining or 0) <= 0:
            return False
        if self.credit_balance_usd is not None and self.credit_balance_usd <= 0.0:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "daily_request_limit": self.daily_request_limit,
            "daily_requests_used": self.daily_requests_used,
            "daily_requests_remaining": self.daily_requests_remaining,
            "monthly_request_limit": self.monthly_request_limit,
            "monthly_requests_used": self.monthly_requests_used,
            "monthly_requests_remaining": self.monthly_requests_remaining,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "credit_balance_usd": self.credit_balance_usd,
            "has_quota_available": self.has_quota_available,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class ProviderDashboardSummary:
    """Aggregated status across all integrated satellite providers."""

    total_providers: int
    healthy_count: int
    degraded_count: int
    down_count: int
    overall_status: ProviderStatus
    providers: list[dict[str, Any]]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_providers": self.total_providers,
            "healthy_count": self.healthy_count,
            "degraded_count": self.degraded_count,
            "down_count": self.down_count,
            "overall_status": self.overall_status.value,
            "providers": self.providers,
            "timestamp": self.timestamp.isoformat(),
        }
