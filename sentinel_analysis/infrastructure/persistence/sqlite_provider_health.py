"""SQLite persistence implementation for provider health and operational quotas."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sentinel_analysis.application.ports.provider_health_repository import (
    ProviderHealthRepository,
)
from sentinel_analysis.domain.provider_health import (
    ProviderHealthRecord,
    ProviderQuotaRecord,
    ProviderStatus,
)
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


def _parse_dt(val: object) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


DEFAULT_PROVIDERS = [
    {
        "provider_id": "copernicus",
        "name": "Copernicus Data Space Ecosystem",
        "endpoint_url": "https://dataspace.copernicus.eu",
        "daily_limit": 5000,
        "monthly_limit": 100000,
        "rate_per_min": 120,
        "credits": None,
    },
    {
        "provider_id": "asf",
        "name": "NASA ASF DAAC",
        "endpoint_url": "https://api.daac.asf.alaska.edu",
        "daily_limit": 10000,
        "monthly_limit": 250000,
        "rate_per_min": 300,
        "credits": None,
    },
    {
        "provider_id": "umbra",
        "name": "Umbra Commercial SAR",
        "endpoint_url": "https://api.umbra.space",
        "daily_limit": 500,
        "monthly_limit": 5000,
        "rate_per_min": 30,
        "credits": 25000.0,
    },
    {
        "provider_id": "n2yo",
        "name": "N2YO Satellite Tracker",
        "endpoint_url": "https://api.n2yo.com",
        "daily_limit": 1000,
        "monthly_limit": 30000,
        "rate_per_min": 60,
        "credits": None,
    },
    {
        "provider_id": "planet",
        "name": "PlanetScope Optical",
        "endpoint_url": "https://api.planet.com",
        "daily_limit": 2000,
        "monthly_limit": 50000,
        "rate_per_min": 60,
        "credits": 15000.0,
    },
]


class SQLiteProviderHealthRepository(ProviderHealthRepository):
    """Stores and queries provider health metrics and rate limits in SQLite."""

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path)
        MigrationRunner(self._database_path).run_migrations()
        self._seed_default_providers_if_needed()

    def _seed_default_providers_if_needed(self) -> None:
        with self._database.connection(rows=True) as conn:
            existing = conn.execute("SELECT COUNT(*) as cnt FROM provider_health").fetchone()
            if existing and int(existing["cnt"]) > 0:
                return

            now = datetime.now(timezone.utc).isoformat()
            for p in DEFAULT_PROVIDERS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO provider_health (
                        provider_id, name, status, latency_ms, error_rate_pct,
                        consecutive_failures, status_message, endpoint_url, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p["provider_id"],
                        p["name"],
                        ProviderStatus.HEALTHY.value,
                        45.0,
                        0.0,
                        0,
                        "Operational and ready",
                        p["endpoint_url"],
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO provider_quotas (
                        provider_id, daily_request_limit, daily_requests_used,
                        monthly_request_limit, monthly_requests_used,
                        rate_limit_per_minute, credit_balance_usd, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p["provider_id"],
                        p["daily_limit"],
                        0,
                        p["monthly_limit"],
                        0,
                        p["rate_per_min"],
                        p["credits"],
                        now,
                    ),
                )

    def save_health(self, record: ProviderHealthRecord) -> None:
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO provider_health (
                    provider_id, name, status, latency_ms, error_rate_pct,
                    last_success_at, last_failure_at, consecutive_failures,
                    status_message, endpoint_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    latency_ms = excluded.latency_ms,
                    error_rate_pct = excluded.error_rate_pct,
                    last_success_at = excluded.last_success_at,
                    last_failure_at = excluded.last_failure_at,
                    consecutive_failures = excluded.consecutive_failures,
                    status_message = excluded.status_message,
                    endpoint_url = excluded.endpoint_url,
                    updated_at = excluded.updated_at
                """,
                (
                    record.provider_id,
                    record.name,
                    record.status.value,
                    record.latency_ms,
                    record.error_rate_pct,
                    record.last_success_at.isoformat() if record.last_success_at else None,
                    record.last_failure_at.isoformat() if record.last_failure_at else None,
                    record.consecutive_failures,
                    record.status_message,
                    record.endpoint_url,
                    record.updated_at.isoformat(),
                ),
            )

    def get_health(self, provider_id: str) -> Optional[ProviderHealthRecord]:
        norm = (provider_id or "").strip().lower()
        with self._database.connection(rows=True) as conn:
            row = conn.execute(
                "SELECT * FROM provider_health WHERE provider_id = ?",
                (norm,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_health(row)

    def list_health(self) -> list[ProviderHealthRecord]:
        with self._database.connection(rows=True) as conn:
            rows = conn.execute("SELECT * FROM provider_health ORDER BY provider_id ASC").fetchall()
            return [self._row_to_health(r) for r in rows]

    def _row_to_health(self, row: Any) -> ProviderHealthRecord:
        status_str = str(row["status"]).upper()
        try:
            status = ProviderStatus(status_str)
        except ValueError:
            status = ProviderStatus.HEALTHY

        return ProviderHealthRecord(
            provider_id=row["provider_id"],
            name=row["name"],
            status=status,
            latency_ms=float(row["latency_ms"] or 0.0),
            error_rate_pct=float(row["error_rate_pct"] or 0.0),
            last_success_at=_parse_dt(row["last_success_at"]),
            last_failure_at=_parse_dt(row["last_failure_at"]),
            consecutive_failures=int(row["consecutive_failures"] or 0),
            status_message=str(row["status_message"] or "Operational"),
            endpoint_url=row["endpoint_url"],
            updated_at=_parse_dt(row["updated_at"]) or datetime.now(timezone.utc),
        )

    def save_quota(self, record: ProviderQuotaRecord) -> None:
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO provider_quotas (
                    provider_id, daily_request_limit, daily_requests_used,
                    monthly_request_limit, monthly_requests_used,
                    rate_limit_per_minute, credit_balance_usd, reset_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    daily_request_limit = excluded.daily_request_limit,
                    daily_requests_used = excluded.daily_requests_used,
                    monthly_request_limit = excluded.monthly_request_limit,
                    monthly_requests_used = excluded.monthly_requests_used,
                    rate_limit_per_minute = excluded.rate_limit_per_minute,
                    credit_balance_usd = excluded.credit_balance_usd,
                    reset_at = excluded.reset_at,
                    updated_at = excluded.updated_at
                """,
                (
                    record.provider_id,
                    record.daily_request_limit,
                    record.daily_requests_used,
                    record.monthly_request_limit,
                    record.monthly_requests_used,
                    record.rate_limit_per_minute,
                    record.credit_balance_usd,
                    record.reset_at.isoformat() if record.reset_at else None,
                    record.updated_at.isoformat(),
                ),
            )

    def get_quota(self, provider_id: str) -> Optional[ProviderQuotaRecord]:
        norm = (provider_id or "").strip().lower()
        with self._database.connection(rows=True) as conn:
            row = conn.execute(
                "SELECT * FROM provider_quotas WHERE provider_id = ?",
                (norm,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_quota(row)

    def list_quotas(self) -> list[ProviderQuotaRecord]:
        with self._database.connection(rows=True) as conn:
            rows = conn.execute("SELECT * FROM provider_quotas ORDER BY provider_id ASC").fetchall()
            return [self._row_to_quota(r) for r in rows]

    def _row_to_quota(self, row: Any) -> ProviderQuotaRecord:
        return ProviderQuotaRecord(
            provider_id=row["provider_id"],
            daily_request_limit=int(row["daily_request_limit"] or 1000),
            daily_requests_used=int(row["daily_requests_used"] or 0),
            monthly_request_limit=int(row["monthly_request_limit"]) if row["monthly_request_limit"] is not None else None,
            monthly_requests_used=int(row["monthly_requests_used"] or 0),
            rate_limit_per_minute=int(row["rate_limit_per_minute"] or 60),
            credit_balance_usd=float(row["credit_balance_usd"]) if row["credit_balance_usd"] is not None else None,
            reset_at=_parse_dt(row["reset_at"]),
            updated_at=_parse_dt(row["updated_at"]) or datetime.now(timezone.utc),
        )

    def record_quota_consumption(
        self,
        provider_id: str,
        units: int = 1,
        cost_usd: float = 0.0,
    ) -> ProviderQuotaRecord:
        quota = self.get_quota(provider_id)
        if not quota:
            quota = ProviderQuotaRecord(provider_id=provider_id)

        now = datetime.now(timezone.utc)
        new_daily_used = quota.daily_requests_used + max(0, units)
        new_monthly_used = quota.monthly_requests_used + max(0, units)
        new_credits = quota.credit_balance_usd
        if new_credits is not None and cost_usd > 0.0:
            new_credits = max(0.0, new_credits - cost_usd)

        updated_quota = ProviderQuotaRecord(
            provider_id=quota.provider_id,
            daily_request_limit=quota.daily_request_limit,
            daily_requests_used=new_daily_used,
            monthly_request_limit=quota.monthly_request_limit,
            monthly_requests_used=new_monthly_used,
            rate_limit_per_minute=quota.rate_limit_per_minute,
            credit_balance_usd=new_credits,
            reset_at=quota.reset_at,
            updated_at=now,
        )
        self.save_quota(updated_quota)
        return updated_quota

    def record_quota_usage(
        self,
        provider_id: str,
        request_count: int = 1,
        gigabytes: float = 0.0,
    ) -> ProviderQuotaRecord:
        return self.record_quota_consumption(provider_id, units=request_count, cost_usd=0.0)

    def update_quota_parameters(
        self,
        provider_id: str,
        daily_request_limit: Optional[int] = None,
        monthly_gb_limit: Optional[float] = None,
        credit_balance_usd: Optional[float] = None,
        reset_daily_requests: bool = False,
    ) -> ProviderQuotaRecord:
        quota = self.get_quota(provider_id)
        if not quota:
            quota = ProviderQuotaRecord(provider_id=provider_id)

        now = datetime.now(timezone.utc)
        new_daily_limit = daily_request_limit if daily_request_limit is not None else quota.daily_request_limit
        new_daily_used = 0 if reset_daily_requests else quota.daily_requests_used
        new_credits = credit_balance_usd if credit_balance_usd is not None else quota.credit_balance_usd

        updated = ProviderQuotaRecord(
            provider_id=quota.provider_id,
            daily_request_limit=new_daily_limit,
            daily_requests_used=new_daily_used,
            monthly_request_limit=quota.monthly_request_limit,
            monthly_requests_used=0 if reset_daily_requests else quota.monthly_requests_used,
            rate_limit_per_minute=quota.rate_limit_per_minute,
            credit_balance_usd=new_credits,
            reset_at=quota.reset_at,
            updated_at=now,
        )
        self.save_quota(updated)
        return updated

    def record_health_event(
        self,
        provider_id: str,
        success: bool,
        latency_ms: float = 0.0,
        error_message: Optional[str] = None,
    ) -> ProviderHealthRecord:
        norm = (provider_id or "").strip().lower()
        current = self.get_health(norm)
        now = datetime.now(timezone.utc)

        if not current:
            current = ProviderHealthRecord(
                provider_id=norm,
                name=norm.capitalize(),
                status=ProviderStatus.HEALTHY if success else ProviderStatus.DEGRADED,
                latency_ms=max(0.0, latency_ms),
            )

        if success:
            consecutive = 0
            # Blend latency with 0.8 / 0.2 exponential moving average
            new_latency = (current.latency_ms * 0.8) + (max(0.0, latency_ms) * 0.2)
            # Reduce error rate towards 0
            new_err_rate = max(0.0, current.error_rate_pct * 0.85)
            status = ProviderStatus.HEALTHY
            msg = "Operational and responding within SLA"
            last_succ = now
            last_fail = current.last_failure_at
        else:
            consecutive = current.consecutive_failures + 1
            new_latency = max(current.latency_ms, latency_ms)
            new_err_rate = min(100.0, (current.error_rate_pct * 0.8) + 20.0)
            if consecutive >= 3:
                status = ProviderStatus.DOWN
                msg = f"Provider outage: {error_message or 'Repeated request failures'}"
            else:
                status = ProviderStatus.DEGRADED
                msg = f"Service degraded: {error_message or 'Transient latency or errors'}"
            last_succ = current.last_success_at
            last_fail = now

        updated = ProviderHealthRecord(
            provider_id=current.provider_id,
            name=current.name,
            status=status,
            latency_ms=round(new_latency, 1),
            error_rate_pct=round(new_err_rate, 1),
            last_success_at=last_succ,
            last_failure_at=last_fail,
            consecutive_failures=consecutive,
            status_message=msg,
            endpoint_url=current.endpoint_url,
            updated_at=now,
        )
        self.save_health(updated)
        return updated
