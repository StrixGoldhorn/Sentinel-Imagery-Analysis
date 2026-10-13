"""Application use cases for SAR polarimetric preset generation and catalog discovery."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.imagery import PolarimetricProcessor
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.polarimetry import (
    PolarimetricPresetCatalog,
    PolarimetricPresetType,
)
from sentinel_analysis.domain.swipe_comparison import compute_bbox_intersection

logger = logging.getLogger(__name__)


class GetPolarimetricPresetsUseCase:
    """Discovers available polarimetric presets and temporal baseline candidates for a SAR scan."""

    def __init__(self, scan_repository: ScanRepository) -> None:
        self._scan_repo = scan_repository

    def execute(self, scan_id: str) -> dict[str, Any]:
        scan = self._scan_repo.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan '{scan_id}' not found")

        presets = [p.to_dict() for p in PolarimetricPresetCatalog.get_all()]

        # Identify prior baseline scans suitable for multi-temporal change detection
        baseline_candidates: list[dict[str, Any]] = []
        target_acq = scan.acquisition.acquired_at

        try:
            if hasattr(self._scan_repo, "list_recent"):
                recent_scans = self._scan_repo.list_recent(limit=50)
            else:
                recent_scans = self._scan_repo.list()
            for other in recent_scans:
                if other.folder_name == scan.folder_name:
                    continue

                other_acq = other.acquisition.acquired_at
                # Check spatial overlap
                _, overlap_ratio = compute_bbox_intersection(scan.bbox, other.bbox)
                if overlap_ratio >= 0.15:
                    time_diff_days = 0.0
                    if target_acq and other_acq:
                        time_diff_days = (target_acq - other_acq).total_seconds() / 86400.0

                    baseline_candidates.append({
                        "scan_id": other.folder_name,
                        "acquired_at": other_acq.isoformat() if other_acq else None,
                        "days_prior": round(time_diff_days, 2),
                        "overlap_pct": round(overlap_ratio * 100.0, 1),
                        "satellite": other.acquisition.satellite,
                    })

            # Sort candidate baselines by overlap and temporal proximity
            baseline_candidates.sort(key=lambda c: (c["overlap_pct"], -abs(c["days_prior"])), reverse=True)
        except Exception as exc:
            logger.debug("Failed discovering baseline scans for %s: %s", scan_id, exc)

        return {
            "scan_id": scan.folder_name,
            "satellite": scan.acquisition.satellite,
            "acquired_at": target_acq.isoformat() if target_acq else None,
            "available_polarizations": list(scan.acquisition.polarizations),
            "presets": presets,
            "baseline_candidates": baseline_candidates,
        }


class RenderPolarimetricPresetUseCase:
    """Renders a selected polarimetric preset for interactive SAR analysis."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        polarimetric_processor: PolarimetricProcessor,
    ) -> None:
        self._scan_repo = scan_repository
        self._processor = polarimetric_processor

    def execute(
        self,
        scan_id: str,
        preset_type: str,
        baseline_scan_id: Optional[str] = None,
        gamma: float = 1.0,
    ) -> bytes:
        clean_preset = (preset_type or "").lower().strip()
        if not PolarimetricPresetCatalog.is_valid(clean_preset):
            valid_keys = ", ".join(p.value for p in PolarimetricPresetType)
            raise ValueError(f"Unknown preset '{preset_type}'. Valid options: {valid_keys}")

        scan = self._scan_repo.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan '{scan_id}' not found")

        image_path = Path(scan.image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Scan image not found at {image_path}")

        # Check for dedicated polarization files in scan directory
        channel_paths: dict[str, Path | str] = {}
        scan_img_dir = image_path.parent
        for pol in ("vv", "vh", "hh", "hv"):
            pol_file = scan_img_dir / f"{pol}.png"
            if pol_file.is_file():
                channel_paths[pol] = pol_file

        prior_image_path: Optional[Path] = None
        if clean_preset == PolarimetricPresetType.CHANGE_DETECTION.value:
            if baseline_scan_id:
                baseline_scan = self._scan_repo.get(baseline_scan_id)
                if baseline_scan and Path(baseline_scan.image_path).is_file():
                    prior_image_path = Path(baseline_scan.image_path)
            else:
                # Auto-resolve most recent overlapping prior scan
                try:
                    if hasattr(self._scan_repo, "list_recent"):
                        candidates = self._scan_repo.list_recent(limit=30)
                    else:
                        candidates = self._scan_repo.list()
                    for c in candidates:
                        if c.folder_name != scan.folder_name:
                            _, ratio = compute_bbox_intersection(scan.bbox, c.bbox)
                            if ratio >= 0.20 and Path(c.image_path).is_file():
                                prior_image_path = Path(c.image_path)
                                break
                except Exception as exc:
                    logger.debug("Auto baseline resolution failed: %s", exc)

        return self._processor.render_preset(
            image_path=image_path,
            preset_type=clean_preset,
            prior_image_path=prior_image_path,
            channel_paths=channel_paths if channel_paths else None,
            gamma=gamma,
        )
