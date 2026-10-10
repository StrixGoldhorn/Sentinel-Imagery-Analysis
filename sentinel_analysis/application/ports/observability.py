"""Application port definitions for operational observability and telemetry."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sentinel_analysis.domain.observability import OperationalMetricsReport


@runtime_checkable
class ObservabilityRecorder(Protocol):
    """Protocol for capturing operational lifecycle events in real time."""

    def record_provider_failure(
        self,
        provider: str,
        operation: str,
        error_message: str,
        correlation_id: str | None = None,
    ) -> None:
        """Record an upstream API/provider failure event."""
        ...

    def record_alert_delivery(
        self,
        alert_id: str,
        channel: str,
        status: str,
        latency_ms: float,
        http_status: int | None = None,
        error_message: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Record the outcome and latency of an outbound alert delivery attempt."""
        ...

    def record_scan_duration(
        self,
        scan_id: str,
        duration_seconds: float,
    ) -> None:
        """Record the processing duration for an imagery scan."""
        ...


@runtime_checkable
class ObservabilityMetricsProvider(Protocol):
    """Protocol for reading system-wide operational metrics."""

    def collect_metrics(self) -> OperationalMetricsReport:
        """Collect and aggregate operational observability metrics across all subsystems."""
        ...
