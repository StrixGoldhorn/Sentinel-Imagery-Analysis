from collections.abc import Iterable
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from sentinel_analysis.domain.entities import AISRecord, BoundingBox
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


def _normalize_utc_iso(val: object) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("Z") or ("+" in s[10:] or ("-" in s[10:] and len(s) > 16)):
        return s
    return s.replace(" ", "T") + "Z"


class SQLiteAISRepository:
    def __init__(self, database_path: Path | str, timeout: float = 5) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()
        try:
            self.reconcile_stale_scraper_logs()
        except Exception:
            pass

    def save_records(self, records: Iterable[AISRecord], source_plugin: str) -> int:
        if not isinstance(source_plugin, str) or not source_plugin.strip():
            raise ValueError("AIS source plugin name is required")
        source_plugin = source_plugin.strip()
        inserted = 0
        with self._database.connection() as connection:
            for record in records:
                vessel = record.vessel
                position = record.position
                connection.execute(
                    """
                    INSERT INTO vessels (imo, mmsi, vessel_name, vessel_type, callsign)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(imo, mmsi) DO UPDATE SET
                        vessel_name=excluded.vessel_name,
                        vessel_type=excluded.vessel_type,
                        callsign=excluded.callsign
                    """,
                    (vessel.imo, vessel.mmsi, vessel.name, vessel.vessel_type, vessel.callsign),
                )
                row = connection.execute(
                    "SELECT id FROM vessels WHERE imo = ? AND mmsi = ?",
                    (vessel.imo, vessel.mmsi),
                ).fetchone()
                if row is None:
                    raise sqlite3.IntegrityError("Unable to resolve persisted vessel")
                connection.execute(
                    """
                    INSERT INTO vessel_locations
                    (vessel_id, latitude, longitude, speed, heading, timestamp, source_plugin)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row[0], position.latitude, position.longitude,
                        position.speed, position.heading,
                        position.timestamp.isoformat(), source_plugin,
                    ),
                )
                inserted += 1
        return inserted

    def log_trigger(
        self,
        plugin_name: str,
        trigger_reason: str,
        status: str = "RUNNING",
    ) -> int:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("AIS plugin name is required")
        norm_status = status.upper().strip()
        if norm_status not in {"RUNNING", "TRIGGERED", "SUCCESS", "FAILED", "COOLDOWN_SKIPPED", "DISABLED_SKIPPED"}:
            norm_status = "RUNNING"
        reason_val = trigger_reason.strip() if isinstance(trigger_reason, str) and trigger_reason.strip() else "Manual / Unspecified"
        with self._database.connection() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO scraper_logs
                    (plugin_name, status, records_inserted, error_message, trigger_reason)
                    VALUES (?, ?, 0, 'In progress...', ?)
                    """,
                    (plugin_name.strip(), norm_status, reason_val),
                )
            except sqlite3.OperationalError:
                cursor = connection.execute(
                    """
                    INSERT INTO scraper_logs
                    (plugin_name, status, records_inserted, error_message)
                    VALUES (?, ?, 0, 'In progress...')
                    """,
                    (plugin_name.strip(), norm_status),
                )
            return cursor.lastrowid  # type: ignore[no-any-return]

    def update_execution_log(
        self,
        log_id: int,
        status: str,
        records_inserted: int = 0,
        error_message: str | None = None,
    ) -> None:
        norm_status = status.upper().strip()
        if norm_status not in {"SUCCESS", "FAILED", "COOLDOWN_SKIPPED", "DISABLED_SKIPPED", "RUNNING", "TRIGGERED"}:
            raise ValueError(f"Invalid execution status: {status}")
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE scraper_logs
                SET status = ?, records_inserted = ?, error_message = ?
                WHERE id = ?
                """,
                (norm_status, max(0, int(records_inserted)), error_message, log_id),
            )

    def log_execution(
        self,
        plugin_name: str,
        status: str,
        records_inserted: int,
        error_message: str | None = None,
        trigger_reason: str | None = None,
    ) -> None:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("AIS plugin name is required")
        norm_status = status.upper().strip()
        if norm_status not in {"SUCCESS", "FAILED", "COOLDOWN_SKIPPED", "DISABLED_SKIPPED", "RUNNING", "TRIGGERED"}:
            raise ValueError(f"Invalid execution status: AIS execution status must be SUCCESS, FAILED, COOLDOWN_SKIPPED, DISABLED_SKIPPED, or RUNNING (got {status})")
        if isinstance(records_inserted, bool) or not isinstance(records_inserted, int) or records_inserted < 0:
            raise ValueError("Inserted record count must be a non-negative integer")
        reason_val = trigger_reason.strip() if isinstance(trigger_reason, str) and trigger_reason.strip() else "Manual / Unspecified"
        with self._database.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO scraper_logs
                    (plugin_name, status, records_inserted, error_message, trigger_reason)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (plugin_name.strip(), norm_status, records_inserted, error_message, reason_val),
                )
            except sqlite3.OperationalError:
                connection.execute(
                    """
                    INSERT INTO scraper_logs
                    (plugin_name, status, records_inserted, error_message)
                    VALUES (?, ?, ?, ?)
                    """,
                    (plugin_name.strip(), norm_status, records_inserted, error_message),
                )

    def get_timeline_bounds(self) -> dict[str, object]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM vessel_locations"
            ).fetchone()
            if row and row[0] is not None and row[1] is not None:
                return {
                    "min_timestamp": _normalize_utc_iso(row[0]),
                    "max_timestamp": _normalize_utc_iso(row[1]),
                    "total_records": row[2],
                    "count": row[2],
                }
            return {
                "min_timestamp": None,
                "max_timestamp": None,
                "total_records": 0,
                "count": 0,
            }

    def get_vessel_positions(
        self,
        bbox: BoundingBox | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        limit: int = 500,
        latest_only: bool = True,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 2000))
        params: list[object] = []
        where_conditions: list[str] = []

        if bbox is not None:
            where_conditions.append("vl.longitude >= ? AND vl.latitude >= ? AND vl.longitude <= ? AND vl.latitude <= ?")
            params.extend([bbox.min_longitude, bbox.min_latitude, bbox.max_longitude, bbox.max_latitude])

        if time_range is not None:
            start, end = time_range
            if start is not None:
                where_conditions.append("vl.timestamp >= ?")
                params.append(start.isoformat())
            if end is not None:
                where_conditions.append("vl.timestamp <= ?")
                params.append(end.isoformat())

        where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""

        if latest_only:
            query = f"""
                WITH ranked AS (
                    SELECT 
                        v.id as vessel_id,
                        v.imo,
                        v.mmsi,
                        v.vessel_name,
                        v.vessel_type,
                        v.callsign,
                        vl.latitude,
                        vl.longitude,
                        vl.speed,
                        vl.heading,
                        vl.timestamp,
                        vl.source_plugin,
                        ROW_NUMBER() OVER (
                            PARTITION BY v.mmsi 
                            ORDER BY vl.timestamp DESC, vl.id DESC
                        ) as rn
                    FROM vessel_locations vl
                    JOIN vessels v ON vl.vessel_id = v.id
                    {where_clause}
                )
                SELECT 
                    vessel_id, imo, mmsi, vessel_name, vessel_type, callsign,
                    latitude, longitude, speed, heading, timestamp, source_plugin
                FROM ranked
                WHERE rn = 1
                ORDER BY timestamp DESC
                LIMIT ?
            """
        else:
            query = f"""
                SELECT 
                    v.id as vessel_id,
                    v.imo,
                    v.mmsi,
                    v.vessel_name,
                    v.vessel_type,
                    v.callsign,
                    vl.latitude,
                    vl.longitude,
                    vl.speed,
                    vl.heading,
                    vl.timestamp,
                    vl.source_plugin
                FROM vessel_locations vl
                JOIN vessels v ON vl.vessel_id = v.id
                {where_clause}
                ORDER BY vl.timestamp DESC
                LIMIT ?
            """
        params.append(limit)

        results: list[dict] = []
        with self._database.connection() as connection:
            cursor = connection.execute(query, tuple(params))
            for row in cursor.fetchall():
                results.append({
                    "vessel_id": row[0],
                    "imo": row[1],
                    "mmsi": row[2],
                    "name": row[3] or f"MMSI: {row[2]}",
                    "type": row[4] or "Unspecified",
                    "callsign": row[5],
                    "latitude": float(row[6]),
                    "longitude": float(row[7]),
                    "speed": float(row[8]) if row[8] is not None else None,
                    "heading": float(row[9]) if row[9] is not None else None,
                    "timestamp": _normalize_utc_iso(row[10]),
                    "source_plugin": row[11],
                })
        return results

    def get_vessel_by_id(self, vessel_id: int) -> dict | None:
        with self._database.connection() as connection:
            vessel_row = connection.execute(
                "SELECT id, imo, mmsi, vessel_name, vessel_type, callsign, created_at FROM vessels WHERE id = ?",
                (vessel_id,),
            ).fetchone()
            if not vessel_row:
                return None

            loc_row = connection.execute(
                """
                SELECT latitude, longitude, speed, heading, timestamp, source_plugin
                FROM vessel_locations
                WHERE vessel_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (vessel_id,),
            ).fetchone()

            return {
                "id": vessel_row[0],
                "vessel_id": vessel_row[0],
                "imo": vessel_row[1],
                "mmsi": vessel_row[2],
                "name": vessel_row[3] or f"MMSI: {vessel_row[2]}",
                "vessel_name": vessel_row[3],
                "type": vessel_row[4] or "Unspecified",
                "vessel_type": vessel_row[4],
                "callsign": vessel_row[5],
                "created_at": _normalize_utc_iso(vessel_row[6]),
                "latitude": float(loc_row[0]) if loc_row and loc_row[0] is not None else None,
                "longitude": float(loc_row[1]) if loc_row and loc_row[1] is not None else None,
                "speed": float(loc_row[2]) if loc_row and loc_row[2] is not None else None,
                "heading": float(loc_row[3]) if loc_row and loc_row[3] is not None else None,
                "timestamp": _normalize_utc_iso(loc_row[4]) if loc_row else None,
                "source_plugin": loc_row[5] if loc_row else None,
            }

    def update_vessel(
        self,
        vessel_id: int,
        name: str | None = None,
        vessel_type: str | None = None,
        callsign: str | None = None,
        imo: str | None = None,
    ) -> dict | None:
        fields = []
        params = []
        if name is not None:
            fields.append("vessel_name = ?")
            params.append(name.strip() if isinstance(name, str) and name.strip() else None)
        if vessel_type is not None:
            fields.append("vessel_type = ?")
            params.append(vessel_type.strip() if isinstance(vessel_type, str) and vessel_type.strip() else None)
        if callsign is not None:
            fields.append("callsign = ?")
            params.append(callsign.strip() if isinstance(callsign, str) and callsign.strip() else None)
        if imo is not None:
            fields.append("imo = ?")
            cleaned_imo = imo.strip() if isinstance(imo, str) and imo.strip() else None
            if not cleaned_imo:
                raise ValueError("IMO cannot be empty")
            params.append(cleaned_imo)

        with self._database.connection() as connection:
            row = connection.execute("SELECT id FROM vessels WHERE id = ?", (vessel_id,)).fetchone()
            if not row:
                return None
            if fields:
                params.append(vessel_id)
                connection.execute(
                    f"UPDATE vessels SET {', '.join(fields)} WHERE id = ?",
                    tuple(params),
                )
        return self.get_vessel_by_id(vessel_id)

    def get_scraper_config(self, plugin_name: str) -> dict | None:
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                SELECT plugin_name, enabled, description, tag, config_json, cooldown_until, consecutive_failures, last_failure_reason, updated_at
                FROM scraper_config WHERE plugin_name = ?
                """,
                (plugin_name,),
            )
            row = cursor.fetchone()
            if row:
                config_data = {}
                if row[4]:
                    try:
                        config_data = json.loads(row[4])
                    except Exception:
                        config_data = {}
                return {
                    "plugin_name": row[0],
                    "enabled": bool(row[1]),
                    "description": row[2],
                    "tag": row[3],
                    "config": config_data,
                    "cooldown_until": _normalize_utc_iso(row[5]),
                    "consecutive_failures": int(row[6] or 0),
                    "last_failure_reason": row[7],
                    "updated_at": _normalize_utc_iso(row[8]),
                }
        return None

    def get_scraper_detail(self, plugin_name: str) -> dict | None:
        detail = self.get_scraper_config(plugin_name)
        if detail is not None:
            return detail
        return {
            "plugin_name": plugin_name,
            "enabled": True,
            "description": None,
            "tag": None,
            "config": {},
            "cooldown_until": None,
            "consecutive_failures": 0,
            "last_failure_reason": None,
            "updated_at": None,
        }

    def get_all_scraper_configs(self) -> dict[str, bool]:
        configs: dict[str, bool] = {}
        with self._database.connection() as connection:
            cursor = connection.execute("SELECT plugin_name, enabled FROM scraper_config")
            for row in cursor.fetchall():
                configs[row[0]] = bool(row[1])
        return configs

    def get_all_scraper_details(self) -> dict[str, dict]:
        details: dict[str, dict] = {}
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                SELECT plugin_name, enabled, description, tag, config_json, cooldown_until, consecutive_failures, last_failure_reason, updated_at
                FROM scraper_config
                """
            )
            for row in cursor.fetchall():
                config_data = {}
                if row[4]:
                    try:
                        config_data = json.loads(row[4])
                    except Exception:
                        config_data = {}
                details[row[0]] = {
                    "plugin_name": row[0],
                    "enabled": bool(row[1]),
                    "description": row[2],
                    "tag": row[3],
                    "config": config_data,
                    "cooldown_until": row[5],
                    "consecutive_failures": int(row[6] or 0),
                    "last_failure_reason": row[7],
                    "updated_at": row[8],
                }
        return details

    def set_scraper_config(self, plugin_name: str, enabled: bool) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO scraper_config (plugin_name, enabled, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(plugin_name) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (plugin_name, 1 if enabled else 0),
            )

    def update_scraper_settings(self, plugin_name: str, config: dict) -> None:
        config_str = json.dumps(config)
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO scraper_config (plugin_name, enabled, config_json, updated_at)
                VALUES (?, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(plugin_name) DO UPDATE SET
                    config_json = excluded.config_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (plugin_name, config_str),
            )

    def update_scraper(
        self,
        plugin_name: str,
        enabled: bool | None = None,
        description: str | None = None,
        tag: str | None = None,
        config: dict | None = None,
    ) -> dict | None:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT enabled, description, tag, config_json FROM scraper_config WHERE plugin_name = ?",
                (plugin_name,),
            ).fetchone()

            current_enabled = bool(row[0]) if row else True
            current_description = row[1] if row else None
            current_tag = row[2] if row else None
            current_config = json.loads(row[3]) if (row and row[3]) else {}

            new_enabled = enabled if enabled is not None else current_enabled
            new_description = description if description is not None else current_description
            new_tag = tag if tag is not None else current_tag
            new_config = config if config is not None else current_config
            connection.execute(
                """
                INSERT INTO scraper_config (plugin_name, enabled, description, tag, config_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(plugin_name) DO UPDATE SET
                    enabled = excluded.enabled,
                    description = excluded.description,
                    tag = excluded.tag,
                    config_json = excluded.config_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    plugin_name,
                    1 if new_enabled else 0,
                    new_description,
                    new_tag,
                    json.dumps(new_config),
                ),
            )
        return self.get_scraper_config(plugin_name)

    def record_scraper_failure(
        self,
        plugin_name: str,
        reason: str,
        cooldown_until: datetime | None,
        consecutive_failures: int,
    ) -> None:
        cooldown_str = cooldown_until.isoformat() if cooldown_until else None
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO scraper_config (plugin_name, enabled, cooldown_until, consecutive_failures, last_failure_reason, updated_at)
                VALUES (?, 1, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(plugin_name) DO UPDATE SET
                    cooldown_until = excluded.cooldown_until,
                    consecutive_failures = excluded.consecutive_failures,
                    last_failure_reason = excluded.last_failure_reason,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (plugin_name, cooldown_str, consecutive_failures, reason),
            )

    def record_scraper_success(self, plugin_name: str) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO scraper_config (plugin_name, enabled, cooldown_until, consecutive_failures, last_failure_reason, updated_at)
                VALUES (?, 1, NULL, 0, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(plugin_name) DO UPDATE SET
                    cooldown_until = NULL,
                    consecutive_failures = 0,
                    last_failure_reason = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (plugin_name,),
            )

    def reset_scraper_cooldown(self, plugin_name: str) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE scraper_config
                SET cooldown_until = NULL, consecutive_failures = 0, last_failure_reason = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE plugin_name = ?
                """,
                (plugin_name,),
            )


    def reconcile_stale_scraper_logs(self, timeout_minutes: int = 15) -> int:
        """Mark orphaned or long-abandoned RUNNING / TRIGGERED scraper logs as FAILED.

        This handles cases where the server process restarted, crashed, or was killed mid-scrape,
        or where a scraper thread timed out or hung.
        """
        mins = max(0, int(timeout_minutes))
        try:
            with self._database.connection() as connection:
                if mins == 0:
                    cursor = connection.execute(
                        """
                        UPDATE scraper_logs
                        SET status = 'FAILED',
                            error_message = CASE
                                WHEN error_message IS NULL OR error_message = 'In progress...'
                                    THEN 'Scraper execution manually cancelled or reset'
                                ELSE error_message || ' | Execution reset'
                            END
                        WHERE status IN ('RUNNING', 'TRIGGERED')
                        """
                    )
                else:
                    error_text = f"Scraper timed out or execution was interrupted (stale > {mins}m)"
                    cursor = connection.execute(
                        """
                        UPDATE scraper_logs
                        SET status = 'FAILED',
                            error_message = CASE
                                WHEN error_message IS NULL OR error_message = 'In progress...'
                                    THEN ?
                                ELSE error_message || ' | ' || ?
                            END
                        WHERE status IN ('RUNNING', 'TRIGGERED')
                          AND (timestamp <= datetime('now', '-' || ? || ' minutes') OR timestamp IS NULL)
                        """,
                        (error_text, error_text, mins),
                    )
                return cursor.rowcount or 0
        except Exception as exc:
            logger.warning("Failed to reconcile stale scraper logs: %s", exc)
            return 0

    def get_scraper_logs(
        self,
        plugin_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        try:
            self.reconcile_stale_scraper_logs(timeout_minutes=15)
        except Exception:
            pass

        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where_clauses = []
        params: list[object] = []

        if plugin_name:
            where_clauses.append("plugin_name = ?")
            params.append(plugin_name)
        if status:
            where_clauses.append("status = ?")
            params.append(status.upper())

        has_reason_col = False
        try:
            with self._database.connection() as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(scraper_logs)").fetchall()]
                has_reason_col = "trigger_reason" in cols
        except Exception:
            has_reason_col = False

        select_cols = "id, plugin_name, status, records_inserted, timestamp, error_message" + (", trigger_reason" if has_reason_col else "")
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"""
            SELECT {select_cols}
            FROM scraper_logs
            {where_sql}
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        results: list[dict] = []
        with self._database.connection() as connection:
            cursor = connection.execute(query, tuple(params))
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "plugin_name": row[1],
                    "status": row[2],
                    "records_inserted": row[3],
                    "timestamp": _normalize_utc_iso(row[4]),
                    "error_message": row[5],
                    "trigger_reason": row[6] if len(row) > 6 and row[6] else "Manual / Unspecified",
                })
        return results

    def get_scraper_stats(self) -> dict[str, dict]:
        try:
            self.reconcile_stale_scraper_logs(timeout_minutes=15)
        except Exception:
            pass
        stats: dict[str, dict] = {}
        with self._database.connection() as connection:
            cursor = connection.execute("""
                SELECT 
                    plugin_name,
                    COUNT(*) as total_runs,
                    SUM(records_inserted) as total_records,
                    SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as success_runs,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed_runs,
                    SUM(CASE WHEN status = 'COOLDOWN_SKIPPED' THEN 1 ELSE 0 END) as cooldown_runs,
                    SUM(CASE WHEN status = 'DISABLED_SKIPPED' THEN 1 ELSE 0 END) as disabled_runs,
                    SUM(CASE WHEN status IN ('RUNNING', 'TRIGGERED') THEN 1 ELSE 0 END) as running_runs,
                    MAX(timestamp) as last_run_at
                FROM scraper_logs
                GROUP BY plugin_name
            """)
            for row in cursor.fetchall():
                stats[row[0]] = {
                    "total_runs": row[1] or 0,
                    "total_records": row[2] or 0,
                    "success_runs": row[3] or 0,
                    "failed_runs": row[4] or 0,
                    "cooldown_runs": row[5] or 0,
                    "disabled_runs": row[6] or 0,
                    "running_runs": row[7] or 0,
                    "last_run_at": _normalize_utc_iso(row[8]),
                }
        return stats


