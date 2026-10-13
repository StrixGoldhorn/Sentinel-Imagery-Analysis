"""Use case for detecting missed satellite passes and delayed imagery ingestion."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional, Set

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.post_pass_repository import PostPassIngestionRepository
from sentinel_analysis.application.use_cases.manage_alerts import DispatchMaritimeAlert
from sentinel_analysis.domain.entities import MaritimeAlert
from sentinel_analysis.domain.pass_alerts import (
    PassAlertEvaluation,
    PassAlertEventType,
    PassAlertSeverity,
    create_delayed_ingestion_alert,
    create_missed_pass_alert,
)

logger = logging.getLogger(__name__)


class EvaluatePassAlerts:
    """Evaluates satellite pass schedules and ingestion pipelines to detect and alert on delays and misses."""

    def __init__(
        self,
        post_pass_repository: PostPassIngestionRepository,
        aoi_repository: AreaOfInterestRepository,
        pass_monitor: Optional[Any] = None,
        dispatch_alert: Optional[DispatchMaritimeAlert] = None,
        default_delay_threshold_minutes: float = 120.0,
    ) -> None:
        self._jobs = post_pass_repository
        self._aois = aoi_repository
        self._pass_monitor = pass_monitor
        self._dispatch_alert = dispatch_alert
        self._delay_threshold_minutes = max(10.0, float(default_delay_threshold_minutes))
        self._alerted_keys: Set[str] = set()

    def execute(
        self,
        delay_threshold_minutes: Optional[float] = None,
        auto_dispatch: bool = True,
    ) -> dict[str, Any]:
        """Evaluate recent and active pass jobs, generating alerts for delays and misses."""
        threshold = float(delay_threshold_minutes) if delay_threshold_minutes is not None else self._delay_threshold_minutes
        now = datetime.now(timezone.utc)

        # Cache AOI names
        aoi_names: dict[int, str] = {}
        try:
            for aoi in self._aois.list():
                if aoi.id is not None:
                    aoi_names[aoi.id] = aoi.name
        except Exception as exc:
            logger.debug("Failed listing AOIs: %s", exc)

        evaluations: list[PassAlertEvaluation] = []
        new_alerts: list[MaritimeAlert] = []

        # 1. Evaluate PostPassIngestionJobs
        active_jobs = []
        try:
            if hasattr(self._jobs, "list_pending"):
                active_jobs.extend(self._jobs.list_pending(limit=200))
            if hasattr(self._jobs, "list"):
                # Also check recent jobs for failures / timeouts
                for j in self._jobs.list()[:200]:
                    if j not in active_jobs:
                        active_jobs.append(j)
        except Exception as exc:
            logger.warning("Error fetching post-pass jobs for alert evaluation: %s", exc)

        for job in active_jobs:
            aoi_name = aoi_names.get(job.aoi_id, job.aoi_name or f"AOI #{job.aoi_id}")
            pass_time = job.pass_time.astimezone(timezone.utc) if job.pass_time.tzinfo else job.pass_time.replace(tzinfo=timezone.utc)
            elapsed_minutes = max(0.0, (now - pass_time).total_seconds() / 60.0)

            # Check for Missed Pass (Job Timed Out or Failed)
            if job.status in ("TIMED_OUT", "FAILED"):
                dedup_key = f"missed_job_{job.id}_{job.status}"
                is_new = dedup_key not in self._alerted_keys
                reason = job.error_message or (
                    "Exceeded maximum post-pass catalog wait window" if job.status == "TIMED_OUT" else "Ingestion pipeline failure"
                )
                alert = create_missed_pass_alert(
                    aoi_id=job.aoi_id,
                    aoi_name=aoi_name,
                    pass_time=pass_time,
                    satellite=job.satellite or "Sentinel-1",
                    reason=reason,
                    job_id=job.id,
                    severity=PassAlertSeverity.CRITICAL.value,
                    details={"job_status": job.status, "attempts": job.attempts},
                )
                evaluations.append(
                    PassAlertEvaluation(
                        aoi_id=job.aoi_id,
                        aoi_name=aoi_name,
                        pass_time=pass_time,
                        satellite=job.satellite or "Sentinel-1",
                        event_type=PassAlertEventType.MISSED_PASS.value,
                        severity=PassAlertSeverity.CRITICAL.value,
                        is_alert=True,
                        reason=reason,
                        details={"job_id": job.id, "job_status": job.status},
                        alert=alert,
                    )
                )
                if is_new:
                    self._alerted_keys.add(dedup_key)
                    new_alerts.append(alert)

            # Check for Delayed Ingestion (Polling catalog exceeding delay threshold)
            elif job.status == "POLLING_CATALOG" and elapsed_minutes > threshold:
                dedup_key = f"delayed_job_{job.id}"
                is_new = dedup_key not in self._alerted_keys
                alert = create_delayed_ingestion_alert(
                    job_id=job.id,
                    aoi_id=job.aoi_id,
                    aoi_name=aoi_name,
                    pass_time=pass_time,
                    satellite=job.satellite or "Sentinel-1",
                    elapsed_minutes=elapsed_minutes,
                    delay_threshold_minutes=threshold,
                    attempts=job.attempts,
                )
                evaluations.append(
                    PassAlertEvaluation(
                        aoi_id=job.aoi_id,
                        aoi_name=aoi_name,
                        pass_time=pass_time,
                        satellite=job.satellite or "Sentinel-1",
                        event_type=PassAlertEventType.DELAYED_INGESTION.value,
                        severity=PassAlertSeverity.WARNING.value,
                        is_alert=True,
                        reason=f"Ingestion delayed by {int(elapsed_minutes)}m exceeding SLA of {int(threshold)}m",
                        details={"job_id": job.id, "elapsed_minutes": elapsed_minutes, "attempts": job.attempts},
                        alert=alert,
                    )
                )
                if is_new:
                    self._alerted_keys.add(dedup_key)
                    new_alerts.append(alert)

        # 2. Evaluate Pass Monitor AIS Failures (if monitor available)
        if self._pass_monitor is not None and hasattr(self._pass_monitor, "get_active_monitors"):
            try:
                monitors = self._pass_monitor.get_active_monitors()
                for mon in monitors:
                    status = mon.get("status")
                    if status in ("COMPLETED", "CANCELLED"):
                        records = mon.get("records_ingested", 0)
                        attempts = mon.get("scrape_attempts", 0)
                        completed = mon.get("scrapes_completed", 0)
                        failed = mon.get("scrapes_failed", 0)
                        aoi_id = mon.get("aoi_id", 0)
                        aoi_name = mon.get("aoi_name") or aoi_names.get(aoi_id, f"AOI #{aoi_id}")
                        pass_time_str = mon.get("pass_time")
                        try:
                            mon_pass_time = datetime.fromisoformat(pass_time_str)
                        except Exception:
                            mon_pass_time = now

                        # If attempts were made but 0 AIS records were ingested or all scrapes failed
                        if attempts > 0 and records == 0 and (failed > 0 or completed == 0):
                            dedup_key = f"ais_missed_{aoi_id}_{pass_time_str}"
                            if dedup_key not in self._alerted_keys:
                                self._alerted_keys.add(dedup_key)
                                reason = mon.get("error") or "AIS scraper failed to capture any positions during satellite pass window"
                                alert = create_missed_pass_alert(
                                    aoi_id=aoi_id,
                                    aoi_name=aoi_name,
                                    pass_time=mon_pass_time,
                                    satellite="Sentinel-1",
                                    reason=reason,
                                    severity=PassAlertSeverity.WARNING.value,
                                    details={"scrape_attempts": attempts, "scrapes_failed": failed, "records_ingested": records},
                                )
                                new_alerts.append(alert)
                                evaluations.append(
                                    PassAlertEvaluation(
                                        aoi_id=aoi_id,
                                        aoi_name=aoi_name,
                                        pass_time=mon_pass_time,
                                        satellite="Sentinel-1",
                                        event_type=PassAlertEventType.MISSED_PASS.value,
                                        severity=PassAlertSeverity.WARNING.value,
                                        is_alert=True,
                                        reason=reason,
                                        details=mon,
                                        alert=alert,
                                    )
                                )
            except Exception as exc:
                logger.warning("Error evaluating pass monitor telemetry: %s", exc)

        # 3. Dispatch alerts if enabled
        dispatched_count = 0
        dispatch_results = []
        if auto_dispatch and self._dispatch_alert is not None:
            for alert in new_alerts:
                try:
                    res = self._dispatch_alert.execute(alert)
                    dispatched_count += res.get("dispatched", 0)
                    dispatch_results.append(res)
                except Exception as exc:
                    logger.warning("Failed dispatching pass alert %s: %s", alert.alert_id, exc)

        return {
            "status": "success",
            "evaluated_at": now.isoformat(),
            "threshold_minutes": threshold,
            "total_evaluations": len(evaluations),
            "new_alerts_count": len(new_alerts),
            "delayed_ingestion_count": sum(1 for a in new_alerts if a.event_type == PassAlertEventType.DELAYED_INGESTION.value),
            "missed_pass_count": sum(1 for a in new_alerts if a.event_type == PassAlertEventType.MISSED_PASS.value),
            "dispatched_count": dispatched_count,
            "dispatch_results": dispatch_results,
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "event_type": a.event_type,
                    "severity": a.severity,
                    "title": a.title,
                    "summary": a.summary,
                    "details": a.details,
                    "timestamp": a.timestamp.isoformat() if a.timestamp else None,
                }
                for a in new_alerts
            ],
        }

    def reset_deduplication(self) -> None:
        """Clear the deduplication cache (useful in tests and re-auditing)."""
        self._alerted_keys.clear()
