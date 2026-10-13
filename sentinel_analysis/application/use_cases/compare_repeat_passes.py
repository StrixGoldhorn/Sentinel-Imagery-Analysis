"""Application use case for before/after swipe comparison between repeat SAR scenes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import BoundingBox, Scan
from sentinel_analysis.domain.swipe_comparison import (
    RepeatPassCandidate,
    SwipeComparisonReport,
    TargetChangeStatus,
    compute_bbox_intersection,
    haversine_m,
)

logger = logging.getLogger(__name__)


class CompareRepeatPasses:
    """Matches repeat SAR acquisitions over the same AOI and builds swipe comparison datasets."""

    def __init__(self, scan_repository: ScanRepository) -> None:
        self._scan_repository = scan_repository

    def execute(
        self,
        target_scan_id: str,
        baseline_scan_id: Optional[str] = None,
        *,
        max_days_prior: int = 120,
        min_overlap_pct: float = 0.20,
        match_distance_m: float = 150.0,
    ) -> dict[str, Any]:
        """Execute repeat pass search and swipe comparison payload generation."""
        scan_getter = getattr(self._scan_repository, "get", None) or getattr(self._scan_repository, "get_scan", None)
        if scan_getter is None:
            raise RuntimeError("ScanRepository must provide get() or get_scan()")

        target_scan: Optional[Scan] = scan_getter(target_scan_id)
        if target_scan is None:
            raise ValueError(f"Target scan '{target_scan_id}' not found")

        target_bbox: BoundingBox = target_scan.bbox
        target_dt = getattr(getattr(target_scan, "acquisition", None), "acquired_at", None)
        if target_dt is None:
            # Fallback to metadata acquisition date or now
            meta_date = target_scan.metadata.get("acquisition_date")
            if meta_date and isinstance(meta_date, str):
                try:
                    target_dt = datetime.fromisoformat(meta_date.replace("Z", "+00:00"))
                except Exception:
                    target_dt = datetime.now(timezone.utc)
            else:
                target_dt = datetime.now(timezone.utc)

        # 1. Discover all candidate repeat passes
        list_fn = (
            getattr(self._scan_repository, "list_all", None)
            or getattr(self._scan_repository, "list_scans", None)
            or getattr(self._scan_repository, "list_recent", None)
        )
        all_scans: list[Scan] = list_fn() if list_fn else []

        candidates: list[RepeatPassCandidate] = []
        best_candidate_scan: Optional[Scan] = None
        best_overlap: float = 0.0

        for s in all_scans:
            if s.folder_name == target_scan_id:
                continue

            s_bbox: Optional[BoundingBox] = getattr(s, "bbox", None)
            if s_bbox is None:
                continue

            inter_box, overlap_ratio = compute_bbox_intersection(target_bbox, s_bbox)
            if overlap_ratio < min_overlap_pct:
                continue

            s_dt = getattr(getattr(s, "acquisition", None), "acquired_at", None)
            if s_dt is None:
                m_date = s.metadata.get("acquisition_date")
                if m_date and isinstance(m_date, str):
                    try:
                        s_dt = datetime.fromisoformat(m_date.replace("Z", "+00:00"))
                    except Exception:
                        s_dt = target_dt
                else:
                    s_dt = target_dt

            days_prior = (target_dt - s_dt).total_seconds() / 86400.0

            cand = RepeatPassCandidate(
                scan_id=s.folder_name,
                acquired_at=s_dt,
                days_prior=days_prior,
                overlap_pct=overlap_ratio,
                satellite=str(s.metadata.get("satellite") or "Sentinel-1"),
                orbit_direction=s.metadata.get("orbit_direction"),
                polarization=s.metadata.get("polarization"),
                image_url=f"/media/scans/{s.folder_name}/stitched.png",
            )
            candidates.append(cand)

        # Sort candidates: prior passes first, then by overlap descending
        candidates.sort(key=lambda c: (0 if c.days_prior > 0 else 1, -c.overlap_pct, abs(c.days_prior)))

        # 2. Select baseline scan
        baseline_scan: Optional[Scan] = None
        if baseline_scan_id:
            baseline_scan = scan_getter(baseline_scan_id)
            if baseline_scan is None:
                raise ValueError(f"Specified baseline scan '{baseline_scan_id}' not found")
        elif candidates:
            # Pick first candidate
            baseline_scan = scan_getter(candidates[0].scan_id)

        # If still no baseline available, self-compare or return standalone candidate list
        if baseline_scan is None:
            baseline_scan = target_scan

        baseline_bbox: BoundingBox = baseline_scan.bbox
        common_box, overlap_pct = compute_bbox_intersection(target_bbox, baseline_bbox)
        if common_box is None:
            common_box = target_bbox
            overlap_pct = 1.0

        baseline_dt = getattr(getattr(baseline_scan, "acquisition", None), "acquired_at", None) or target_dt
        time_delta_sec = (target_dt - baseline_dt).total_seconds()
        days_diff = time_delta_sec / 86400.0
        hours_diff = time_delta_sec / 3600.0

        # 3. Target Diffing (Persistent, Arrived, Departed)
        target_dets = self._extract_detections(target_scan)
        baseline_dets = self._extract_detections(baseline_scan)

        target_changes: list[TargetChangeStatus] = []
        matched_baseline_indices: set[int] = set()

        for t_idx, td in enumerate(target_dets):
            t_lat = td.get("latitude")
            t_lon = td.get("longitude")
            if t_lat is None or t_lon is None:
                continue

            best_match_idx = None
            min_dist = float("inf")

            if baseline_scan.folder_name != target_scan.folder_name:
                for b_idx, bd in enumerate(baseline_dets):
                    b_lat = bd.get("latitude")
                    b_lon = bd.get("longitude")
                    if b_lat is None or b_lon is None:
                        continue
                    d_m = haversine_m(float(t_lat), float(t_lon), float(b_lat), float(b_lon))
                    if d_m < min_dist:
                        min_dist = d_m
                        best_match_idx = b_idx

            if best_match_idx is not None and min_dist <= match_distance_m:
                matched_baseline_indices.add(best_match_idx)
                target_changes.append(
                    TargetChangeStatus(
                        target_id=f"target_{target_scan.folder_name}_{t_idx}",
                        status="PERSISTENT",
                        latitude=float(t_lat),
                        longitude=float(t_lon),
                        confidence=float(td.get("confidence") or 0.85),
                        length_m=float(td.get("length_m") or td.get("length") or 0.0) or None,
                        separation_m=min_dist,
                        pass_source="TARGET",
                    )
                )
            else:
                target_changes.append(
                    TargetChangeStatus(
                        target_id=f"target_{target_scan.folder_name}_{t_idx}",
                        status="ARRIVED",
                        latitude=float(t_lat),
                        longitude=float(t_lon),
                        confidence=float(td.get("confidence") or 0.85),
                        length_m=float(td.get("length_m") or td.get("length") or 0.0) or None,
                        pass_source="TARGET",
                    )
                )

        # Departed targets: in baseline but not in target
        if baseline_scan.folder_name != target_scan.folder_name:
            for b_idx, bd in enumerate(baseline_dets):
                if b_idx not in matched_baseline_indices:
                    b_lat = bd.get("latitude")
                    b_lon = bd.get("longitude")
                    if b_lat is None or b_lon is None:
                        continue
                    target_changes.append(
                        TargetChangeStatus(
                            target_id=f"baseline_{baseline_scan.folder_name}_{b_idx}",
                            status="DEPARTED",
                            latitude=float(b_lat),
                            longitude=float(b_lon),
                            confidence=float(bd.get("confidence") or 0.85),
                            length_m=float(bd.get("length_m") or bd.get("length") or 0.0) or None,
                            pass_source="BASELINE",
                        )
                    )

        report = SwipeComparisonReport(
            target_scan_id=target_scan.folder_name,
            baseline_scan_id=baseline_scan.folder_name,
            target_acquisition=target_dt,
            baseline_acquisition=baseline_dt,
            days_difference=days_diff,
            hours_difference=hours_diff,
            overlap_pct=overlap_pct,
            common_bbox=common_box,
            target_image_url=f"/media/scans/{target_scan.folder_name}/stitched.png",
            baseline_image_url=f"/media/scans/{baseline_scan.folder_name}/stitched.png",
            targets=target_changes,
            candidates=candidates,
            metadata={
                "match_distance_m": match_distance_m,
                "target_metadata": target_scan.metadata,
                "baseline_metadata": baseline_scan.metadata,
            },
        )
        return report.to_dict()

    def _extract_detections(self, scan: Scan) -> list[dict[str, Any]]:
        """Safely extract detection dictionaries from scan metadata or results file."""
        if scan.metadata and isinstance(scan.metadata.get("detections"), list):
            return list(scan.metadata["detections"])

        if scan.image_path:
            p = Path(scan.image_path).parent / "detection_results.json"
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data.get("detections"), list):
                        return data["detections"]
                except Exception as exc:
                    logger.debug("Failed reading detection_results.json for %s: %s", scan.folder_name, exc)
        return []
