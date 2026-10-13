"""Filesystem and JSON persistence for AOI historical baseline models."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional, Sequence

from sentinel_analysis.application.ports.aoi_baseline_repository import AOIBaselineRepository
from sentinel_analysis.domain.aoi_baseline import (
    AOIBaseline,
    ClutterBaseline,
    PersistentScatterer,
)
from sentinel_analysis.domain.entities import BoundingBox

logger = logging.getLogger(__name__)


def _parse_iso(val: Any) -> Optional[datetime]:
    if not val or not isinstance(val, str):
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


class FileSystemAOIBaselineRepository(AOIBaselineRepository):
    """Persists AOI baseline models as atomic JSON documents on the local filesystem."""

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, aoi_id: int) -> Path:
        return self._dir / f"aoi_{aoi_id}_baseline.json"

    def save(self, baseline: AOIBaseline) -> None:
        """Serialize and save baseline to disk."""
        target = self._file_path(baseline.aoi_id)
        temp_target = target.with_suffix(".tmp")
        payload = baseline.to_dict()
        try:
            with open(temp_target, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            temp_target.replace(target)
        except Exception:
            if temp_target.exists():
                temp_target.unlink()
            raise

    def get(self, aoi_id: int) -> Optional[AOIBaseline]:
        """Deserialize baseline model from disk."""
        target = self._file_path(aoi_id)
        if not target.is_file():
            return None

        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._from_dict(data)
        except Exception as exc:
            logger.warning("Failed loading baseline for AOI %s from %s: %s", aoi_id, target, exc)
            return None

    def list_all(self) -> Sequence[AOIBaseline]:
        """List all valid baselines found in storage directory."""
        results: list[AOIBaseline] = []
        for file in self._dir.glob("aoi_*_baseline.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                b = self._from_dict(data)
                if b is not None:
                    results.append(b)
            except Exception:
                continue
        return results

    def delete(self, aoi_id: int) -> None:
        """Delete baseline file for AOI."""
        target = self._file_path(aoi_id)
        if target.is_file():
            try:
                target.unlink()
            except OSError:
                pass

    def _from_dict(self, data: dict[str, Any]) -> Optional[AOIBaseline]:
        try:
            aoi_id = int(data["aoi_id"])
            aoi_name = str(data["aoi_name"])
            bbox_coords = data["bbox"]
            bbox = BoundingBox(
                min_longitude=float(bbox_coords[0]),
                min_latitude=float(bbox_coords[1]),
                max_longitude=float(bbox_coords[2]),
                max_latitude=float(bbox_coords[3]),
            )
            created_at = _parse_iso(data["created_at"]) or datetime.now(timezone.utc)
            start_date = _parse_iso(data["start_date"]) or datetime.now(timezone.utc)
            end_date = _parse_iso(data["end_date"]) or datetime.now(timezone.utc)
            total_passes = int(data.get("total_passes", 1))

            cb_data = data.get("clutter_baseline", {})
            clutter_baseline = ClutterBaseline(
                mean_clutter_db=float(cb_data.get("mean_clutter_db", -21.5)),
                std_clutter_db=float(cb_data.get("std_clutter_db", 2.8)),
                p95_clutter_db=float(cb_data.get("p95_clutter_db", -16.2)),
                recommended_threshold_db=float(cb_data.get("recommended_threshold_db", -13.1)),
                sample_count=int(cb_data.get("sample_count", 0)),
            )

            ps_list: list[PersistentScatterer] = []
            for item in data.get("persistent_scatterers", []):
                ps = PersistentScatterer(
                    id=str(item["id"]),
                    latitude=float(item["latitude"]),
                    longitude=float(item["longitude"]),
                    persistence_pct=float(item["persistence_pct"]),
                    observation_count=int(item["observation_count"]),
                    total_passes=int(item.get("total_passes", total_passes)),
                    category=str(item.get("category", "INFRASTRUCTURE")),
                    average_rcs_db=float(item["average_rcs_db"]) if item.get("average_rcs_db") is not None else None,
                    average_length_m=float(item["average_length_m"]) if item.get("average_length_m") is not None else None,
                    average_beam_m=float(item["average_beam_m"]) if item.get("average_beam_m") is not None else None,
                    first_seen=_parse_iso(item.get("first_seen")),
                    last_seen=_parse_iso(item.get("last_seen")),
                    metadata=dict(item.get("metadata", {})),
                )
                ps_list.append(ps)

            return AOIBaseline(
                aoi_id=aoi_id,
                aoi_name=aoi_name,
                bbox=bbox,
                created_at=created_at,
                start_date=start_date,
                end_date=end_date,
                total_passes=total_passes,
                persistent_scatterers=tuple(ps_list),
                clutter_baseline=clutter_baseline,
                vessel_density_summary=dict(data.get("vessel_density_summary", {})),
                historical_pass_ids=tuple(data.get("historical_pass_ids", [])),
            )
        except Exception as exc:
            logger.warning("Error parsing baseline dictionary: %s", exc)
            return None
