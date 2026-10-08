"""Application port for Multi-Temporal SAR Change Detection."""

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SARChangeDetector(Protocol):
    """Protocol for SAR log-ratio coherence and change analysis."""

    def compute_change_map(
        self,
        reference_image_path: Path | str,
        target_image_path: Path | str,
        output_path: Path | str,
        *,
        threshold_db: float = 4.5,
    ) -> dict[str, Any]:
        """Compute log-ratio difference map and identify change targets."""
        ...
