"""Use case for generating decision-ready maritime intelligence briefs."""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.briefing import IntelligenceBriefGenerator
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import Scan

logger = logging.getLogger(__name__)


class GenerateIntelligenceBrief:
    """Orchestrates generation of comprehensive PDF intelligence briefing packets."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        brief_generator: IntelligenceBriefGenerator,
    ) -> None:
        self._scan_repository = scan_repository
        self._brief_generator = brief_generator

    def execute(self, folder_name: str, target_path: Optional[Path] = None) -> Path:
        """Generate and save the PDF intelligence briefing packet for the given scan."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")

        if target_path is None:
            image_path = Path(scan.image_path)
            target_path = image_path.parent / f"{scan.folder_name}_briefing.pdf"

        return self._brief_generator.generate_brief(scan, target_path)

    def generate_summary(self, folder_name: str) -> dict[str, Any]:
        """Compute an intelligence executive summary without rendering the full PDF."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")

        metadata = scan.metadata or {}
        detections = metadata.get("detections", [])

        total_vessels = len(detections)
        dark_vessels = sum(1 for d in detections if isinstance(d, dict) and d.get("is_dark", True))
        ais_correlated = total_vessels - dark_vessels
        dark_ratio = (dark_vessels / total_vessels * 100.0) if total_vessels > 0 else 0.0

        solas_violations = 0
        high_risk_targets: list[dict[str, Any]] = []

        for idx, d in enumerate(detections):
            if not isinstance(d, dict):
                continue
            is_dark = d.get("is_dark", True)
            length = float(d.get("length_m", 0) or 0)
            confidence = float(d.get("confidence", 0) or 0)

            # IMO SOLAS Chapter V Regulation 19: All passenger ships & cargo ships >= 300 GT
            # typically corresponding to length >= 45-50 meters must broadcast AIS.
            is_solas_suspect = is_dark and (length >= 45.0)
            if is_solas_suspect:
                solas_violations += 1

            if is_dark and (length >= 40.0 or confidence >= 0.85):
                high_risk_targets.append({
                    "id": idx + 1,
                    "latitude": d.get("latitude"),
                    "longitude": d.get("longitude"),
                    "length_m": length,
                    "confidence": confidence,
                    "is_solas_suspect": is_solas_suspect,
                })

        return {
            "scan_folder": scan.folder_name,
            "aoi_name": metadata.get("aoi_name", "Target Area"),
            "acquisition_time": scan.acquisition.acquired_at.isoformat(),
            "satellite": scan.acquisition.satellite,
            "total_vessels": total_vessels,
            "dark_vessels": dark_vessels,
            "ais_correlated": ais_correlated,
            "dark_percentage": round(dark_ratio, 1),
            "solas_suspect_count": solas_violations,
            "high_risk_count": len(high_risk_targets),
            "high_risk_targets": high_risk_targets,
        }
