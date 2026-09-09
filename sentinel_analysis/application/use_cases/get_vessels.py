"""Query normalized AIS vessel records and positions."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.domain.entities import BoundingBox


class GetVesselPositions:
    """Use case to query persisted AIS vessel positions with spatial/temporal filters."""

    def __init__(self, repository: AISRepository) -> None:
        self._repository = repository

    def execute(
        self,
        bbox: BoundingBox | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        limit: int = 500,
        latest_only: bool = True,
        within_hours: float | None = None,
    ) -> list[dict[str, Any]]:
        effective_time_range = time_range
        if within_hours is not None and effective_time_range is None:
            now_utc = datetime.now(timezone.utc)
            effective_time_range = (now_utc - timedelta(hours=within_hours), None)

        positions = self._repository.get_vessel_positions(
            bbox=bbox,
            time_range=effective_time_range,
            limit=limit,
            latest_only=latest_only,
        )

        if within_hours is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
            filtered = []
            for pos in positions:
                ts = pos.get("timestamp")
                if ts:
                    if isinstance(ts, str):
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if dt < cutoff:
                                continue
                        except Exception:
                            pass
                    elif isinstance(ts, datetime):
                        dt = ts if ts.utcoffset() else ts.replace(tzinfo=timezone.utc)
                        if dt < cutoff:
                            continue
                filtered.append(pos)
            positions = filtered

        if latest_only:
            seen_mmsi: dict[str, dict[str, Any]] = {}
            for pos in positions:
                mmsi = str(pos.get("mmsi") or "")
                if not mmsi:
                    continue
                if mmsi not in seen_mmsi:
                    seen_mmsi[mmsi] = pos
                else:
                    cur_ts = str(seen_mmsi[mmsi].get("timestamp") or "")
                    new_ts = str(pos.get("timestamp") or "")
                    if new_ts > cur_ts:
                        seen_mmsi[mmsi] = pos
            return list(seen_mmsi.values())
        return positions

