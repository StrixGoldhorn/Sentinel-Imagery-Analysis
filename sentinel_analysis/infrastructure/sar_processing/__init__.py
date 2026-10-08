"""SAR processing and multi-temporal coherence infrastructure adapters."""

from sentinel_analysis.infrastructure.sar_processing.change_detection import (
    NumpySARChangeDetector,
)

__all__ = ["NumpySARChangeDetector"]
