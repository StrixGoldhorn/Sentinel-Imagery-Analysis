"""Domain models and value objects for SAR radiometry, histogram, and filtering."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DespeckleFilter(str, Enum):
    NONE = "none"
    LEE = "lee"
    FROST = "frost"
    MEDIAN = "median"
    BOXCAR = "boxcar"


@dataclass(frozen=True)
class HistogramData:
    """SAR pixel intensity distribution and radiometric statistics."""

    bins: list[int]
    counts: list[int]
    cdf: list[float]
    min_val: float
    max_val: float
    mean: float
    std_dev: float
    percentiles: dict[str, float]
    polarization: Optional[str] = None
    total_pixels: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bins": self.bins,
            "counts": self.counts,
            "cdf": self.cdf,
            "min_val": round(self.min_val, 2),
            "max_val": round(self.max_val, 2),
            "mean": round(self.mean, 2),
            "std_dev": round(self.std_dev, 2),
            "percentiles": {k: round(v, 2) for k, v in self.percentiles.items()},
            "polarization": self.polarization,
            "total_pixels": self.total_pixels,
        }


@dataclass
class RadiometryAdjustment:
    """User-controlled radiometry enhancement parameters."""

    contrast_min: float = 0.0
    contrast_max: float = 255.0
    gamma: float = 1.0
    log_stretch: bool = False
    despeckle_filter: str = DespeckleFilter.NONE.value
    filter_kernel_size: int = 5
    polarization: Optional[str] = None

    def __post_init__(self) -> None:
        self.contrast_min = max(0.0, min(254.0, float(self.contrast_min)))
        self.contrast_max = max(self.contrast_min + 1.0, min(255.0, float(self.contrast_max)))
        self.gamma = max(0.1, min(5.0, float(self.gamma)))
        # Kernel size must be an odd integer >= 3
        k = int(self.filter_kernel_size)
        if k < 3:
            k = 3
        elif k % 2 == 0:
            k += 1
        self.filter_kernel_size = min(15, k)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contrast_min": self.contrast_min,
            "contrast_max": self.contrast_max,
            "gamma": self.gamma,
            "log_stretch": self.log_stretch,
            "despeckle_filter": self.despeckle_filter,
            "filter_kernel_size": self.filter_kernel_size,
            "polarization": self.polarization,
        }


class RadiometryPreset:
    """Standard radiometry configurations optimized for SAR maritime analysis."""

    @staticmethod
    def get_presets() -> dict[str, RadiometryAdjustment]:
        return {
            "DEFAULT": RadiometryAdjustment(
                contrast_min=0.0,
                contrast_max=255.0,
                gamma=1.0,
                log_stretch=False,
                despeckle_filter="none",
            ),
            "HIGH_CONTRAST": RadiometryAdjustment(
                contrast_min=20.0,
                contrast_max=220.0,
                gamma=0.85,
                log_stretch=False,
                despeckle_filter="none",
            ),
            "LEE_DESPECKLE": RadiometryAdjustment(
                contrast_min=10.0,
                contrast_max=245.0,
                gamma=1.0,
                log_stretch=False,
                despeckle_filter="lee",
                filter_kernel_size=5,
            ),
            "DARK_VESSEL_BOOST": RadiometryAdjustment(
                contrast_min=15.0,
                contrast_max=190.0,
                gamma=1.35,
                log_stretch=True,
                despeckle_filter="median",
                filter_kernel_size=3,
            ),
            "OCEAN_CLUTTER_SUPPRESS": RadiometryAdjustment(
                contrast_min=40.0,
                contrast_max=250.0,
                gamma=0.75,
                log_stretch=False,
                despeckle_filter="frost",
                filter_kernel_size=5,
            ),
        }
