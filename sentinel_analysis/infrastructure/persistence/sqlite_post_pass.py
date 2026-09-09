"""SQLite implementation of PostPassIngestionRepository."""

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

    def __init__(self, database_path: Path | str, timeout: float = 5.0) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()
        self._auto_expire_jobs()

    def _auto_expire_jobs(self, now: Optional[datetime] = None) -> int:
        """Transitions any active post-pass ingestion jobs whose 24h wait window has expired to TIMED_OUT."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        cutoff_str = _format_dt(cutoff)
        now_str = _format_dt(now)
        timeout_msg = "Wait window expired: Exceeded maximum post-pass wait window (24.0 hours)"
        with self._database.connection(rows=True) as conn:
            # 1. Bulk SQL update based on ISO string cutoff
            conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = 'TIMED_OUT',
                    next_poll_at = NULL,
                    completed_at = COALESCE(completed_at, ?),
                    error_message = COALESCE(error_message, ?)
                WHERE status IN ('POLLING_CATALOG', 'PENDING_PASS')
                  AND COALESCE(expected_imagery_time, pass_time) <= ?
                """,
                (now_str, timeout_msg, cutoff_str),
            )
            # 2. Defensive check for any remaining active rows with non-standard date formatting
            rows = conn.execute(
                """
                SELECT id, pass_time, expected_imagery_time
                FROM post_pass_ingestions
                WHERE status IN ('POLLING_CATALOG', 'PENDING_PASS')
                """
            ).fetchall()
            for r in rows:
                p_dt = _parse_dt(r["expected_imagery_time"]) or _parse_dt(r["pass_time"])
                if p_dt and (now - p_dt).total_seconds() > 24 * 3600:
                    conn.execute(
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
            expected_imagery_time=_parse_dt(row["expected_imagery_time"]) if "expected_imagery_time" in row.keys() else None,
        )

    def add(self, job: PostPassIngestionJob) -> int:
        pass_time_str = _format_dt(job.pass_time)
        with self._database.connection(rows=True) as conn:
            cursor = conn.execute(
                """
                INSERT INTO post_pass_ingestions (
                    aoi_id, pass_time, satellite, orbit_direction, status,
                    attempts, last_polled_at, next_poll_at, scan_folder,
                    error_message, completed_at, expected_imagery_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(aoi_id, pass_time) DO UPDATE SET
                    satellite = excluded.satellite,
                    orbit_direction = COALESCE(excluded.orbit_direction, post_pass_ingestions.orbit_direction),
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
                ),
            )
            row = cursor.fetchone()
            if row and row["id"]:
                return int(row["id"])
            if cursor.lastrowid is not None:
                return int(cursor.lastrowid)
            raise RuntimeError("Failed to insert or retrieve post-pass ingestion job ID")

    def get(self, job_id: int) -> Optional[PostPassIngestionJob]:
        self._auto_expire_jobs()
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
        self._auto_expire_jobs()
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
        self._auto_expire_jobs()
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
        self._auto_expire_jobs(now)
        now_str = _format_dt(now)
        with self._database.connection(rows=True) as conn:
            # Transition any PENDING_PASS jobs whose flypast window has now completed
            conn.execute(
                """
                UPDATE post_pass_ingestions
                SET status = 'POLLING_CATALOG'
                WHERE status = 'PENDING_PASS'
                  AND (next_poll_at IS NULL OR next_poll_at <= ?)
                """,
                (now_str,),
            )
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

    def get_stats(self) -> dict[str, int]:
        self._auto_expire_jobs()
        with self._database.connection(rows=True) as conn:
            cursor = conn.execute(
                """
                SELECT 
                    SUM(CASE WHEN status = 'POLLING_CATALOG' THEN 1 ELSE 0 END) AS polling,
                    SUM(CASE WHEN status = 'PENDING_PASS' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'INGESTING' THEN 1 ELSE 0 END) AS ingesting,
                    SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status IN ('TIMED_OUT', 'WAIT_EXPIRED') THEN 1 ELSE 0 END) AS timed_out,
                    COUNT(*) AS total
                FROM post_pass_ingestions
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
                    "completed": int(row["completed"] or 0),
                    "failed": failed_direct + timed_out_count,
                    "timed_out": timed_out_count,
                    "total": int(row["total"] or 0),
                }
            return {
                "polling": 0, "pending": 0, "ingesting": 0,
                "completed": 0, "failed": 0, "timed_out": 0, "total": 0,
            }

    def list(
        self,
        limit: int = 500,
        status: Optional[str] = None,
        aoi_id: Optional[int] = None,
    ) -> list[PostPassIngestionJob]:
        self._auto_expire_jobs()
        limit_val = max(1, min(int(limit), 2000))
        where_clauses = []
        params: list[object] = []
        if status:
            norm_status = status.upper().strip()
            if norm_status in ("TIMED_OUT", "WAIT_EXPIRED"):
                where_clauses.append("p.status IN ('TIMED_OUT', 'WAIT_EXPIRED')")
            elif norm_status == "FAILED":
                where_clauses.append("p.status IN ('FAILED', 'TIMED_OUT', 'WAIT_EXPIRED')")
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
                    WHEN p.status = 'POLLING_CATALOG' THEN 2
                    WHEN p.status = 'PENDING_PASS' THEN 3
                    ELSE 4
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
            conn.execute("DELETE FROM post_pass_ingestions WHERE id = ?", (job_id,))
