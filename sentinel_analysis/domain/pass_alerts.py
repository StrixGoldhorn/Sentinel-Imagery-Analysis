"""Domain definitions and alert builders for satellite flypasts and imagery ingestion."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid

from sentinel_analysis.domain.entities import MaritimeAlert


class PassAlertEventType(str, Enum):
    MISSED_PASS = "MISSED_PASS"
    DELAYED_INGESTION = "DELAYED_INGESTION"
    AIS_MONITORING_FAILED = "AIS_MONITORING_FAILED"


class PassAlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class PassAlertEvaluation:
    """Summary of a pass or ingestion monitoring check."""
    aoi_id: int
    aoi_name: str
    pass_time: datetime
    satellite: str
    event_type: str
    severity: str
    is_alert: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    alert: Optional[MaritimeAlert] = None


def create_delayed_ingestion_alert(
    job_id: int,
    aoi_id: int,
    aoi_name: str,
    pass_time: datetime,
    satellite: str,
    elapsed_minutes: float,
    delay_threshold_minutes: float = 120.0,
    attempts: int = 1,
    correlation_id: Optional[str] = None,
) -> MaritimeAlert:
    """Create a standardized DELAYED_INGESTION MaritimeAlert."""
    now = datetime.now(timezone.utc)
    short_id = uuid.uuid4().hex[:8]
    pass_iso = pass_time.isoformat() if hasattr(pass_time, "isoformat") else str(pass_time)

    return MaritimeAlert(
        alert_id=f"alt-delay-{job_id}-{short_id}",
        event_type=PassAlertEventType.DELAYED_INGESTION.value,
        severity=PassAlertSeverity.WARNING.value,
        title=f"Delayed SAR Ingestion: {aoi_name} ({satellite or 'Sentinel-1'})",
        summary=(
            f"Imagery ingestion for {aoi_name} pass at {pass_iso} has been delayed by "
            f"{int(elapsed_minutes)} minutes (threshold: {int(delay_threshold_minutes)}m, "
            f"attempts: {attempts})."
        ),
        details={
            "job_id": job_id,
            "aoi_id": aoi_id,
            "aoi_name": aoi_name,
            "pass_time": pass_iso,
            "satellite": satellite or "Sentinel-1",
            "elapsed_minutes": round(elapsed_minutes, 1),
            "delay_threshold_minutes": delay_threshold_minutes,
            "polling_attempts": attempts,
            "alert_category": "ingestion_sla",
        },
        timestamp=now,
        correlation_id=correlation_id,
    )


def create_missed_pass_alert(
    aoi_id: int,
    aoi_name: str,
    pass_time: datetime,
    satellite: str,
    reason: str,
    job_id: Optional[int] = None,
    severity: str = PassAlertSeverity.CRITICAL.value,
    details: Optional[dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> MaritimeAlert:
    """Create a standardized MISSED_PASS MaritimeAlert."""
    now = datetime.now(timezone.utc)
    short_id = uuid.uuid4().hex[:8]
    pass_iso = pass_time.isoformat() if hasattr(pass_time, "isoformat") else str(pass_time)
    extra_details = dict(details or {})

    job_tag = f"-job{job_id}" if job_id is not None else ""
    return MaritimeAlert(
        alert_id=f"alt-missed{job_tag}-{short_id}",
        event_type=PassAlertEventType.MISSED_PASS.value,
        severity=severity.upper(),
        title=f"Missed Satellite Pass: {aoi_name} ({satellite or 'Sentinel-1'})",
        summary=f"Satellite pass over {aoi_name} scheduled at {pass_iso} missed collection or ingestion: {reason}",
        details={
            "aoi_id": aoi_id,
            "aoi_name": aoi_name,
            "pass_time": pass_iso,
            "satellite": satellite or "Sentinel-1",
            "reason": reason,
            "job_id": job_id,
            "alert_category": "pass_execution",
            **extra_details,
        },
        timestamp=now,
        correlation_id=correlation_id,
    )
