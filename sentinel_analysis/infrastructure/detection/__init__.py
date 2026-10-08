"""Computer-vision adapters."""

from sentinel_analysis.infrastructure.detection.cfar import (
    ca_cfar_2d,
    fuse_dual_polarization,
    go_cfar_2d,
    so_cfar_2d,
)
from sentinel_analysis.infrastructure.detection.classical import ClassicalShipDetector
from sentinel_analysis.infrastructure.detection.onnx_detector import (
    DeepLearningShipDetector,
    InferenceBackend,
    MockONNXBackend,
    ONNXRuntimeBackend,
    OpenCVDNNBackend,
    SARCNNClassifier,
    VESSEL_CLASSES,
)
from sentinel_analysis.infrastructure.detection.wake import (
    ShipWakeDetector,
    WakeAnalysisResult,
    compute_radon_transform,
)

__all__ = [
    "ClassicalShipDetector",
    "DeepLearningShipDetector",
    "SARCNNClassifier",
    "InferenceBackend",
    "OpenCVDNNBackend",
    "ONNXRuntimeBackend",
    "MockONNXBackend",
    "VESSEL_CLASSES",
    "save_detection_results",
    "ca_cfar_2d",
    "go_cfar_2d",
    "so_cfar_2d",
    "fuse_dual_polarization",
    "ShipWakeDetector",
    "WakeAnalysisResult",
    "compute_radon_transform",
]
