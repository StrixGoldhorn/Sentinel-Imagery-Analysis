"""SQLite implementation of PostPassIngestionRepository."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sentinel_analysis.domain.entities import PostPassIngestionJob
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


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


def _format_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class SQLitePostPassIngestionRepository:
    """Stores and queries post-pass satellite scan ingestion tasks in SQLite."""

    def __init__(self, database_path: Path | str, timeout: float = 5.0) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()

    @staticmethod
    def _from_row(row) -> PostPassIngestionJob:
        return PostPassIngestionJob(
            id=row["id"],
            aoi_id=row["aoi_id"],
            pass_time=_parse_dt(row["pass_time"]) or datetime.now(timezone.utc),
            satellite=row["satellite"] or "Sentinel-1",
            orbit_direction=row["orbit_direction"],
            status=row["status"] or "POLLING_CATALOG",
            attempts=int(row["attempts"] or 0),
            last_polled_at=_parse_dt(row["last_polled_at"]),
            next_poll_at=_parse_dt(row["next_poll_at"]),
            scan_folder=row["scan_folder"],
            error_message=row["error_message"],
            created_at=_parse_dt(row["created_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            aoi_name=row["aoi_name"] if "aoi_name" in row.keys() else None,
        )

    def add(self, job: PostPassIngestionJob) -> int:
        pass_time_str = _format_dt(job.pass_time)
        with self._database.connection(rows=True) as conn:
            cursor = conn.execute(
                """
                INSERT INTO post_pass_ingestions (
                    aoi_id, pass_time, satellite, orbit_direction, status,
                    attempts, last_polled_at, next_poll_at, scan_folder,
                    error_message, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(aoi_id, pass_time) DO UPDATE SET
                    satellite = excluded.satellite,
                    orbit_direction = COALESCE(excluded.orbit_direction, post_pass_ingestions.orbit_direction)
                RETURNING id
                """,
                (
                    job.aoi_id,
                    pass_time_str,
                    job.satellite,
                    job.orbit_direction,
                    job.status,
                    job.attempts,
                    _format_dt(job.last_polled_at),
                    _format_dt(job.next_poll_at),
                    job.scan_folder,
                    job.error_message,
                    _format_dt(job.completed_at),
                ),
            )
            row = cursor.fetchone()
            if row and row["id"]:
                return int(row["id"])
            if cursor.lastrowid is not None:
                return int(cursor.lastrowid)
            raise RuntimeError("Failed to insert or retrieve post-pass ingestion job ID")

    def get(self, job_id: int) -> Optional[PostPassIngestionJob]:
        with self._database.connection(rows=True) as conn:
            row = conn.execute(
                """
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                WHERE p.id = ?
                """,
                (job_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def find_by_aoi_and_pass(self, aoi_id: int, pass_time: datetime) -> Optional[PostPassIngestionJob]:
        pass_time_str = _format_dt(pass_time)
        with self._database.connection(rows=True) as conn:
            row = conn.execute(
                """
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                WHERE p.aoi_id = ? AND p.pass_time = ?
                """,
                (aoi_id, pass_time_str),
            ).fetchone()
        return self._from_row(row) if row else None

    def get_active_jobs(self) -> list[PostPassIngestionJob]:
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(
                """
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                WHERE p.status IN ('PENDING_PASS', 'POLLING_CATALOG', 'INGESTING')
                ORDER BY p.pass_time ASC
                """
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_jobs_due_for_poll(self, now: datetime) -> list[PostPassIngestionJob]:
        now_str = _format_dt(now)
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(
                """
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                WHERE p.status = 'POLLING_CATALOG'
                  AND (p.next_poll_at IS NULL OR p.next_poll_at <= ?)
                ORDER BY p.pass_time ASC
                """,
                (now_str,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def update(self, job: PostPassIngestionJob) -> None:
        if job.id is None:
            raise ValueError("Job ID is required to update post-pass ingestion job")
        with self._database.connection(rows=True) as conn:
            conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = ?,
                    attempts = ?,
                    last_polled_at = ?,
                    next_poll_at = ?,
                    scan_folder = ?,
                    error_message = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    job.status,
                    job.attempts,
                    _format_dt(job.last_polled_at),
                    _format_dt(job.next_poll_at),
                    job.scan_folder,
                    job.error_message,
                    _format_dt(job.completed_at),
                    job.id,
                ),
            )

    def list(self, limit: int = 50) -> list[PostPassIngestionJob]:
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(
                """
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                ORDER BY p.pass_time DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, job_id: int) -> None:
        with self._database.connection(rows=True) as conn:
            conn.execute("DELETE FROM post_pass_ingestions WHERE id = ?", (job_id,))
