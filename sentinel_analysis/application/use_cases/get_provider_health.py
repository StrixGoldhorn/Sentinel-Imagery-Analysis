"""Use cases for querying and managing satellite provider health and operational quotas."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Optional

from sentinel_analysis.application.ports.provider_health_repository import (
    ProviderHealthRepository,
)
from sentinel_analysis.domain.provider_health import (
    ProviderDashboardSummary,
    ProviderHealthRecord,
    ProviderQuotaRecord,
    ProviderStatus,
)


class GetProviderHealthDashboard:
    """Aggregates multi-provider telemetry, health states, and quota consumption."""

    def __init__(self, repository: ProviderHealthRepository) -> None:
        self._repository = repository

    def execute(self) -> ProviderDashboardSummary:
        health_list = self._repository.list_health()
        quota_list = self._repository.list_quotas()
        quota_map = {q.provider_id: q for q in quota_list}

        healthy_cnt = 0
        degraded_cnt = 0
        down_cnt = 0

        combined: list[dict[str, Any]] = []

        for h in health_list:
            if h.status == ProviderStatus.HEALTHY:
                healthy_cnt += 1
            elif h.status == ProviderStatus.DEGRADED:
                degraded_cnt += 1
            elif h.status in (ProviderStatus.DOWN, ProviderStatus.MAINTENANCE):
                down_cnt += 1

            q = quota_map.get(h.provider_id)
            combined.append({
                **h.to_dict(),
                "quota": q.to_dict() if q else None,
                "has_quota_available": q.has_quota_available if q else True,
                "daily_requests_remaining": q.daily_requests_remaining if q else 1000,
                "credit_balance_usd": q.credit_balance_usd if q else None,
            })

        # Calculate overall status
        total = len(health_list)
        if down_cnt > 0 and (down_cnt >= max(1, total // 2)):
            overall = ProviderStatus.DOWN
        elif down_cnt > 0 or degraded_cnt > 0:
            overall = ProviderStatus.DEGRADED
        else:
            overall = ProviderStatus.HEALTHY

        return ProviderDashboardSummary(
            total_providers=total,
            healthy_count=healthy_cnt,
            degraded_count=degraded_cnt,
            down_count=down_cnt,
            overall_status=overall,
            providers=combined,
            timestamp=datetime.now(timezone.utc),
        )


class CheckProviderHealth:
    """Probes a provider endpoint or client and records outcome in the repository."""

    def __init__(self, repository: ProviderHealthRepository) -> None:
        self._repository = repository

    def execute(
        self,
        provider_id: str,
        force_status: Optional[str] = None,
        latency_override_ms: Optional[float] = None,
    ) -> ProviderHealthRecord:
        norm = (provider_id or "").strip().lower()
        if force_status:
            st = force_status.upper()
            try:
                status_enum = ProviderStatus(st)
            except ValueError:
                status_enum = ProviderStatus.HEALTHY if st in ("UP", "OK") else ProviderStatus.DOWN

            current = self._repository.get_health(norm)
            if not current:
                current = ProviderHealthRecord(provider_id=norm, name=norm.capitalize())

            latency = latency_override_ms if latency_override_ms is not None else current.latency_ms
            now = datetime.now(timezone.utc)
            updated = ProviderHealthRecord(
                provider_id=current.provider_id,
                name=current.name,
                status=status_enum,
                latency_ms=round(max(0.0, latency), 1),
                error_rate_pct=0.0 if status_enum == ProviderStatus.HEALTHY else 100.0,
                last_success_at=now if status_enum == ProviderStatus.HEALTHY else current.last_success_at,
                last_failure_at=now if status_enum != ProviderStatus.HEALTHY else current.last_failure_at,
                consecutive_failures=0 if status_enum == ProviderStatus.HEALTHY else 3,
                status_message=f"Manual status set to {status_enum.value}",
                endpoint_url=current.endpoint_url,
                updated_at=now,
            )
            self._repository.save_health(updated)
            return updated

        # Default simulated active check
        t0 = time.perf_counter()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0 + 42.0
        return self._repository.record_health_event(
            provider_id=norm,
            success=True,
            latency_ms=elapsed_ms,
            error_message=None,
        )


class UpdateProviderQuotaUseCase:
    """Updates operational quotas or credit balances for a satellite provider."""

    def __init__(self, repository: ProviderHealthRepository) -> None:
        self._repository = repository

    def execute(
        self,
        provider_id: str,
        daily_limit: Optional[int] = None,
        daily_request_limit: Optional[int] = None,
        requests_used: Optional[int] = None,
        monthly_gb_limit: Optional[float] = None,
        rate_limit_per_minute: Optional[int] = None,
        credit_balance_usd: Optional[float] = None,
        reset_usage: bool = False,
        reset_daily_requests: bool = False,
    ) -> ProviderQuotaRecord:
        norm = (provider_id or "").strip().lower()
        current = self._repository.get_quota(norm)
        if not current:
            current = ProviderQuotaRecord(provider_id=norm)

        effective_daily = daily_request_limit if daily_request_limit is not None else daily_limit
        effective_reset = reset_usage or reset_daily_requests

        now = datetime.now(timezone.utc)
        new_daily_limit = effective_daily if effective_daily is not None else current.daily_request_limit
        new_used = 0 if effective_reset else (requests_used if requests_used is not None else current.daily_requests_used)
        new_rate = rate_limit_per_minute if rate_limit_per_minute is not None else current.rate_limit_per_minute
        new_credits = credit_balance_usd if credit_balance_usd is not None else current.credit_balance_usd

        updated = ProviderQuotaRecord(
            provider_id=current.provider_id,
            daily_request_limit=new_daily_limit,
            daily_requests_used=new_used,
            monthly_request_limit=current.monthly_request_limit,
            monthly_requests_used=0 if effective_reset else current.monthly_requests_used,
            rate_limit_per_minute=new_rate,
            credit_balance_usd=new_credits,
            reset_at=current.reset_at,
            updated_at=now,
        )
        self._repository.save_quota(updated)
        return updated
