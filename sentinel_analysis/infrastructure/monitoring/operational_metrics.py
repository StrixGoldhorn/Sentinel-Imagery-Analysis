"""Operational observability and metrics collection infrastructure."""

from __future__ import annotations

import json
import logging
import math
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.observability import (
    ObservabilityMetricsProvider,
    ObservabilityRecorder,
)
from sentinel_analysis.domain.correlation import ensure_correlation_id
from sentinel_analysis.domain.observability import (
    AlertDeliveryMetrics,
    DatabaseLockMetrics,
    DetectionCountMetrics,
    ImageryAgeMetrics,
    OperationalMetricsReport,
    ProviderFailureMetrics,
    QueueDepthMetrics,
    ScanLatencyMetrics,
    SchedulerLeadershipMetrics,
)
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase

logger = logging.getLogger(__name__)


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class OperationalMetricsCollector(ObservabilityRecorder, ObservabilityMetricsProvider):
    """Gathers, aggregates, and exposes operational telemetry across all Sentinel subsystems."""

    def __init__(
        self,
        database_path: Path | str,
        scans_dir: Path | str,
        start_time: Optional[datetime] = None,
    ) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path)
        self._scans_dir = Path(scans_dir).resolve()
        self._start_time = start_time or datetime.now(timezone.utc)
        self._recent_scan_durations: deque[float] = deque(maxlen=200)
        self._contention_count: int = 0

        # Ensure schema migrations up to 015 are run
        try:
            MigrationRunner(self._database_path).run_migrations()
        except Exception as exc:
            logger.warning("Migration execution during observability setup warning: %s", exc)

    # -------------------------------------------------------------------------
    # ObservabilityRecorder Protocol Implementation
    # -------------------------------------------------------------------------

    def record_provider_failure(
        self,
        provider: str,
        operation: str,
        error_message: str,
        correlation_id: str | None = None,
    ) -> None:
        """Record an upstream provider/API failure."""
        cid = ensure_correlation_id(correlation_id, prefix="prov")
        now_str = datetime.now(timezone.utc).isoformat()
        error_type = error_message.split(":")[0] if ":" in error_message else "ProviderError"
        try:
            with self._database.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO provider_failures (provider, operation, error_type, error_message, correlation_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (provider.lower(), operation, error_type, error_message, cid, now_str),
                )
        except Exception as exc:
            logger.warning("Failed to record provider failure: %s", exc)

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
        """Record outbound maritime alert dispatch telemetry."""
        cid = ensure_correlation_id(correlation_id, prefix="alert")
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            with self._database.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO alert_deliveries (
                        alert_id, webhook_id, service_type, status, http_status,
                        latency_ms, error_message, correlation_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert_id,
                        channel,
                        channel,
                        status.upper(),
                        http_status,
                        float(latency_ms),
                        error_message,
                        cid,
                        now_str,
                    ),
                )
        except Exception as exc:
            logger.warning("Failed to record alert delivery: %s", exc)

    def record_scan_duration(self, scan_id: str, duration_seconds: float) -> None:
        """Record processing duration for an imagery scan."""
        if duration_seconds >= 0:
            self._recent_scan_durations.append(float(duration_seconds))

    # -------------------------------------------------------------------------
    # ObservabilityMetricsProvider Protocol Implementation
    # -------------------------------------------------------------------------

    def collect_metrics(self) -> OperationalMetricsReport:
        """Compile live operational metrics report across 8 key subsystems."""
        now = datetime.now(timezone.utc)
        uptime = max(0.0, (now - self._start_time).total_seconds())

        queue_depth = self._collect_queue_depth()
        scan_latency = self._collect_scan_latency()
        provider_failures = self._collect_provider_failures()
        imagery_age = self._collect_imagery_age(now)
        scheduler_leadership = self._collect_scheduler_leadership(now)
        database_locks = self._collect_database_locks()
        detection_counts = self._collect_detection_counts()
        alert_delivery = self._collect_alert_delivery()

        return OperationalMetricsReport(
            timestamp=now,
            system_uptime_seconds=uptime,
            queue_depth=queue_depth,
            scan_latency=scan_latency,
            provider_failures=provider_failures,
            imagery_age=imagery_age,
            scheduler_leadership=scheduler_leadership,
            database_locks=database_locks,
            detection_counts=detection_counts,
            alert_delivery=alert_delivery,
        )

    collect = collect_metrics

    # -------------------------------------------------------------------------
    # Internal metric collector helpers
    # -------------------------------------------------------------------------

    def _collect_queue_depth(self) -> QueueDepthMetrics:
        active_tasks = 0
        queued_tasks = 0
        post_pass_polling = 0
        post_pass_querying = 0
        post_pass_ingesting = 0
        review_pending = 0

        try:
            with self._database.connection(rows=True) as conn:
                # 1. Background tasks
                try:
                    rows = conn.execute(
                        "SELECT status, COUNT(*) as cnt FROM background_tasks GROUP BY status"
                    ).fetchall()
                    for r in rows:
                        st = str(r["status"]).upper()
                        cnt = int(r["cnt"])
                        if st == "RUNNING":
                            active_tasks += cnt
                        elif st in ("QUEUED", "PENDING"):
                            queued_tasks += cnt
                except Exception:
                    pass

                # 2. Post pass ingestion jobs
                try:
                    rows = conn.execute(
                        "SELECT status, COUNT(*) as cnt FROM post_pass_ingestions WHERE deleted_at IS NULL GROUP BY status"
                    ).fetchall()
                    for r in rows:
                        st = str(r["status"]).upper()
                        cnt = int(r["cnt"])
                        if st in ("POLLING", "POLLING_CATALOG"):
                            post_pass_polling += cnt
                        elif st in ("QUERYING", "PENDING_PASS"):
                            post_pass_querying += cnt
                        elif st in ("INGESTING", "PROCESSING"):
                            post_pass_ingesting += cnt
                except Exception:
                    pass

                # 3. Review queue
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM reviews WHERE disposition = 'pending'"
                    ).fetchone()
                    if row:
                        review_pending = int(row["cnt"])
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Error querying queue depths: %s", exc)

        total = (
            active_tasks
            + queued_tasks
            + post_pass_polling
            + post_pass_querying
            + post_pass_ingesting
            + review_pending
        )
        return QueueDepthMetrics(
            background_tasks_active=active_tasks,
            background_tasks_queued=queued_tasks,
            post_pass_polling=post_pass_polling,
            post_pass_querying=post_pass_querying,
            post_pass_ingesting=post_pass_ingesting,
            review_queue_pending=review_pending,
            total_queue_depth=total,
        )

    def _collect_scan_latency(self) -> ScanLatencyMetrics:
        durations: list[float] = list(self._recent_scan_durations)

        # Inspect disk scans to enrich duration data if memory buffer is small
        if len(durations) < 10 and self._scans_dir.is_dir():
            try:
                for scan_dir in sorted(self._scans_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                    if scan_dir.is_dir():
                        meta_file = scan_dir / "metadata.json"
                        if meta_file.is_file():
                            try:
                                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                                d = meta.get("processing_duration_seconds") or meta.get("duration_seconds")
                                if d and float(d) > 0:
                                    durations.append(float(d))
                            except Exception:
                                pass
            except Exception:
                pass

        if not durations:
            return ScanLatencyMetrics(
                count=0,
                mean_seconds=0.0,
                p95_seconds=0.0,
                min_seconds=0.0,
                max_seconds=0.0,
                latest_scan_duration_seconds=None,
            )

        sorted_durations = sorted(durations)
        count = len(sorted_durations)
        mean_val = sum(sorted_durations) / count
        min_val = sorted_durations[0]
        max_val = sorted_durations[-1]
        p95_idx = int(math.ceil(0.95 * count)) - 1
        p95_val = sorted_durations[max(0, min(p95_idx, count - 1))]
        latest = durations[-1]

        return ScanLatencyMetrics(
            count=count,
            mean_seconds=round(mean_val, 2),
            p95_seconds=round(p95_val, 2),
            min_seconds=round(min_val, 2),
            max_seconds=round(max_val, 2),
            latest_scan_duration_seconds=round(latest, 2),
        )

    def _collect_provider_failures(self) -> ProviderFailureMetrics:
        total = 0
        by_provider: dict[str, int] = {}
        recent: list[dict[str, Any]] = []

        try:
            with self._database.connection(rows=True) as conn:
                try:
                    rows = conn.execute(
                        "SELECT provider, COUNT(*) as cnt FROM provider_failures GROUP BY provider"
                    ).fetchall()
                    for r in rows:
                        p = str(r["provider"]).lower()
                        cnt = int(r["cnt"])
                        by_provider[p] = cnt
                        total += cnt

                    recent_rows = conn.execute(
                        """
                        SELECT provider, operation, error_message, correlation_id, created_at
                        FROM provider_failures
                        ORDER BY id DESC
                        LIMIT 5
                        """
                    ).fetchall()
                    for r in recent_rows:
                        recent.append({
                            "provider": r["provider"],
                            "operation": r["operation"],
                            "error_message": r["error_message"],
                            "correlation_id": r["correlation_id"],
                            "created_at": r["created_at"],
                        })
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Error collecting provider failures: %s", exc)

        return ProviderFailureMetrics(
            total_failures=total,
            by_provider=by_provider,
            recent_failures=recent,
        )

    def _collect_imagery_age(self, now: datetime) -> ImageryAgeMetrics:
        aoi_list: list[dict[str, Any]] = []
        newest_overall: Optional[float] = None
        oldest_overall: Optional[float] = None

        # Load scans with acquisition timestamps
        scan_timestamps: list[datetime] = []
        if self._scans_dir.is_dir():
            try:
                for scan_dir in self._scans_dir.iterdir():
                    if scan_dir.is_dir():
                        meta_file = scan_dir / "metadata.json"
                        if meta_file.is_file():
                            try:
                                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                                acq = meta.get("acquisition_datetime")
                                if acq:
                                    dt = _parse_dt(acq)
                                    if dt:
                                        scan_timestamps.append(dt)
                            except Exception:
                                pass
            except Exception:
                pass

        scan_timestamps.sort(reverse=True)

        try:
            with self._database.connection(rows=True) as conn:
                aoi_rows = conn.execute("SELECT id, name, last_checked FROM aoi ORDER BY name").fetchall()
                for row in aoi_rows:
                    aoi_id = int(row["id"])
                    aoi_name = str(row["name"])
                    
                    # Estimate imagery age based on latest available scan
                    newest_age_hours: Optional[float] = None
                    if scan_timestamps:
                        age = max(0.0, (now - scan_timestamps[0]).total_seconds() / 3600.0)
                        newest_age_hours = round(age, 1)
                        if newest_overall is None or age < newest_overall:
                            newest_overall = age
                        if oldest_overall is None or age > oldest_overall:
                            oldest_overall = age

                    status = "none"
                    if newest_age_hours is not None:
                        if newest_age_hours < 24.0:
                            status = "fresh"
                        elif newest_age_hours < 72.0:
                            status = "moderate"
                        else:
                            status = "stale"

                    aoi_list.append({
                        "aoi_id": aoi_id,
                        "aoi_name": aoi_name,
                        "scan_count": len(scan_timestamps),
                        "newest_scan_age_hours": newest_age_hours,
                        "status": status,
                    })
        except Exception as exc:
            logger.warning("Error collecting imagery age: %s", exc)

        return ImageryAgeMetrics(
            overall_newest_hours=round(newest_overall, 1) if newest_overall is not None else None,
            overall_oldest_hours=round(oldest_overall, 1) if oldest_overall is not None else None,
            by_aoi=aoi_list,
        )

    def _collect_scheduler_leadership(self, now: datetime) -> SchedulerLeadershipMetrics:
        try:
            with self._database.connection(rows=True) as conn:
                row = conn.execute(
                    "SELECT owner_id, lease_expires_at, heartbeat_at FROM scheduler_leases WHERE name = 'pass_scheduler'"
                ).fetchone()
                if row:
                    owner_id = str(row["owner_id"])
                    expires_dt = _parse_dt(row["lease_expires_at"])
                    heartbeat_str = str(row["heartbeat_at"])
                    expires_str = str(row["lease_expires_at"])

                    if expires_dt and expires_dt > now:
                        time_left = max(0.0, (expires_dt - now).total_seconds())
                        return SchedulerLeadershipMetrics(
                            is_leader=True,
                            owner_id=owner_id,
                            lease_expires_at=expires_str,
                            heartbeat_at=heartbeat_str,
                            time_until_expiry_seconds=round(time_left, 1),
                        )
                    else:
                        return SchedulerLeadershipMetrics(
                            is_leader=False,
                            owner_id=owner_id,
                            lease_expires_at=expires_str,
                            heartbeat_at=heartbeat_str,
                            time_until_expiry_seconds=0.0,
                        )
        except Exception as exc:
            logger.debug("Scheduler lease check: %s", exc)

        return SchedulerLeadershipMetrics(
            is_leader=False,
            owner_id=None,
            lease_expires_at=None,
            heartbeat_at=None,
            time_until_expiry_seconds=None,
        )

    def _collect_database_locks(self) -> DatabaseLockMetrics:
        journal_mode = "wal"
        busy_timeout = 5000
        size_bytes = 0
        table_counts: dict[str, int] = {}

        try:
            if self._database_path.is_file():
                size_bytes += self._database_path.stat().st_size
                wal_path = self._database_path.with_name(self._database_path.name + "-wal")
                if wal_path.is_file():
                    size_bytes += wal_path.stat().st_size

            with self._database.connection(rows=True) as conn:
                jm_row = conn.execute("PRAGMA journal_mode;").fetchone()
                if jm_row:
                    journal_mode = str(jm_row[0]).lower()

                bt_row = conn.execute("PRAGMA busy_timeout;").fetchone()
                if bt_row:
                    busy_timeout = int(bt_row[0])

                for tbl in (
                    "aoi",
                    "vessels",
                    "vessel_locations",
                    "background_tasks",
                    "post_pass_ingestions",
                    "alert_deliveries",
                    "provider_failures",
                    "reviews",
                ):
                    try:
                        c_row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
                        if c_row:
                            table_counts[tbl] = int(c_row[0])
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Error inspecting database locks and size: %s", exc)
            self._contention_count += 1

        lock_status = "contended" if self._contention_count > 5 else "normal"
        return DatabaseLockMetrics(
            journal_mode=journal_mode,
            busy_timeout_ms=busy_timeout,
            busy_contention_count=self._contention_count,
            lock_status=lock_status,
            database_size_bytes=size_bytes,
            table_counts=table_counts,
        )

    def _collect_detection_counts(self) -> DetectionCountMetrics:
        total = 0
        classes: dict[str, int] = {}
        anomalies: dict[str, int] = {
            "dark_vessels": 0,
            "transshipment": 0,
            "loitering": 0,
            "geofence_breaches": 0,
        }

        if self._scans_dir.is_dir():
            try:
                for scan_dir in self._scans_dir.iterdir():
                    if not scan_dir.is_dir():
                        continue
                    det_file = scan_dir / "detection_results.json"
                    if not det_file.is_file():
                        continue
                    try:
                        data = json.loads(det_file.read_text(encoding="utf-8"))
                        detections = data if isinstance(data, list) else data.get("detections", [])
                        for d in detections:
                            total += 1
                            cls_name = str(d.get("vessel_class") or d.get("class") or "unclassified").lower()
                            classes[cls_name] = classes.get(cls_name, 0) + 1

                            # Check for dark vessel or other anomalies
                            is_dark = bool(d.get("is_dark") or d.get("dark_vessel"))
                            if is_dark:
                                anomalies["dark_vessels"] += 1
                            if d.get("is_transshipment"):
                                anomalies["transshipment"] += 1
                            if d.get("is_loitering"):
                                anomalies["loitering"] += 1
                            if d.get("geofence_breach"):
                                anomalies["geofence_breaches"] += 1
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Error reading detection results for metrics: %s", exc)

        return DetectionCountMetrics(
            cumulative_detections=total,
            by_vessel_class=classes,
            anomalies=anomalies,
        )

    def _collect_alert_delivery(self) -> AlertDeliveryMetrics:
        total = 0
        success = 0
        failure = 0
        latencies: list[float] = []
        by_service: dict[str, dict[str, Any]] = {}

        try:
            with self._database.connection(rows=True) as conn:
                try:
                    rows = conn.execute(
                        "SELECT service_type, status, latency_ms FROM alert_deliveries"
                    ).fetchall()
                    for r in rows:
                        total += 1
                        svc = str(r["service_type"]).lower()
                        st = str(r["status"]).upper()
                        lat = float(r["latency_ms"])
                        latencies.append(lat)

                        if svc not in by_service:
                            by_service[svc] = {"attempts": 0, "success": 0, "failure": 0}
                        by_service[svc]["attempts"] += 1

                        if st == "SUCCESS":
                            success += 1
                            by_service[svc]["success"] += 1
                        else:
                            failure += 1
                            by_service[svc]["failure"] += 1
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Error collecting alert deliveries: %s", exc)

        rate = (success / total * 100.0) if total > 0 else 100.0
        mean_lat = (sum(latencies) / len(latencies)) if latencies else 0.0
        sorted_lat = sorted(latencies)
        p95_lat = 0.0
        if sorted_lat:
            p95_idx = int(math.ceil(0.95 * len(sorted_lat))) - 1
            p95_lat = sorted_lat[max(0, min(p95_idx, len(sorted_lat) - 1))]

        return AlertDeliveryMetrics(
            total_attempts=total,
            success_count=success,
            failure_count=failure,
            success_rate_percent=round(rate, 2),
            mean_latency_ms=round(mean_lat, 2),
            p95_latency_ms=round(p95_lat, 2),
            by_service_type=by_service,
        )
