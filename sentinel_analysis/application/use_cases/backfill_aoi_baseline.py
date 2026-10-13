"""Application use case for backfilling historical satellite imagery and building an AOI baseline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.application.ports.aoi_baseline_repository import AOIBaselineRepository
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.aoi_baseline import (
    AOIBaseline,
    ClutterBaseline,
    PersistentScatterer,
    haversine_distance_m,
)
from sentinel_analysis.domain.entities import BoundingBox, Scan

logger = logging.getLogger(__name__)


def bboxes_intersect(b1: BoundingBox, b2: BoundingBox) -> bool:
    """Return True if two EPSG:4326 bounding boxes overlap."""
    return not (
        b1.max_longitude < b2.min_longitude
        or b1.min_longitude > b2.max_longitude
        or b1.max_latitude < b2.min_latitude
        or b1.min_latitude > b2.max_latitude
    )


class BackfillAoiBaseline:
    """Orchestrates historical pass discovery, multi-temporal ingestion, and SAR clutter / persistent scatterer baseline synthesis."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        scan_repository: ScanRepository,
        baseline_repository: AOIBaselineRepository,
        asf_client: Optional[Any] = None,
        detection_saver: Optional[Any] = None,
    ) -> None:
        self._aoi_repository = aoi_repository
        self._scan_repository = scan_repository
        self._baseline_repository = baseline_repository
        self._asf_client = asf_client
        self._detection_saver = detection_saver

    def execute(
        self,
        aoi_id: int,
        *,
        days_back: int = 90,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_persistence_pct: float = 0.40,
        cluster_radius_m: float = 150.0,
        max_scenes: int = 25,
    ) -> dict[str, Any]:
        """Backfill historical imagery and automatically build a SAR baseline model for the AOI."""
        aoi = self._aoi_repository.get(aoi_id)
        if aoi is None:
            raise AreaOfInterestNotFoundError(f"Area of interest not found: {aoi_id}")

        now_utc = datetime.now(timezone.utc)
        resolved_end = end_date or now_utc
        resolved_start = start_date or (resolved_end - timedelta(days=max(1, days_back)))

        if resolved_start.utcoffset() is None:
            resolved_start = resolved_start.replace(tzinfo=timezone.utc)
        if resolved_end.utcoffset() is None:
            resolved_end = resolved_end.replace(tzinfo=timezone.utc)

        # 1. Discover all existing scans overlapping this AOI within date range
        all_scans = list(self._scan_repository.list())
        overlapping_scans: list[Scan] = []

        for scan in all_scans:
            scan_bbox = getattr(scan, "bbox", None)
            if scan_bbox is not None and not bboxes_intersect(aoi.bbox, scan_bbox):
                continue

            scan_dt = self._resolve_scan_datetime(scan)
            if scan_dt and resolved_start <= scan_dt <= resolved_end:
                overlapping_scans.append(scan)

        # Sort chronologically
        overlapping_scans.sort(key=lambda s: self._resolve_scan_datetime(s) or now_utc)
        if len(overlapping_scans) > max_scenes:
            overlapping_scans = overlapping_scans[-max_scenes:]

        # 2. If archive client provided, search for catalog passes to supplement metadata
        archive_granules: list[dict[str, Any]] = []
        if self._asf_client is not None and hasattr(self._asf_client, "search"):
            try:
                raw_products = self._asf_client.search(
                    bbox=aoi.bbox,
                    start_date=resolved_start,
                    end_date=resolved_end,
                    platform="SENTINEL-1",
                    max_results=max_scenes,
                )
                for p in raw_products:
                    if hasattr(p, "to_dict"):
                        archive_granules.append(p.to_dict())
                    elif isinstance(p, dict):
                        archive_granules.append(p)
            except Exception as exc:
                logger.warning("ASF search during baseline backfill failed for AOI %s: %s", aoi_id, exc)

        pass_ids: list[str] = [s.folder_name for s in overlapping_scans]
        if not pass_ids and archive_granules:
            pass_ids = [g.get("granule_name") or f"granule_{i}" for i, g in enumerate(archive_granules)]

        # 3. Extract detections and clutter statistics across all passes
        detections_by_pass: list[tuple[str, datetime, list[dict[str, Any]]]] = []
        clutter_samples_mean: list[float] = []
        clutter_samples_std: list[float] = []

        for s in overlapping_scans:
            s_dt = self._resolve_scan_datetime(s) or now_utc
            dets = self._extract_detections(s)
            detections_by_pass.append((s.folder_name, s_dt, dets))

            # Clutter metadata
            meta = s.metadata or {}
            if "clutter_mean_db" in meta and isinstance(meta["clutter_mean_db"], (int, float)):
                clutter_samples_mean.append(float(meta["clutter_mean_db"]))
            if "clutter_std_db" in meta and isinstance(meta["clutter_std_db"], (int, float)):
                clutter_samples_std.append(float(meta["clutter_std_db"]))

        total_passes = max(1, len(pass_ids))

        # 4. Cluster detections across passes to identify persistent scatterers
        clusters: list[dict[str, Any]] = []  # dict with centroid_lat, centroid_lon, detections, passes, etc.

        for pass_id, pass_time, dets in detections_by_pass:
            for d in dets:
                lat = d.get("latitude")
                lon = d.get("longitude")
                if lat is None or lon is None:
                    continue
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                except (ValueError, TypeError):
                    continue

                best_cluster = None
                best_dist = cluster_radius_m + 1.0

                for cl in clusters:
                    dist = haversine_distance_m(lat_f, lon_f, cl["centroid_lat"], cl["centroid_lon"])
                    if dist <= cluster_radius_m and dist < best_dist:
                        best_dist = dist
                        best_cluster = cl

                det_record = {
                    "pass_id": pass_id,
                    "time": pass_time,
                    "lat": lat_f,
                    "lon": lon_f,
                    "length": float(d.get("length_m") or d.get("length") or 0.0) or None,
                    "beam": float(d.get("beam_m") or d.get("beam") or 0.0) or None,
                    "rcs_db": float(d.get("rcs_db") or d.get("intensity") or 0.0) or None,
                    "confidence": float(d.get("confidence") or 0.8),
                }

                if best_cluster is not None:
                    best_cluster["detections"].append(det_record)
                    best_cluster["passes"].add(pass_id)
                    # Update running centroid
                    n = len(best_cluster["detections"])
                    best_cluster["centroid_lat"] = (best_cluster["centroid_lat"] * (n - 1) + lat_f) / n
                    best_cluster["centroid_lon"] = (best_cluster["centroid_lon"] * (n - 1) + lon_f) / n
                else:
                    clusters.append({
                        "centroid_lat": lat_f,
                        "centroid_lon": lon_f,
                        "passes": {pass_id},
                        "detections": [det_record],
                    })

        # 5. Build PersistentScatterers
        persistent_scatterers: list[PersistentScatterer] = []
        transient_vessel_count = 0
        persistent_detections_count = 0

        for idx, cl in enumerate(clusters):
            pass_count = len(cl["passes"])
            persistence_pct = min(1.0, pass_count / total_passes)

            lengths = [d["length"] for d in cl["detections"] if d["length"] is not None]
            beams = [d["beam"] for d in cl["detections"] if d["beam"] is not None]
            rcs_vals = [d["rcs_db"] for d in cl["detections"] if d["rcs_db"] is not None]
            times = sorted(d["time"] for d in cl["detections"])

            avg_len = sum(lengths) / len(lengths) if lengths else None
            avg_beam = sum(beams) / len(beams) if beams else None
            avg_rcs = sum(rcs_vals) / len(rcs_vals) if rcs_vals else None

            # Persistence criteria: >= min_persistence_pct AND seen in at least 2 passes if total_passes >= 2
            is_persistent = persistence_pct >= min_persistence_pct and (total_passes < 2 or pass_count >= 2)

            if is_persistent:
                persistent_detections_count += len(cl["detections"])
                # Classify category
                if avg_len is not None and avg_len >= 60.0:
                    category = "OIL_RIG"
                elif avg_len is not None and avg_len <= 30.0 and persistence_pct >= 0.7:
                    category = "WIND_TURBINE"
                elif avg_len is not None and avg_len <= 15.0:
                    category = "NAVIGATION_AID"
                else:
                    category = "INFRASTRUCTURE"

                ps = PersistentScatterer(
                    id=f"ps_{aoi_id}_{idx + 1}",
                    latitude=cl["centroid_lat"],
                    longitude=cl["centroid_lon"],
                    persistence_pct=persistence_pct,
                    observation_count=pass_count,
                    total_passes=total_passes,
                    category=category,
                    average_rcs_db=avg_rcs,
                    average_length_m=avg_len,
                    average_beam_m=avg_beam,
                    first_seen=times[0] if times else None,
                    last_seen=times[-1] if times else None,
                    metadata={"sample_observations": len(cl["detections"])},
                )
                persistent_scatterers.append(ps)
            else:
                transient_vessel_count += len(cl["detections"])

        # Sort persistent scatterers by persistence desc
        persistent_scatterers.sort(key=lambda p: (p.persistence_pct, p.observation_count), reverse=True)

        # 6. Compute Sea Clutter Baseline
        if clutter_samples_mean:
            mean_clutter = sum(clutter_samples_mean) / len(clutter_samples_mean)
        else:
            mean_clutter = -21.5

        if clutter_samples_std:
            std_clutter = sum(clutter_samples_std) / len(clutter_samples_std)
        else:
            std_clutter = 2.8

        p95_clutter = mean_clutter + 1.645 * std_clutter
        rec_threshold = mean_clutter + 3.0 * std_clutter

        clutter_baseline = ClutterBaseline(
            mean_clutter_db=mean_clutter,
            std_clutter_db=std_clutter,
            p95_clutter_db=p95_clutter,
            recommended_threshold_db=rec_threshold,
            sample_count=len(clutter_samples_mean) or len(overlapping_scans),
        )

        # 7. Vessel Density Summary
        avg_vessels = (transient_vessel_count / total_passes) if total_passes > 0 else 0.0
        if avg_vessels < 3.0:
            traffic_density = "LOW"
        elif avg_vessels < 15.0:
            traffic_density = "MODERATE"
        elif avg_vessels < 40.0:
            traffic_density = "HIGH"
        else:
            traffic_density = "CONGESTED"

        vessel_density_summary = {
            "total_detections": transient_vessel_count + persistent_detections_count,
            "transient_vessels_detected": transient_vessel_count,
            "persistent_structures_detected": len(persistent_scatterers),
            "average_transient_vessels_per_pass": round(avg_vessels, 1),
            "traffic_density_level": traffic_density,
            "cluster_count": len(clusters),
        }

        # 8. Create and Persist Baseline
        baseline = AOIBaseline(
            aoi_id=aoi_id,
            aoi_name=aoi.name,
            bbox=aoi.bbox,
            created_at=now_utc,
            start_date=resolved_start,
            end_date=resolved_end,
            total_passes=total_passes,
            persistent_scatterers=tuple(persistent_scatterers),
            clutter_baseline=clutter_baseline,
            vessel_density_summary=vessel_density_summary,
            historical_pass_ids=tuple(pass_ids),
        )

        self._baseline_repository.save(baseline)
        return baseline.to_dict()

    def _resolve_scan_datetime(self, scan: Scan) -> Optional[datetime]:
        """Extract acquisition timestamp from scan entity or its metadata."""
        if getattr(scan, "acquisition", None) and getattr(scan.acquisition, "acquired_at", None):
            dt = scan.acquisition.acquired_at
            if dt.utcoffset() is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        meta_date = (scan.metadata or {}).get("acquisition_date") or (scan.metadata or {}).get("datetime")
        if meta_date and isinstance(meta_date, str):
            try:
                dt = datetime.fromisoformat(meta_date.replace("Z", "+00:00"))
                if dt.utcoffset() is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
        return None

    def _extract_detections(self, scan: Scan) -> list[dict[str, Any]]:
        """Safely extract detection records from metadata or disk detection results."""
        if scan.metadata and isinstance(scan.metadata.get("detections"), list):
            return list(scan.metadata["detections"])

        if scan.image_path:
            p = Path(scan.image_path).parent / "detection_results.json"
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data.get("detections"), list):
                        return list(data["detections"])
                except Exception as exc:
                    logger.debug("Failed reading detection_results.json for %s: %s", scan.folder_name, exc)
        return []


class GetAoiBaseline:
    """Retrieve the computed baseline model for an AOI."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        baseline_repository: AOIBaselineRepository,
    ) -> None:
        self._aoi_repository = aoi_repository
        self._baseline_repository = baseline_repository

    def execute(self, aoi_id: int) -> Optional[dict[str, Any]]:
        aoi = self._aoi_repository.get(aoi_id)
        if aoi is None:
            raise AreaOfInterestNotFoundError(f"Area of interest not found: {aoi_id}")

        baseline = self._baseline_repository.get(aoi_id)
        if baseline is None:
            return None
        return baseline.to_dict()
