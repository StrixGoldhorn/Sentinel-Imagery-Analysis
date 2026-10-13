"""Port definitions for provider health and quota persistence."""

from __future__ import annotations

from typing import Optional, Protocol

from sentinel_analysis.domain.provider_health import (
    ProviderHealthRecord,
    ProviderQuotaRecord,
)


class ProviderHealthRepository(Protocol):
    """Persistence interface for provider health telemetry and operational quotas."""

    def save_health(self, record: ProviderHealthRecord) -> None:
        ...

    def get_health(self, provider_id: str) -> Optional[ProviderHealthRecord]:
        ...

    def list_health(self) -> list[ProviderHealthRecord]:
        ...

    def save_quota(self, record: ProviderQuotaRecord) -> None:
        ...

    def get_quota(self, provider_id: str) -> Optional[ProviderQuotaRecord]:
        ...

    def list_quotas(self) -> list[ProviderQuotaRecord]:
        ...

    def record_quota_consumption(
        self,
        provider_id: str,
        units: int = 1,
        cost_usd: float = 0.0,
    ) -> ProviderQuotaRecord:
        ...

    def record_health_event(
        self,
        provider_id: str,
        success: bool,
        latency_ms: float = 0.0,
        error_message: Optional[str] = None,
    ) -> ProviderHealthRecord:
        ...
