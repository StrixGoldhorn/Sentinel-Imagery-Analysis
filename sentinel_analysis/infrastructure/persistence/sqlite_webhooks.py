"""SQLite persistence implementation for maritime alert webhooks."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sentinel_analysis.application.ports.alerting import WebhookRepository
from sentinel_analysis.domain.entities import WebhookConfig
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


def _format_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class SQLiteWebhookRepository(WebhookRepository):
    """Stores webhook subscriber configurations in SQLite."""

    def __init__(self, database_path: Path | str, timeout: float = 5.0) -> None:
        self._database = SQLiteDatabase(database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        """Create the webhooks table and indexes if they do not exist."""
        with self._database.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS maritime_webhooks (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    service_type TEXT NOT NULL DEFAULT 'generic',
                    name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    min_severity TEXT NOT NULL DEFAULT 'INFO',
                    secret_token TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_webhooks_enabled ON maritime_webhooks(enabled)"
            )

    @staticmethod
    def _from_row(row) -> WebhookConfig:
        return WebhookConfig(
            id=row["id"],
            url=row["url"],
            service_type=row["service_type"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            min_severity=row["min_severity"],
            secret_token=row["secret_token"] if row["secret_token"] else None,
            created_at=_parse_dt(row["created_at"]),
        )

    def save(self, webhook: WebhookConfig) -> None:
        """Create or update a webhook configuration."""
        created_at_str = _format_dt(webhook.created_at)
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO maritime_webhooks (
                    id, url, service_type, name, enabled, min_severity, secret_token, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url,
                    service_type = excluded.service_type,
                    name = excluded.name,
                    enabled = excluded.enabled,
                    min_severity = excluded.min_severity,
                    secret_token = excluded.secret_token
                """,
                (
                    webhook.id,
                    webhook.url,
                    webhook.service_type,
                    webhook.name,
                    1 if webhook.enabled else 0,
                    webhook.min_severity,
                    webhook.secret_token,
                    created_at_str,
                ),
            )

    def get(self, webhook_id: str) -> Optional[WebhookConfig]:
        """Retrieve a webhook configuration by ID."""
        with self._database.connection(rows=True) as conn:
            row = conn.execute(
                "SELECT * FROM maritime_webhooks WHERE id = ?",
                (webhook_id,),
            ).fetchone()
            if row is None:
                return None
            return self._from_row(row)

    def list(self, enabled_only: bool = False) -> list[WebhookConfig]:
        """List configured webhooks."""
        query = "SELECT * FROM maritime_webhooks"
        params = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name ASC, created_at DESC"

        with self._database.connection(rows=True) as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._from_row(row) for row in rows]

    def delete(self, webhook_id: str) -> bool:
        """Delete a webhook configuration by ID."""
        with self._database.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM maritime_webhooks WHERE id = ?",
                (webhook_id,),
            )
            return cursor.rowcount > 0
