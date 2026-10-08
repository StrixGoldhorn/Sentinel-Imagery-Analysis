"""Computer-vision adapters."""

from sentinel_analysis.infrastructure.detection.cfar import (
    ca_cfar_2d,
    fuse_dual_polarization,
    go_cfar_2d,
    so_cfar_2d,
)
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.detection.detection_saver import save_detection_results
from sentinel_analysis.infrastructure.detection.wake import (
    ShipWakeDetector,
    WakeAnalysisResult,
    compute_radon_transform,
)

__all__ = [
    "ClassicalShipDetector",
    "save_detection_results",
    "ca_cfar_2d",
    "go_cfar_2d",
    "so_cfar_2d",
    "fuse_dual_polarization",
    "ShipWakeDetector",
    "WakeAnalysisResult",
    "compute_radon_transform",
]
