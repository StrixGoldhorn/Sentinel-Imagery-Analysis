"""Domain entities and contracts for operational observability and telemetry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class QueueDepthMetrics:
    """Telemetry for asynchronous task and ingestion queues."""
    background_tasks_active: int = 0
    background_tasks_queued: int = 0
    post_pass_polling: int = 0
    post_pass_querying: int = 0
    post_pass_ingesting: int = 0
    review_queue_pending: int = 0
    total_queue_depth: int = 0


@dataclass(frozen=True)
class ScanLatencyMetrics:
    """Latency statistics for SAR image ingestion and processing pipelines."""
    count: int = 0
    mean_seconds: float = 0.0
    p95_seconds: float = 0.0
    min_seconds: float = 0.0
    max_seconds: float = 0.0
    latest_scan_duration_seconds: Optional[float] = None


@dataclass(frozen=True)
class ProviderFailureMetrics:
    """Aggregated failure rates and error counts across external providers."""
    total_failures: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)
    recent_failures: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AOIImageryAge:
    """Freshness of SAR imagery for a single Area of Interest."""
    aoi_id: int
    aoi_name: str
    scan_count: int
    newest_scan_age_hours: Optional[float]
    status: str  # "fresh" (<24h), "moderate" (<72h), "stale" (>=72h), "none"


@dataclass(frozen=True)
class ImageryAgeMetrics:
    """Imagery staleness across active monitoring zones."""
    overall_newest_hours: Optional[float] = None
    overall_oldest_hours: Optional[float] = None
    by_aoi: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SchedulerLeadershipMetrics:
    """Distributed leader election and lease status for periodic workers."""
    is_leader: bool = False
    owner_id: Optional[str] = None
    lease_expires_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    time_until_expiry_seconds: Optional[float] = None


@dataclass(frozen=True)
class DatabaseLockMetrics:
    """SQLite transaction locks, contention metrics, and storage footprint."""
    journal_mode: str = "wal"
    busy_timeout_ms: int = 5000
    busy_contention_count: int = 0
    lock_status: str = "normal"  # "normal" or "contended"
    database_size_bytes: int = 0
    table_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectionCountMetrics:
    """Cumulative detection volumes and anomaly classifications."""
    cumulative_detections: int = 0
    by_vessel_class: dict[str, int] = field(default_factory=dict)
    anomalies: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertDeliveryMetrics:
    """Maritime alert webhook delivery success, failure, and latency metrics."""
    total_attempts: int = 0
    success_count: int = 0
    failure_count: int = 0
    success_rate_percent: float = 100.0
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    by_service_type: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationalMetricsReport:
    """Unified operational observability telemetry report."""
    timestamp: datetime
    system_uptime_seconds: float
    queue_depth: QueueDepthMetrics
    scan_latency: ScanLatencyMetrics
    provider_failures: ProviderFailureMetrics
    imagery_age: ImageryAgeMetrics
    scheduler_leadership: SchedulerLeadershipMetrics
    database_locks: DatabaseLockMetrics
    detection_counts: DetectionCountMetrics
    alert_delivery: AlertDeliveryMetrics

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "system_uptime_seconds": self.system_uptime_seconds,
            "queue_depth": asdict(self.queue_depth),
            "scan_latency": asdict(self.scan_latency),
            "provider_failures": asdict(self.provider_failures),
            "imagery_age": asdict(self.imagery_age),
            "scheduler_leadership": asdict(self.scheduler_leadership),
            "database_locks": asdict(self.database_locks),
            "detection_counts": asdict(self.detection_counts),
            "alert_delivery": asdict(self.alert_delivery),
        }

    def to_prometheus_text(self) -> str:
        """Render metrics in standard Prometheus exposition text format."""
        lines: list[str] = []

        def add_gauge(name: str, help_text: str, value: float | int | None, labels: dict[str, str] | None = None) -> None:
            if value is None:
                return
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            if labels:
                label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
                lines.append(f"{name}{{{label_str}}} {value}")
            else:
                lines.append(f"{name} {value}")

        def add_counter(name: str, help_text: str, value: float | int | None, labels: dict[str, str] | None = None) -> None:
            if value is None:
                return
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            if labels:
                label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
                lines.append(f"{name}{{{label_str}}} {value}")
            else:
                lines.append(f"{name} {value}")

        # System
        add_gauge("sentinel_system_uptime_seconds", "System uptime in seconds", self.system_uptime_seconds)

        # Queue Depth
        add_gauge("sentinel_queue_depth_total", "Total operational queue depth", self.queue_depth.total_queue_depth)
        add_gauge("sentinel_queue_depth_background_active", "Active background tasks", self.queue_depth.background_tasks_active)
        add_gauge("sentinel_queue_depth_background_queued", "Queued background tasks", self.queue_depth.background_tasks_queued)
        add_gauge("sentinel_queue_depth_post_pass_polling", "Post-pass jobs polling catalog", self.queue_depth.post_pass_polling)
        add_gauge("sentinel_queue_depth_post_pass_querying", "Post-pass jobs querying catalog", self.queue_depth.post_pass_querying)
        add_gauge("sentinel_queue_depth_review_pending", "Pending analyst reviews in queue", self.queue_depth.review_queue_pending)

        # Scan Latency
        add_gauge("sentinel_scan_latency_mean_seconds", "Mean scan pipeline processing latency", self.scan_latency.mean_seconds)
        add_gauge("sentinel_scan_latency_p95_seconds", "P95 scan pipeline processing latency", self.scan_latency.p95_seconds)
        add_gauge("sentinel_scan_latency_min_seconds", "Minimum scan processing latency", self.scan_latency.min_seconds)
        add_gauge("sentinel_scan_latency_max_seconds", "Maximum scan processing latency", self.scan_latency.max_seconds)
        add_counter("sentinel_scans_processed_total", "Total scans processed by pipeline", self.scan_latency.count)

        # Provider Failures
        add_counter("sentinel_provider_failures_total", "Total provider failure count", self.provider_failures.total_failures)
        for provider, count in self.provider_failures.by_provider.items():
            add_counter("sentinel_provider_failures", "Provider failures by provider", count, {"provider": provider})

        # Imagery Age
        add_gauge("sentinel_imagery_age_newest_hours", "Hours since newest SAR acquisition across all AOIs", self.imagery_age.overall_newest_hours)
        add_gauge("sentinel_imagery_age_oldest_hours", "Hours since oldest SAR acquisition across all AOIs", self.imagery_age.overall_oldest_hours)

        # Scheduler Leadership
        add_gauge("sentinel_scheduler_is_leader", "1 if this node is active scheduler leader, 0 otherwise", 1 if self.scheduler_leadership.is_leader else 0)
        if self.scheduler_leadership.time_until_expiry_seconds is not None:
            add_gauge("sentinel_scheduler_lease_time_remaining_seconds", "Seconds remaining on scheduler lease", self.scheduler_leadership.time_until_expiry_seconds)

        # Database Locks & Contention
        add_counter("sentinel_database_busy_contention_total", "Total SQLite lock contention / busy events", self.database_locks.busy_contention_count)
        add_gauge("sentinel_database_size_bytes", "SQLite database file size in bytes", self.database_locks.database_size_bytes)
        for tbl, cnt in self.database_locks.table_counts.items():
            add_gauge("sentinel_database_table_rows", "Table row count", cnt, {"table": tbl})

        # Detection Counts
        add_counter("sentinel_detections_cumulative_total", "Total cumulative detections across all scans", self.detection_counts.cumulative_detections)
        for v_class, cnt in self.detection_counts.by_vessel_class.items():
            add_counter("sentinel_detections_by_class_total", "Cumulative detections by vessel class", cnt, {"vessel_class": v_class})
        for anomaly, cnt in self.detection_counts.anomalies.items():
            add_counter("sentinel_detection_anomalies_total", "Cumulative detected anomalies", cnt, {"anomaly": anomaly})

        # Alert Deliveries
        add_counter("sentinel_alert_delivery_attempts_total", "Total webhook alert delivery attempts", self.alert_delivery.total_attempts)
        add_counter("sentinel_alert_delivery_success_total", "Successful webhook alert deliveries", self.alert_delivery.success_count)
        add_counter("sentinel_alert_delivery_failure_total", "Failed webhook alert deliveries", self.alert_delivery.failure_count)
        add_gauge("sentinel_alert_delivery_success_rate_percent", "Webhook delivery success rate percentage", self.alert_delivery.success_rate_percent)
        add_gauge("sentinel_alert_delivery_mean_latency_ms", "Mean alert delivery latency in milliseconds", self.alert_delivery.mean_latency_ms)

        return "\n".join(lines) + "\n"
