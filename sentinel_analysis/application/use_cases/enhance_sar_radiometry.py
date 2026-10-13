"""Application use cases for SAR histogram extraction and radiometry enhancement."""

import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.imagery import SarRadiometryProcessor
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.sar_radiometry import (
    HistogramData,
    RadiometryAdjustment,
    RadiometryPreset,
)

logger = logging.getLogger(__name__)


class GetScanHistogram:
    """Retrieve SAR pixel intensity histogram and radiometry statistics for a scan."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        radiometry_processor: SarRadiometryProcessor,
    ) -> None:
        self._scan_repo = scan_repository
        self._processor = radiometry_processor

    def execute(self, scan_id: str, polarization: Optional[str] = None) -> dict[str, Any]:
        scan = self._scan_repo.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan '{scan_id}' not found")

        image_path = Path(scan.image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Scan image not found at {image_path}")

        # If polarization not specified, default to first available
        pol = polarization
        if not pol and scan.acquisition.polarizations:
            pol = scan.acquisition.polarizations[0]

        hist_data: HistogramData = self._processor.compute_histogram(
            image_path=image_path,
            polarization=pol,
        )

        presets = {k: v.to_dict() for k, v in RadiometryPreset.get_presets().items()}

        return {
            "scan_id": scan.folder_name,
            "polarization": pol,
            "available_polarizations": list(scan.acquisition.polarizations),
            "histogram": hist_data.to_dict(),
            "presets": presets,
        }


class EnhanceScanRadiometry:
    """Apply interactive radiometry adjustments and return enhanced SAR image bytes."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        radiometry_processor: SarRadiometryProcessor,
    ) -> None:
        self._scan_repo = scan_repository
        self._processor = radiometry_processor

    def execute(self, scan_id: str, adjustment: RadiometryAdjustment) -> bytes:
        scan = self._scan_repo.get(scan_id)
        if scan is None:
            raise FileNotFoundError(f"Scan '{scan_id}' not found")

        image_path = Path(scan.image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Scan image not found at {image_path}")

        return self._processor.enhance_radiometry(
            image_path=image_path,
            adjustment=adjustment,
        )
