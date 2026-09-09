"""SQLite implementation of the AOI repository."""

import json
from datetime import datetime, timezone
from pathlib import Path

from sentinel_analysis.domain.entities import AreaOfInterest, BoundingBox
from sentinel_analysis.domain.satellite import is_historical_prediction
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


def _parse_dt(val: object) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _format_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class SQLiteAreaOfInterestRepository:
    def __init__(self, database_path: Path | str, timeout: float = 5) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()

    @staticmethod
    def _from_row(row) -> AreaOfInterest:
        has_auto = "auto_capture_enabled" in row.keys()
        auto_capture = bool(row["auto_capture_enabled"]) if has_auto else False
        return AreaOfInterest(
            id=row["id"],
            name=row["name"],
            bbox=BoundingBox.from_sequence(json.loads(row["bbox"])),
            next_scan=_parse_dt(row["next_scan"]),
            last_checked=_parse_dt(row["last_checked"]),
            auto_capture_enabled=auto_capture,
        )

    def list(self) -> list[AreaOfInterest]:
        with self._database.connection(rows=True) as connection:
            rows = connection.execute("SELECT * FROM aoi ORDER BY name").fetchall()
        return [self._from_row(row) for row in rows]

    def add(self, aoi: AreaOfInterest) -> int:
        with self._database.connection(rows=True) as connection:
            cursor = connection.execute(
                "INSERT INTO aoi (name, bbox, auto_capture_enabled) VALUES (?, ?, ?)",
                (aoi.name, json.dumps(aoi.bbox.as_list()), 1 if aoi.auto_capture_enabled else 0),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an AOI identifier")
            return int(cursor.lastrowid)

    def get(self, aoi_id: int) -> AreaOfInterest | None:
        with self._database.connection(rows=True) as connection:
            row = connection.execute("SELECT * FROM aoi WHERE id = ?", (aoi_id,)).fetchone()
        return self._from_row(row) if row else None

    def update_prediction(self, aoi_id: int, next_scan: datetime, last_checked: datetime) -> None:
        with self._database.connection(rows=True) as connection:
            cursor = connection.execute(
                "UPDATE aoi SET next_scan = ?, last_checked = ? WHERE id = ?",
                (_format_dt(next_scan), _format_dt(last_checked), aoi_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"Area of interest not found: {aoi_id}")

    def update_auto_capture(self, aoi_id: int, enabled: bool) -> None:
        with self._database.connection(rows=True) as connection:
            cursor = connection.execute(
                "UPDATE aoi SET auto_capture_enabled = ? WHERE id = ?",
                (1 if enabled else 0, aoi_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"Area of interest not found: {aoi_id}")

    def get_cached_forecast(self, aoi_id: int) -> dict | None:
        from datetime import timedelta
        with self._database.connection(rows=True) as connection:
            row = connection.execute("SELECT * FROM aoi_forecasts WHERE aoi_id = ?", (aoi_id,)).fetchone()
        if not row:
            return None

        now = datetime.now(timezone.utc)
        expires_at = _parse_dt(row["expires_at"])
        fetched_at = _parse_dt(row["fetched_at"])
        if not expires_at or not fetched_at:
            return None

        if now > expires_at:
            return None

        n2yo_preds = json.loads(row["n2yo_predictions_json"]) if row["n2yo_predictions_json"] else []
        hist_preds = json.loads(row["historical_predictions_json"]) if row["historical_predictions_json"] else []
        comb_preds = json.loads(row["combined_predictions_json"]) if row["combined_predictions_json"] else []
        mission_summary = json.loads(row["mission_analysis_json"]) if row["mission_analysis_json"] else None

        cutoff = now - timedelta(minutes=5)
        def _filter_upcoming(passes):
            filtered = []
            for p in passes:
                t_val = p.get("time")
                if t_val:
                    p_dt = _parse_dt(t_val)
                    if p_dt is not None:
                        if p_dt >= cutoff:
                            filtered.append(p)
                    else:
                        filtered.append(p)
                else:
                    filtered.append(p)
            return filtered

        n2yo_filtered = _filter_upcoming(n2yo_preds)
        hist_filtered = [p for p in _filter_upcoming(hist_preds) if is_historical_prediction(p)]
        comb_filtered = [p for p in _filter_upcoming(comb_preds) if is_historical_prediction(p)]

        next_scan_val = None
        if comb_filtered:
            next_scan_val = comb_filtered[0]["time"]
        elif hist_filtered:
            next_scan_val = hist_filtered[0]["time"]

        return {
            "aoi_id": aoi_id,
            "predictions": comb_filtered if comb_filtered else hist_filtered,
            "n2yo_predictions": n2yo_filtered,
            "historical_predictions": hist_filtered,
            "mission_analysis": mission_summary,
            "next_scan": next_scan_val,
            "fetched_at": fetched_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "cached": True,
        }

    def save_cached_forecast(
        self,
        aoi_id: int,
        forecast_data: dict,
        ttl_seconds: int = 3600,
    ) -> None:
        from datetime import timedelta
        fetched_at = datetime.now(timezone.utc)
        expires_at = fetched_at + timedelta(seconds=ttl_seconds)

        n2yo_json = json.dumps(forecast_data.get("n2yo_predictions", []))
        hist_json = json.dumps(forecast_data.get("historical_predictions", []))
        comb_json = json.dumps(forecast_data.get("predictions", []))
        mission_json = json.dumps(forecast_data.get("mission_analysis")) if forecast_data.get("mission_analysis") else None
        next_scan = forecast_data.get("next_scan")

        with self._database.connection(rows=True) as connection:
            connection.execute(
                """
                INSERT INTO aoi_forecasts (
                    aoi_id,
                    n2yo_predictions_json,
                    historical_predictions_json,
                    combined_predictions_json,
                    mission_analysis_json,
                    next_scan,
                    fetched_at,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(aoi_id) DO UPDATE SET
                    n2yo_predictions_json = excluded.n2yo_predictions_json,
                    historical_predictions_json = excluded.historical_predictions_json,
                    combined_predictions_json = excluded.combined_predictions_json,
                    mission_analysis_json = excluded.mission_analysis_json,
                    next_scan = excluded.next_scan,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (
                    aoi_id,
                    n2yo_json,
                    hist_json,
                    comb_json,
                    mission_json,
                    next_scan,
                    fetched_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )

    def clear_cached_forecast(self, aoi_id: int) -> None:
        with self._database.connection(rows=True) as connection:
            connection.execute("DELETE FROM aoi_forecasts WHERE aoi_id = ?", (aoi_id,))

    def delete(self, aoi_id: int) -> None:
        with self._database.connection(rows=True) as connection:
            connection.execute("DELETE FROM aoi_forecasts WHERE aoi_id = ?", (aoi_id,))
            connection.execute("DELETE FROM post_pass_ingestions WHERE aoi_id = ?", (aoi_id,))
            cursor = connection.execute("DELETE FROM aoi WHERE id = ?", (aoi_id,))
            if cursor.rowcount == 0:
                raise LookupError(f"Area of interest not found: {aoi_id}")
