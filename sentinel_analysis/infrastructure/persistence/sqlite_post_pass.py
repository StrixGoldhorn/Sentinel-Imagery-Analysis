"""SQLite implementation of PostPassIngestionRepository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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

    def __init__(
        self, database_path: Path | str, timeout: float = 5.0,
        max_wait_hours: float = 24.0,
    ) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self._max_wait_hours = float(max_wait_hours)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()
        self._auto_expire_jobs()

    @staticmethod
    def _record_event(conn, job_id: int, old_status: Optional[str], new_status: str, reason: str, message: Optional[str] = None) -> None:
        conn.execute(
            """
            INSERT INTO post_pass_job_events (job_id, old_status, new_status, reason, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, old_status, new_status, reason, message),
        )

    def _auto_expire_jobs(self, now: Optional[datetime] = None) -> int:
        """Transition active jobs whose configured wait window has expired."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self._max_wait_hours)
        cutoff_str = _format_dt(cutoff)
        now_str = _format_dt(now)
        timeout_msg = (
            "Wait window expired: Exceeded maximum post-pass wait window "
            f"({self._max_wait_hours} hours)"
        )
        with self._database.connection(rows=True) as conn:
            expiring_rows = [
                (int(row["id"]), str(row["status"]))
                for row in conn.execute(
                    """
                    SELECT id, status FROM post_pass_ingestions
                    WHERE status IN ('POLLING_CATALOG', 'PENDING_PASS') AND deleted_at IS NULL
                      AND COALESCE(expected_imagery_time, pass_time) <= ?
                    """,
                    (cutoff_str,),
                ).fetchall()
            ]
            # 1. Bulk SQL update based on ISO string cutoff
            conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = 'TIMED_OUT',
                    next_poll_at = NULL,
                    completed_at = COALESCE(completed_at, ?),
                    error_message = COALESCE(error_message, ?)
                WHERE status IN ('POLLING_CATALOG', 'PENDING_PASS')
                  AND deleted_at IS NULL
                  AND COALESCE(expected_imagery_time, pass_time) <= ?
                """,
                (now_str, timeout_msg, cutoff_str),
            )
            for job_id, old_status in expiring_rows:
                self._record_event(conn, job_id, old_status, "TIMED_OUT", "WAIT_WINDOW_EXPIRED", timeout_msg)
            expired_count = len(expiring_rows)
            # 2. Defensive check for any remaining active rows with non-standard date formatting
            rows = conn.execute(
                """
                SELECT id, status, pass_time, expected_imagery_time
                FROM post_pass_ingestions
                WHERE deleted_at IS NULL
                  AND status IN ('POLLING_CATALOG', 'PENDING_PASS')
                """
            ).fetchall()
            for r in rows:
                p_dt = _parse_dt(r["expected_imagery_time"]) or _parse_dt(r["pass_time"])
                if p_dt and (now - p_dt).total_seconds() > self._max_wait_hours * 3600:
                    cursor = conn.execute(
                        """
                        UPDATE post_pass_ingestions
                        SET status = 'TIMED_OUT',
                            next_poll_at = NULL,
                            completed_at = COALESCE(completed_at, ?),
                            error_message = COALESCE(error_message, ?)
                        WHERE id = ?
                        """,
                        (now_str, timeout_msg, r["id"]),
                    )
                    if cursor.rowcount == 1:
                        expired_count += 1
                        self._record_event(
                            conn,
                            int(r["id"]),
                            str(r["status"]),
                            "TIMED_OUT",
                            "WAIT_WINDOW_EXPIRED",
                            timeout_msg,
                        )
            return expired_count

    def expire_jobs(self, now: Optional[datetime] = None) -> int:
        """Run the explicit timeout reconciliation step."""
        return self._auto_expire_jobs(now)

    def configure_max_wait_hours(self, value: float) -> None:
        self._max_wait_hours = max(0.01, float(value))

    @staticmethod
    def _from_row(row) -> PostPassIngestionJob:
        keys = set(row.keys())
        return PostPassIngestionJob(
            id=row["id"],
            aoi_id=row["aoi_id"],
            pass_time=_parse_dt(row["pass_time"]) or datetime.now(timezone.utc),
            satellite=row["satellite"] or "Sentinel-1",
            orbit_direction=row["orbit_direction"],
            relative_orbit=row["relative_orbit"] if "relative_orbit" in keys else None,
            trigger_type=row["trigger_type"] if "trigger_type" in keys else "MANUAL",
            prediction_source=row["prediction_source"] if "prediction_source" in keys else None,
            workflow_id=row["workflow_id"] if "workflow_id" in keys else None,
            basis_product_id=row["basis_product_id"] if "basis_product_id" in keys else None,
            basis_acquisition_time=_parse_dt(row["basis_acquisition_time"]) if "basis_acquisition_time" in keys else None,
            basis_satellite=row["basis_satellite"] if "basis_satellite" in keys else None,
            basis_relative_orbit=row["basis_relative_orbit"] if "basis_relative_orbit" in keys else None,
            status=row["status"] or "POLLING_CATALOG",
            attempts=int(row["attempts"] or 0),
            last_polled_at=_parse_dt(row["last_polled_at"]),
            next_poll_at=_parse_dt(row["next_poll_at"]),
            scan_folder=row["scan_folder"],
            error_message=row["error_message"],
            created_at=_parse_dt(row["created_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            aoi_name=row["aoi_name"] if "aoi_name" in keys else None,
            expected_imagery_time=_parse_dt(row["expected_imagery_time"]) if "expected_imagery_time" in keys else None,
        )

    def add(self, job: PostPassIngestionJob) -> int:
        pass_time_str = _format_dt(job.pass_time)
        with self._database.connection(rows=True) as conn:
            previous = conn.execute(
                "SELECT id, status FROM post_pass_ingestions WHERE aoi_id = ? AND pass_time = ?",
                (job.aoi_id, pass_time_str),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO post_pass_ingestions (
                    aoi_id, pass_time, satellite, orbit_direction, status,
                    attempts, last_polled_at, next_poll_at, scan_folder,
                    error_message, completed_at, expected_imagery_time,
                    relative_orbit, trigger_type, prediction_source, workflow_id,
                    basis_product_id, basis_acquisition_time, basis_satellite,
                    basis_relative_orbit, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(aoi_id, pass_time) DO UPDATE SET
                    satellite = excluded.satellite,
                    orbit_direction = COALESCE(excluded.orbit_direction, post_pass_ingestions.orbit_direction),
                    relative_orbit = COALESCE(excluded.relative_orbit, post_pass_ingestions.relative_orbit),
                    trigger_type = excluded.trigger_type,
                    prediction_source = COALESCE(excluded.prediction_source, post_pass_ingestions.prediction_source),
                    workflow_id = COALESCE(excluded.workflow_id, post_pass_ingestions.workflow_id),
                    basis_product_id = COALESCE(excluded.basis_product_id, post_pass_ingestions.basis_product_id),
                    basis_acquisition_time = COALESCE(excluded.basis_acquisition_time, post_pass_ingestions.basis_acquisition_time),
                    basis_satellite = COALESCE(excluded.basis_satellite, post_pass_ingestions.basis_satellite),
                    basis_relative_orbit = COALESCE(excluded.basis_relative_orbit, post_pass_ingestions.basis_relative_orbit),
                    deleted_at = NULL,
                    expected_imagery_time = COALESCE(excluded.expected_imagery_time, post_pass_ingestions.expected_imagery_time),
                    status = CASE
                        WHEN post_pass_ingestions.status = 'PENDING_PASS' AND excluded.status = 'POLLING_CATALOG' THEN 'POLLING_CATALOG'
                        ELSE post_pass_ingestions.status
                    END,
                    next_poll_at = CASE
                        WHEN post_pass_ingestions.status = 'PENDING_PASS' AND excluded.status = 'POLLING_CATALOG' THEN excluded.next_poll_at
                        ELSE COALESCE(post_pass_ingestions.next_poll_at, excluded.next_poll_at)
                    END
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
                    _format_dt(job.expected_imagery_time),
                    job.relative_orbit,
                    job.trigger_type,
                    job.prediction_source,
                    job.workflow_id,
                    job.basis_product_id,
                    _format_dt(job.basis_acquisition_time),
                    job.basis_satellite,
                    job.basis_relative_orbit,
                ),
            )
            row = cursor.fetchone()
            if row and row["id"]:
                job_id = int(row["id"])
                current = conn.execute("SELECT status FROM post_pass_ingestions WHERE id = ?", (job_id,)).fetchone()
                current_status = str(current["status"])
                if previous is None:
                    self._record_event(conn, job_id, None, current_status, "JOB_CREATED")
                elif str(previous["status"]) != current_status:
                    self._record_event(conn, job_id, str(previous["status"]), current_status, "JOB_UPSERTED")
                return job_id
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
                WHERE p.id = ? AND p.deleted_at IS NULL
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
                WHERE p.aoi_id = ? AND p.pass_time = ? AND p.deleted_at IS NULL
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
                WHERE p.deleted_at IS NULL
                  AND p.status IN ('PENDING_PASS', 'POLLING_CATALOG', 'QUERYING_CATALOG', 'INGESTING')
                ORDER BY p.pass_time ASC
                """
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_jobs_due_for_poll(self, now: datetime) -> list[PostPassIngestionJob]:
        now_str = _format_dt(now)
        with self._database.connection(rows=True) as conn:
            pending_ids = [
                int(row["id"])
                for row in conn.execute(
                    """SELECT id FROM post_pass_ingestions
                       WHERE status = 'PENDING_PASS' AND deleted_at IS NULL
                         AND (next_poll_at IS NULL OR next_poll_at <= ?)""",
                    (now_str,),
                ).fetchall()
            ]
            # Transition any PENDING_PASS jobs whose flypast window has now completed
            conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = 'POLLING_CATALOG'
                WHERE status = 'PENDING_PASS' AND deleted_at IS NULL
                  AND (next_poll_at IS NULL OR next_poll_at <= ?)
                """,
                (now_str,),
            )
            for pending_id in pending_ids:
                self._record_event(conn, pending_id, "PENDING_PASS", "POLLING_CATALOG", "PASS_WINDOW_ENDED")
            rows = conn.execute(
                """
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                WHERE p.status = 'POLLING_CATALOG' AND p.deleted_at IS NULL
                  AND (p.next_poll_at IS NULL OR p.next_poll_at <= ?)
                ORDER BY p.pass_time ASC
                """,
                (now_str,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _recover_stale_ingestions(self, conn, now: datetime) -> None:
        """Make abandoned claims retryable after a conservative two-hour lease."""
        stale_before = _format_dt(now - timedelta(hours=2))
        stale_rows = conn.execute(
            """
            SELECT id, status FROM post_pass_ingestions
            WHERE status IN ('QUERYING_CATALOG', 'INGESTING') AND deleted_at IS NULL
              AND last_polled_at IS NOT NULL
              AND last_polled_at <= ?
              AND scan_folder IS NULL
            """,
            (stale_before,),
        ).fetchall()
        conn.execute(
            """
            UPDATE post_pass_ingestions
            SET status = 'POLLING_CATALOG',
                next_poll_at = ?,
                error_message = COALESCE(error_message, 'Recovered abandoned ingestion claim')
            WHERE status IN ('QUERYING_CATALOG', 'INGESTING') AND deleted_at IS NULL
              AND last_polled_at IS NOT NULL
              AND last_polled_at <= ?
              AND scan_folder IS NULL
            """,
            (_format_dt(now), stale_before),
        )
        for row in stale_rows:
            self._record_event(
                conn,
                int(row["id"]),
                str(row["status"]),
                "POLLING_CATALOG",
                "ABANDONED_CLAIM_RECOVERED",
            )

    def claim_jobs_due_for_poll(self, now: datetime, limit: int = 1) -> list[PostPassIngestionJob]:
        """Atomically claim a bounded number of due jobs for one worker."""
        now_str = _format_dt(now)
        with self._database.connection(rows=True) as conn:
            self._recover_stale_ingestions(conn, now)
            pending_ids = [
                int(row["id"])
                for row in conn.execute(
                    """SELECT id FROM post_pass_ingestions
                       WHERE status = 'PENDING_PASS' AND deleted_at IS NULL
                         AND (next_poll_at IS NULL OR next_poll_at <= ?)""",
                    (now_str,),
                ).fetchall()
            ]
            conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = 'POLLING_CATALOG'
                WHERE status = 'PENDING_PASS' AND deleted_at IS NULL
                  AND (next_poll_at IS NULL OR next_poll_at <= ?)
                """,
                (now_str,),
            )
            for pending_id in pending_ids:
                self._record_event(conn, pending_id, "PENDING_PASS", "POLLING_CATALOG", "PASS_WINDOW_ENDED")
            ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM post_pass_ingestions
                    WHERE status = 'POLLING_CATALOG' AND deleted_at IS NULL
                      AND (next_poll_at IS NULL OR next_poll_at <= ?)
                    ORDER BY pass_time ASC
                    LIMIT ?
                    """,
                    (now_str, max(1, min(int(limit), 20))),
                ).fetchall()
            ]
            claimed_ids: list[int] = []
            for job_id in ids:
                cursor = conn.execute(
                    """
                    UPDATE post_pass_ingestions
                    SET status = 'QUERYING_CATALOG', last_polled_at = ?, next_poll_at = NULL
                    WHERE id = ? AND status = 'POLLING_CATALOG' AND deleted_at IS NULL
                      AND (next_poll_at IS NULL OR next_poll_at <= ?)
                    """,
                    (now_str, job_id, now_str),
                )
                if cursor.rowcount == 1:
                    claimed_ids.append(job_id)
                    self._record_event(conn, job_id, "POLLING_CATALOG", "QUERYING_CATALOG", "JOB_CLAIMED")
            if not claimed_ids:
                return []
            placeholders = ",".join("?" for _ in claimed_ids)
            rows = conn.execute(
                f"""
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                WHERE p.id IN ({placeholders})
                ORDER BY p.pass_time ASC
                """,
                tuple(claimed_ids),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def claim_job(self, job_id: int, now: datetime) -> Optional[PostPassIngestionJob]:
        """Atomically claim one polling job for a manual or scheduled attempt."""
        now_str = _format_dt(now)
        with self._database.connection(rows=True) as conn:
            self._recover_stale_ingestions(conn, now)
            cursor = conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = 'QUERYING_CATALOG', last_polled_at = ?, next_poll_at = NULL
                WHERE id = ? AND status = 'POLLING_CATALOG' AND deleted_at IS NULL
                """,
                (now_str, int(job_id)),
            )
            if cursor.rowcount != 1:
                return None
            self._record_event(conn, int(job_id), "POLLING_CATALOG", "QUERYING_CATALOG", "JOB_CLAIMED")
            row = conn.execute(
                """
                SELECT p.*, a.name AS aoi_name
                FROM post_pass_ingestions p
                LEFT JOIN aoi a ON p.aoi_id = a.id
                WHERE p.id = ?
                """,
                (int(job_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def update(self, job: PostPassIngestionJob) -> None:
        if job.id is None:
            raise ValueError("Job ID is required to update post-pass ingestion job")
        with self._database.connection(rows=True) as conn:
            previous = conn.execute(
                "SELECT status, attempts, error_message FROM post_pass_ingestions WHERE id = ?",
                (job.id,),
            ).fetchone()
            cursor = conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = ?,
                    attempts = ?,
                    last_polled_at = ?,
                    next_poll_at = ?,
                    scan_folder = ?,
                    error_message = ?,
                    completed_at = ?,
                    expected_imagery_time = ?
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
                    _format_dt(job.expected_imagery_time),
                    job.id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Post-pass ingestion job not found: {job.id}")
            old_status = str(previous["status"]) if previous else None
            if old_status != job.status:
                reasons = {
                    "POLLING_CATALOG": "POLL_SCHEDULED",
                    "QUERYING_CATALOG": "CATALOG_QUERY_STARTED",
                    "INGESTING": "IMAGERY_INGESTION_STARTED",
                    "COMPLETED": "IMAGERY_INGESTED",
                    "TIMED_OUT": "WAIT_WINDOW_EXPIRED",
                    "FAILED": "JOB_FAILED",
                }
                self._record_event(conn, job.id, old_status, job.status, reasons.get(job.status, "STATUS_UPDATED"), job.error_message)
            elif (
                previous is not None
                and job.status == "POLLING_CATALOG"
                and job.attempts > int(previous["attempts"] or 0)
            ):
                self._record_event(
                    conn, job.id, old_status, job.status,
                    "POLL_ATTEMPT_FAILED" if job.error_message else "CATALOG_NO_MATCH",
                    job.error_message,
                )

    def get_stats(self) -> dict[str, int]:
        with self._database.connection(rows=True) as conn:
            cursor = conn.execute(
                """
                SELECT 
                    SUM(CASE WHEN status = 'POLLING_CATALOG' THEN 1 ELSE 0 END) AS polling,
                    SUM(CASE WHEN status = 'PENDING_PASS' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'INGESTING' THEN 1 ELSE 0 END) AS ingesting,
                    SUM(CASE WHEN status = 'QUERYING_CATALOG' THEN 1 ELSE 0 END) AS querying,
                    SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status IN ('TIMED_OUT', 'WAIT_EXPIRED') THEN 1 ELSE 0 END) AS timed_out,
                    COUNT(*) AS total
                FROM post_pass_ingestions
                WHERE deleted_at IS NULL
                """
            )
            row = cursor.fetchone()
            if row:
                failed_direct = int(row["failed"] or 0)
                timed_out_count = int(row["timed_out"] or 0)
                return {
                    "polling": int(row["polling"] or 0),
                    "pending": int(row["pending"] or 0),
                    "ingesting": int(row["ingesting"] or 0),
                    "querying": int(row["querying"] or 0),
                    "completed": int(row["completed"] or 0),
                    "failed": failed_direct,
                    "timed_out": timed_out_count,
                    "total": int(row["total"] or 0),
                }
            return {
                "polling": 0, "pending": 0, "querying": 0, "ingesting": 0,
                "completed": 0, "failed": 0, "timed_out": 0, "total": 0,
            }

    def list(
        self,
        limit: int = 500,
        status: Optional[str] = None,
        aoi_id: Optional[int] = None,
    ) -> list[PostPassIngestionJob]:
        limit_val = max(1, min(int(limit), 2000))
        where_clauses = ["p.deleted_at IS NULL"]
        params: list[object] = []
        if status:
            norm_status = status.upper().strip()
            if norm_status in ("TIMED_OUT", "WAIT_EXPIRED"):
                where_clauses.append("p.status IN ('TIMED_OUT', 'WAIT_EXPIRED')")
            else:
                where_clauses.append("p.status = ?")
                params.append(norm_status)
        if aoi_id is not None:
            where_clauses.append("p.aoi_id = ?")
            params.append(int(aoi_id))

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"""
            SELECT p.*, a.name AS aoi_name
            FROM post_pass_ingestions p
            LEFT JOIN aoi a ON p.aoi_id = a.id
            {where_sql}
            ORDER BY 
                CASE 
                    WHEN p.status = 'INGESTING' THEN 1
                    WHEN p.status = 'QUERYING_CATALOG' THEN 2
                    WHEN p.status = 'POLLING_CATALOG' THEN 3
                    WHEN p.status = 'PENDING_PASS' THEN 4
                    ELSE 5
                END ASC,
                p.pass_time DESC
            LIMIT ?
        """
        params.append(limit_val)
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, job_id: int) -> None:
        with self._database.connection(rows=True) as conn:
            row = conn.execute(
                "SELECT status FROM post_pass_ingestions WHERE id = ? AND deleted_at IS NULL",
                (int(job_id),),
            ).fetchone()
            if row is None:
                return
            status = str(row["status"])
            if status in {"PENDING_PASS", "POLLING_CATALOG", "QUERYING_CATALOG", "INGESTING"}:
                raise ValueError("Active post-pass ingestion jobs cannot be deleted")
            self._record_event(conn, int(job_id), status, status, "JOB_DELETED")
            conn.execute(
                "UPDATE post_pass_ingestions SET deleted_at = ? WHERE id = ?",
                (_format_dt(datetime.now(timezone.utc)), int(job_id)),
            )

    def reset_for_retry(self, job_id: int, now: datetime) -> Optional[PostPassIngestionJob]:
        """Atomically reset a terminal job, returning None if state changed."""
        with self._database.connection(rows=True) as conn:
            old = conn.execute(
                "SELECT status FROM post_pass_ingestions WHERE id = ? AND deleted_at IS NULL",
                (int(job_id),),
            ).fetchone()
            cursor = conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = 'POLLING_CATALOG', attempts = 0, next_poll_at = ?,
                    last_polled_at = NULL, completed_at = NULL, error_message = NULL,
                    scan_folder = NULL
                WHERE id = ? AND deleted_at IS NULL
                  AND status IN ('FAILED', 'TIMED_OUT', 'WAIT_EXPIRED')
                """,
                (_format_dt(now), int(job_id)),
            )
            if cursor.rowcount != 1:
                return None
            self._record_event(
                conn, int(job_id), str(old["status"]) if old else None,
                "POLLING_CATALOG", "MANUAL_RETRY",
            )
            row = conn.execute(
                "SELECT p.*, a.name AS aoi_name FROM post_pass_ingestions p LEFT JOIN aoi a ON p.aoi_id = a.id WHERE p.id = ?",
                (int(job_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def try_acquire_scheduler_lease(
        self, name: str, owner_id: str, now: datetime, ttl_seconds: float = 90.0
    ) -> bool:
        expires_at = now + timedelta(seconds=max(5.0, float(ttl_seconds)))
        with self._database.connection(rows=True) as conn:
            conn.execute(
                """
                INSERT INTO scheduler_leases(name, owner_id, lease_expires_at, heartbeat_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    lease_expires_at = excluded.lease_expires_at,
                    heartbeat_at = excluded.heartbeat_at
                WHERE scheduler_leases.owner_id = excluded.owner_id
                   OR scheduler_leases.lease_expires_at <= excluded.heartbeat_at
                """,
                (name, owner_id, _format_dt(expires_at), _format_dt(now)),
            )
            row = conn.execute("SELECT owner_id FROM scheduler_leases WHERE name = ?", (name,)).fetchone()
        return bool(row and row["owner_id"] == owner_id)

    def release_scheduler_lease(self, name: str, owner_id: str) -> None:
        with self._database.connection() as conn:
            conn.execute("DELETE FROM scheduler_leases WHERE name = ? AND owner_id = ?", (name, owner_id))

    def claim_automatic_ais_tick(
        self, aoi_id: int, pass_time: datetime, tick_time: datetime, post_pass_job_id: int
    ) -> bool:
        tick_bucket = tick_time.astimezone(timezone.utc).replace(second=0, microsecond=0)
        with self._database.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO automatic_ais_runs
                    (aoi_id, pass_time, tick_time, status, post_pass_job_id)
                VALUES (?, ?, ?, 'RUNNING', ?)
                ON CONFLICT(aoi_id, pass_time, tick_time) DO UPDATE SET
                    status = 'RUNNING', post_pass_job_id = excluded.post_pass_job_id,
                    error_message = NULL, completed_at = NULL
                WHERE automatic_ais_runs.status = 'FAILED'
                """,
                (aoi_id, _format_dt(pass_time), _format_dt(tick_bucket), post_pass_job_id),
            )
        return cursor.rowcount == 1

    def finish_automatic_ais_tick(
        self, aoi_id: int, pass_time: datetime, tick_time: datetime,
        error_message: Optional[str] = None,
    ) -> None:
        tick_bucket = tick_time.astimezone(timezone.utc).replace(second=0, microsecond=0)
        with self._database.connection() as conn:
            conn.execute(
                """
                UPDATE automatic_ais_runs
                SET status = ?, error_message = ?, completed_at = ?
                WHERE aoi_id = ? AND pass_time = ? AND tick_time = ?
                """,
                (
                    "FAILED" if error_message else "COMPLETED", error_message,
                    _format_dt(datetime.now(timezone.utc)), aoi_id,
                    _format_dt(pass_time), _format_dt(tick_bucket),
                ),
            )

    def list_events(self, job_id: int, limit: int = 100) -> list[dict[str, object]]:
        limit_value = max(1, min(int(limit), 500))
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(
                """
                SELECT id, job_id, old_status, new_status, reason, message, created_at
                FROM post_pass_job_events
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(job_id), limit_value),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_poll_attempts(self, job_id: int, limit: int = 20) -> list[dict[str, object]]:
        """Return completed catalog retrieval attempts in chronological order.

        A claim marks the start of a catalog retrieval.  The following state
        transition out of QUERYING_CATALOG marks its completion, whether the
        catalog had no match, returned an error, or found imagery to ingest.
        Incomplete claims are intentionally omitted because they may still be
        running (or may be recovered by the stale-claim lease).
        """
        limit_value = max(1, min(int(limit), 100))
        events = list(reversed(self.list_events(job_id, limit=500)))
        completed: list[dict[str, object]] = []
        active_attempt: Optional[dict[str, object]] = None

        for event in events:
            reason = str(event.get("reason") or "")
            if reason == "JOB_CLAIMED":
                # A new claim without a closing transition is incomplete and
                # must not be presented as a completed previous attempt.
                active_attempt = {
                    "started_at": event.get("created_at"),
                    "completed_at": None,
                    "reason": None,
                    "message": None,
                }
                continue

            if (
                active_attempt is not None
                and event.get("old_status") == "QUERYING_CATALOG"
                and event.get("new_status") != "QUERYING_CATALOG"
            ):
                active_attempt["completed_at"] = event.get("created_at")
                active_attempt["reason"] = reason or None
                active_attempt["message"] = event.get("message")
                completed.append(active_attempt)
                active_attempt = None

        # Do not include an unclosed final claim: it is the current attempt.
        numbered = list(enumerate(completed, start=1))[-limit_value:]
        return [
            {
                "attempt": index,
                "started_at": _format_dt(_parse_dt(attempt.get("started_at"))),
                "completed_at": _format_dt(_parse_dt(attempt.get("completed_at"))),
                "reason": attempt.get("reason"),
                "message": attempt.get("message"),
            }
            for index, attempt in numbered
        ]
