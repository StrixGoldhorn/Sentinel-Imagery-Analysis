"""Application-owned contract for satellite pass prediction and mission analysis."""

from typing import Protocol, TypedDict, runtime_checkable

from sentinel_analysis.domain.entities import BoundingBox


class HistoricalMissionPass(TypedDict, total=False):
    """Details of a single historical Sentinel-1 SAR acquisition over an area."""

    product_id: str | None
    platform: str
    acquisition_time: str
    orbit_direction: str  # "ASCENDING" or "DESCENDING"
    relative_orbit: int | None
    polarisation: str | None
    instrument_mode: str | None


class MissionAnalysisSummary(TypedDict, total=False):
    """Aggregate statistics and repeat-cycle patterns from historical Sentinel-1 missions."""

    total_acquisitions: int
    first_acquisition: str | None
    latest_acquisition: str | None
    average_revisit_days: float | None
    dominant_tracks: list[int]
    ascending_count: int
    descending_count: int
    typical_utc_windows: dict[str, str]


class PassPrediction(TypedDict, total=False):
    """Provider-neutral satellite pass data returned to the application."""

    time: str
    max_elevation: float | int | None
    source: str | None  # "N2YO", "HISTORICAL_MISSION", "COMBINED"
    contribution: str | None  # "both", "n2yo", "historical"
    contribution_label: str | None  # e.g. "Both (N2YO + Historical)", "N2YO Tracking Only", "Historical Repeat Cycle Only"
    contribution_detail: str | None  # Description of factors contributing to forecast
    satellite: str | None  # e.g. "Sentinel-1A", "Sentinel-1C"
    orbit_direction: str | None  # "ASCENDING", "DESCENDING"
    relative_orbit: int | None
    confidence_score: float | None
    swath_mode: str | None
    historical_match: str | None


@runtime_checkable
class PassPredictor(Protocol):
    """Predict observable satellite passes over an area."""

    def predict(self, bbox: BoundingBox, api_key: str) -> list[PassPrediction]:
        ...


@runtime_checkable
class MissionPassAnalyzer(Protocol):
    """Analyze historical Sentinel-1 acquisitions and project orbital passes."""

    def analyze_history(
        self,
        bbox: BoundingBox,
        limit: int = 50,
    ) -> tuple[MissionAnalysisSummary, list[HistoricalMissionPass]]:
        ...

    def predict_from_history(
        self,
        bbox: BoundingBox,
        days_ahead: int = 10,
        limit: int = 20,
    ) -> list[PassPrediction]:
        ...
