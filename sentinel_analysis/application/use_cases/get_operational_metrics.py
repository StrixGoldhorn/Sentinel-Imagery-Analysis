"""Use case for collecting system-wide operational metrics."""

from __future__ import annotations

from dataclasses import dataclass

from sentinel_analysis.application.ports.observability import ObservabilityMetricsProvider
from sentinel_analysis.domain.observability import OperationalMetricsReport


@dataclass
class GetOperationalMetrics:
    """Use case coordinating operational observability telemetry collection."""

    provider: ObservabilityMetricsProvider

    def execute(self) -> OperationalMetricsReport:
        """Fetch unified operational metrics across all system pillars."""
        return self.provider.collect_metrics()
