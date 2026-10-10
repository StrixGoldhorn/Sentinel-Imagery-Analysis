"""SQLite implementation of the analyst review repository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sentinel_analysis.domain.review import ReviewAction, ReviewDisposition, ReviewHistoryEntry, ReviewRecord
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner
from sentinel_analysis.infrastructure.persistence.sqlite import SQLiteDatabase


def _parse_dt(val: Any) -> datetime:
    if not val:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _format_dt(dt: datetime | None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class SQLiteReviewRepository:
    """Stores analyst reviews and immutable audit trail in SQLite."""

    def __init__(self, database_path: Path | str, timeout: float = 10.0) -> None:
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteDatabase(self._database_path, timeout=timeout)
        self.initialize()

    def initialize(self) -> None:
        MigrationRunner(self._database_path).run_migrations()

    def _row_to_history(self, row: Any) -> ReviewHistoryEntry:
        corrected_bbox = None
        if row["corrected_bbox"]:
            try:
                corrected_bbox = json.loads(row["corrected_bbox"])
            except Exception:
                corrected_bbox = None

        metadata = {}
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except Exception:
                metadata = {}

        return ReviewHistoryEntry(
            id=int(row["id"]),
            review_id=row["review_id"],
            action=row["action"],
            disposition=row["disposition"],
            reviewer_id=row["reviewer_id"],
            corrected_bbox=corrected_bbox,
            comments=row["comments"] or "",
            metadata=metadata,
            timestamp=_parse_dt(row["timestamp"]),
        )

    def _row_to_record(self, row: Any, history: list[ReviewHistoryEntry] | None = None) -> ReviewRecord:
        orig_bbox = {}
        if row["original_bbox"]:
            try:
                orig_bbox = json.loads(row["original_bbox"])
            except Exception:
                orig_bbox = {}

        corr_bbox = None
        if row["corrected_bbox"]:
            try:
                corr_bbox = json.loads(row["corrected_bbox"])
            except Exception:
                corr_bbox = None

        reasons: list[str] = []
        if row["reason_codes"]:
            try:
                reasons = json.loads(row["reason_codes"])
            except Exception:
                reasons = []

        return ReviewRecord(
            review_id=row["review_id"],
            scan_id=row["scan_id"],
            detection_idx=int(row["detection_idx"]),
            disposition=row["disposition"],
            reviewer_id=row["reviewer_id"],
            original_bbox=orig_bbox,
            corrected_bbox=corr_bbox,
            confidence=float(row["confidence"] or 0.0),
            vessel_class=row["vessel_class"],
            comments=row["comments"],
            reason_codes=reasons,
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            history=history or [],
        )

    def get(self, review_id: str) -> ReviewRecord | None:
        with self._database.connection(rows=True) as conn:
            row = conn.execute("SELECT * FROM reviews WHERE review_id = ?", (review_id,)).fetchone()
            if row is None:
                return None
            hist_rows = conn.execute(
                "SELECT * FROM review_history WHERE review_id = ? ORDER BY id ASC",
                (review_id,),
            ).fetchall()
            history = [self._row_to_history(hr) for hr in hist_rows]
            return self._row_to_record(row, history)

    def get_by_id(self, review_id: str) -> ReviewRecord | None:
        """Fetch review record by unique identifier (alias for get)."""
        return self.get(review_id)

    def get_by_scan_and_index(self, scan_id: str, detection_idx: int) -> ReviewRecord | None:
        with self._database.connection(rows=True) as conn:
            row = conn.execute(
                "SELECT * FROM reviews WHERE scan_id = ? AND detection_idx = ?",
                (scan_id, detection_idx),
            ).fetchone()
            if row is None:
                return None
            review_id = row["review_id"]
            hist_rows = conn.execute(
                "SELECT * FROM review_history WHERE review_id = ? ORDER BY id ASC",
                (review_id,),
            ).fetchall()
            history = [self._row_to_history(hr) for hr in hist_rows]
            return self._row_to_record(row, history)

    def save(self, record: ReviewRecord, action: Any = None) -> ReviewRecord:
        now_str = _format_dt(record.updated_at)
        created_str = _format_dt(record.created_at)

        orig_d = record.original_bbox.to_dict() if hasattr(record.original_bbox, "to_dict") else record.original_bbox
        corr_d = record.corrected_bbox.to_dict() if hasattr(record.corrected_bbox, "to_dict") else record.corrected_bbox

        orig_json = json.dumps(orig_d) if orig_d else "{}"
        corr_json = json.dumps(corr_d) if corr_d else None
        reasons_json = json.dumps(list(record.reason_codes))

        with self._database.connection(rows=False) as conn:
            conn.execute(
                """
                INSERT INTO reviews (
                    review_id, scan_id, detection_idx, disposition, reviewer_id,
                    original_bbox, corrected_bbox, confidence, vessel_class,
                    comments, reason_codes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    disposition = excluded.disposition,
                    reviewer_id = excluded.reviewer_id,
                    original_bbox = excluded.original_bbox,
                    corrected_bbox = excluded.corrected_bbox,
                    confidence = excluded.confidence,
                    vessel_class = excluded.vessel_class,
                    comments = excluded.comments,
                    reason_codes = excluded.reason_codes,
                    updated_at = excluded.updated_at
                """,
                (
                    record.review_id,
                    record.scan_id,
                    record.detection_idx,
                    record.disposition,
                    record.reviewer_id,
                    orig_json,
                    corr_json,
                    float(record.confidence),
                    record.vessel_class,
                    record.comments,
                    reasons_json,
                    created_str,
                    now_str,
                ),
            )

        if action is not None:
            act_val = action.value if hasattr(action, "value") else str(action)
            entry = ReviewHistoryEntry(
                id=None,
                review_id=record.review_id,
                action=act_val,
                disposition=record.disposition,
                reviewer_id=record.reviewer_id or "system",
                corrected_bbox=corr_d,
                comments=record.comments or "",
                timestamp=record.updated_at,
            )
            self.add_history(entry)
        else:
            existing_history = self.get_history(record.review_id)
            if not existing_history:
                entry = ReviewHistoryEntry(
                    id=None,
                    review_id=record.review_id,
                    action=ReviewAction.CREATED.value,
                    disposition=record.disposition,
                    reviewer_id=record.reviewer_id or "system",
                    corrected_bbox=corr_d,
                    comments=record.comments or "",
                    timestamp=record.created_at,
                )
                self.add_history(entry)

        return record

    def add_history(self, entry: ReviewHistoryEntry) -> int:
        ts_str = _format_dt(entry.timestamp)
        corr_d = entry.corrected_bbox.to_dict() if hasattr(entry.corrected_bbox, "to_dict") else entry.corrected_bbox
        corr_json = json.dumps(corr_d) if corr_d else None
        meta_json = json.dumps(entry.metadata) if entry.metadata else None

        with self._database.connection(rows=False) as conn:
            cursor = conn.execute(
                """
                INSERT INTO review_history (
                    review_id, action, disposition, reviewer_id,
                    corrected_bbox, comments, metadata, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.review_id,
                    entry.action,
                    entry.disposition,
                    entry.reviewer_id,
                    corr_json,
                    entry.comments or "",
                    meta_json,
                    ts_str,
                ),
            )
            return int(cursor.lastrowid or 0)

    def get_history(self, review_id: str) -> list[ReviewHistoryEntry]:
        with self._database.connection(rows=True) as conn:
            rows = conn.execute(
                "SELECT * FROM review_history WHERE review_id = ? ORDER BY id ASC",
                (review_id,),
            ).fetchall()
            return [self._row_to_history(r) for r in rows]

    def list_reviews(
        self,
        disposition: str | None = None,
        scan_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReviewRecord]:
        query = "SELECT * FROM reviews WHERE 1=1"
        params: list[Any] = []
        if disposition and disposition.lower() != "all":
            query += " AND disposition = ?"
            params.append(disposition.lower())
        if scan_id:
            query += " AND scan_id = ?"
            params.append(scan_id)

        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])

        with self._database.connection(rows=True) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            if not rows:
                return []
            review_ids = [r["review_id"] for r in rows]
            # Batch fetch histories for these reviews
            placeholders = ",".join("?" for _ in review_ids)
            hist_query = f"SELECT * FROM review_history WHERE review_id IN ({placeholders}) ORDER BY id ASC"
            hist_rows = conn.execute(hist_query, tuple(review_ids)).fetchall()
            history_map: dict[str, list[ReviewHistoryEntry]] = {rid: [] for rid in review_ids}
            for hr in hist_rows:
                entry = self._row_to_history(hr)
                history_map.setdefault(entry.review_id, []).append(entry)

            return [self._row_to_record(r, history_map.get(r["review_id"], [])) for r in rows]

    def count_by_disposition(self) -> dict[str, int]:
        with self._database.connection(rows=True) as conn:
            rows = conn.execute("SELECT disposition, COUNT(*) as cnt FROM reviews GROUP BY disposition").fetchall()
            counts = {
                ReviewDisposition.PENDING.value: 0,
                ReviewDisposition.ACCEPTED.value: 0,
                ReviewDisposition.REJECTED.value: 0,
                ReviewDisposition.UNCERTAIN.value: 0,
            }
            total = 0
            for r in rows:
                disp = str(r["disposition"]).lower()
                c = int(r["cnt"])
                counts[disp] = c
                total += c
            counts["total"] = total
            return counts

    def query(
        self,
        disposition: str | None = None,
        scan_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ReviewRecord], int]:
        """Query reviews matching filter and return (list_of_reviews, total_matching)."""
        items = self.list_reviews(disposition=disposition, scan_id=scan_id, limit=limit, offset=offset)
        counts = self.count_by_disposition()
        disp_key = (disposition or "all").lower()
        if disp_key != "all":
            total = counts.get(disp_key, len(items))
        else:
            total = counts.get("total", len(items))
        return items, total
