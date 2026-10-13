"""Use cases for automated multi-provider failover and acquisition dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.provider_health_repository import (
    ProviderHealthRepository,
)
from sentinel_analysis.domain.entities import BoundingBox, Scan
from sentinel_analysis.domain.provider_fallback import (
    AllProvidersExhaustedError,
    FallbackAttempt,
    FallbackReason,
    FallbackResolution,
)
from sentinel_analysis.domain.provider_health import ProviderStatus

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_CHAINS: dict[str, list[str]] = {
    "SAR": ["copernicus", "asf", "umbra"],
    "OPTICAL": ["copernicus", "planet"],
    "PASS_PREDICTION": ["n2yo", "hybrid"],
}


class AcquireWithProviderFallback:
    """Executes satellite acquisition queries across a prioritized failover chain of providers."""

    def __init__(
        self,
        health_repository: ProviderHealthRepository,
        aoi_repository: Optional[AreaOfInterestRepository] = None,
        custom_chains: Optional[dict[str, list[str]]] = None,
        provider_dispatchers: Optional[dict[str, Callable[..., Any]]] = None,
    ) -> None:
        self._health_repo = health_repository
        self._aoi_repo = aoi_repository
        self._chains = dict(DEFAULT_FALLBACK_CHAINS)
        if custom_chains:
            self._chains.update({k.upper(): list(v) for k, v in custom_chains.items()})
        self._dispatchers: dict[str, Callable[..., Any]] = provider_dispatchers or {}

    def register_dispatcher(self, provider_id: str, dispatcher_fn: Callable[..., Any]) -> None:
        """Register or override a dispatch execution callable for a specific provider."""
        self._dispatchers[provider_id.lower()] = dispatcher_fn

    def get_fallback_chain(self, modality: str = "SAR") -> list[str]:
        """Return the ordered candidate providers for a modality."""
        return list(self._chains.get(modality.upper(), ["copernicus"]))

    def get_all_chains(self) -> dict[str, list[str]]:
        """Return all configured fallback chains."""
        return {k: list(v) for k, v in self._chains.items()}

    def execute_dispatch(
        self,
        modality: str = "SAR",
        aoi_id: Optional[int] = None,
        bbox: Optional[Sequence[float] | BoundingBox] = None,
        preferred_provider: Optional[str] = None,
        allow_commercial: bool = True,
        acquisition_params: Optional[dict[str, Any]] = None,
    ) -> tuple[Any, FallbackResolution]:
        """Attempt acquisition on primary provider, automatically failing over to alternates upon failure."""
        mod = modality.upper()
        chain = list(self.get_fallback_chain(mod))

        # If user/caller specified a preferred provider, reorder it to the front
        if preferred_provider:
            pref = preferred_provider.lower()
            if pref in chain:
                chain.remove(pref)
                chain.insert(0, pref)
            else:
                chain.insert(0, pref)

        primary_id = chain[0]
        attempts: list[FallbackAttempt] = []
        now = datetime.now(timezone.utc)

        # Retrieve AOI if provided
        aoi = None
        if aoi_id is not None and self._aoi_repo is not None:
            aoi = self._aoi_repo.get(aoi_id)

        for candidate in chain:
            t_attempt = datetime.now(timezone.utc)
            # 1. Pre-flight health and quota check
            health = self._health_repo.get_health(candidate)
            quota = self._health_repo.get_quota(candidate)

            if health and not health.is_available:
                attempts.append(FallbackAttempt(
                    provider_id=candidate,
                    attempted_at=t_attempt,
                    success=False,
                    reason=FallbackReason.UNHEALTHY_STATUS,
                    error_message=f"Provider status is {health.status.value}: {health.status_message}",
                ))
                logger.warning(
                    "Failing over from %s: provider is in %s state",
                    candidate,
                    health.status.value,
                )
                continue

            if quota and not quota.has_quota_available:
                attempts.append(FallbackAttempt(
                    provider_id=candidate,
                    attempted_at=t_attempt,
                    success=False,
                    reason=FallbackReason.QUOTA_EXHAUSTED,
                    error_message=f"Daily limit of {quota.daily_request_limit} requests exhausted",
                ))
                logger.warning("Failing over from %s: daily request quota exhausted", candidate)
                continue

            # Commercial policy check (e.g., Umbra or Planet)
            is_commercial = candidate in ("umbra", "planet", "capella", "iceye")
            if is_commercial and not allow_commercial:
                attempts.append(FallbackAttempt(
                    provider_id=candidate,
                    attempted_at=t_attempt,
                    success=False,
                    reason=FallbackReason.INSUFFICIENT_FUNDS,
                    error_message="Commercial imagery not authorized for this dispatch",
                ))
                continue

            if is_commercial and aoi is not None:
                if not aoi.has_budget_available:
                    attempts.append(FallbackAttempt(
                        provider_id=candidate,
                        attempted_at=t_attempt,
                        success=False,
                        reason=FallbackReason.INSUFFICIENT_FUNDS,
                        error_message=f"AOI '{aoi.name}' budget exhausted (${aoi.budget_spent_usd:.2f} spent of ${aoi.collection_budget_usd:.2f})",
                    ))
                    continue

            # 2. Invoke provider dispatcher
            dispatcher = self._dispatchers.get(candidate)
            t0 = time.perf_counter()

            if dispatcher is None:
                # Simulated nominal dispatch if no external client bound
                elapsed_ms = (time.perf_counter() - t0) * 1000.0 + 55.0
                self._health_repo.record_health_event(candidate, success=True, latency_ms=elapsed_ms)
                self._health_repo.record_quota_consumption(candidate, units=1)

                attempts.append(FallbackAttempt(
                    provider_id=candidate,
                    attempted_at=t_attempt,
                    success=True,
                    latency_ms=elapsed_ms,
                    reason=FallbackReason.PRIMARY_SUCCEEDED if candidate == primary_id else None,
                ))

                resolution = FallbackResolution(
                    modality=mod,
                    primary_provider_id=primary_id,
                    selected_provider_id=candidate,
                    fallback_occurred=(candidate != primary_id),
                    attempts=attempts,
                    total_attempts=len(attempts),
                    dispatched_at=now,
                    metadata={"simulated": True},
                )
                return {"provider": candidate, "status": "dispatched", "simulated": True}, resolution

            # Real dispatcher execution
            try:
                result = dispatcher(
                    bbox=bbox,
                    aoi_id=aoi_id,
                    params=acquisition_params or {},
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._health_repo.record_health_event(candidate, success=True, latency_ms=elapsed_ms)
                self._health_repo.record_quota_consumption(candidate, units=1)

                attempts.append(FallbackAttempt(
                    provider_id=candidate,
                    attempted_at=t_attempt,
                    success=True,
                    latency_ms=elapsed_ms,
                    reason=FallbackReason.PRIMARY_SUCCEEDED if candidate == primary_id else None,
                ))

                resolution = FallbackResolution(
                    modality=mod,
                    primary_provider_id=primary_id,
                    selected_provider_id=candidate,
                    fallback_occurred=(candidate != primary_id),
                    attempts=attempts,
                    total_attempts=len(attempts),
                    dispatched_at=now,
                    metadata={"success": True},
                )
                return result, resolution

            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                err_msg = str(exc)
                logger.error("Provider %s failed during acquisition: %s", candidate, err_msg)
                self._health_repo.record_health_event(
                    candidate,
                    success=False,
                    latency_ms=elapsed_ms,
                    error_message=err_msg,
                )
                attempts.append(FallbackAttempt(
                    provider_id=candidate,
                    attempted_at=t_attempt,
                    success=False,
                    latency_ms=elapsed_ms,
                    reason=FallbackReason.TIMEOUT_OR_NETWORK_ERROR,
                    error_message=err_msg,
                ))
                continue

        # All candidates exhausted
        failed_resolution = FallbackResolution(
            modality=mod,
            primary_provider_id=primary_id,
            selected_provider_id="",
            fallback_occurred=True,
            attempts=attempts,
            total_attempts=len(attempts),
            dispatched_at=now,
            metadata={"exhausted": True},
        )
        raise AllProvidersExhaustedError(
            f"All providers in {mod} fallback chain ({', '.join(chain)}) failed or were unavailable.",
            resolution=failed_resolution,
        )
