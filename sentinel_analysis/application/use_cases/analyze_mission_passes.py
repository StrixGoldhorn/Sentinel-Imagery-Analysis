"""Use case for analyzing historical Sentinel-1 acquisitions over an Area of Interest."""

from typing import Any

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.satellite import (
    HistoricalMissionPass,
    MissionAnalysisSummary,
    MissionPassAnalyzer,
)
from sentinel_analysis.domain.entities import BoundingBox


class AnalyzeMissionPasses:
    """Extracts and analyzes historical Sentinel-1 mission acquisitions over an AOI."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        mission_analyzer: MissionPassAnalyzer,
    ) -> None:
        self._aois = aoi_repository
        self._analyzer = mission_analyzer

    def execute(self, aoi_id: int, limit: int = 100) -> dict[str, Any]:
        if isinstance(aoi_id, bool) or not isinstance(aoi_id, int) or aoi_id <= 0:
            raise ValueError("Area-of-interest ID must be a positive integer")

        aoi = self._aois.get(aoi_id)
        if aoi is None:
            raise AreaOfInterestNotFoundError(f"Area of interest not found: {aoi_id}")

        summary, history = self._analyzer.analyze_history(aoi.bbox, limit=limit)

        return {
            "aoi_id": aoi.id,
            "name": aoi.name,
            "bbox": aoi.bbox.as_list(),
            "mission_analysis": summary,
            "historical_passes": history,
        }

    def analyze_bbox(
        self,
        bbox: BoundingBox,
        limit: int = 100,
    ) -> tuple[MissionAnalysisSummary, list[HistoricalMissionPass]]:
        return self._analyzer.analyze_history(bbox, limit=limit)
