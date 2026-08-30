"""Orchestration for automated satellite pass monitoring and AOI capture."""

from datetime import datetime, timezone
from typing import Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.domain.entities import AreaOfInterest




class CheckAndScheduleAOIs:
    """Evaluate active AOIs, update satellite pass forecasts, and trigger automated scans."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        pass_predictor: PassPredictor,
        create_scan: Optional[CreateScan] = None,
    ) -> None:
        self._aois = aoi_repository
        self._predictor = pass_predictor
        self._create_scan = create_scan

    def execute(self, api_key: str) -> list[dict[str, object]]:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Satellite prediction API key is required")
        api_key = api_key.strip()
        now = datetime.now(timezone.utc)
        results: list[dict[str, object]] = []

        for aoi in self._aois.list():
            if not getattr(aoi, "auto_capture_enabled", False):
                continue

            try:
                raw_predictions = self._predictor.predict(aoi.bbox, api_key)
                valid_passes: list[datetime] = []
                for pred in raw_predictions:
                    pass_time_raw = pred.get("time")
                    if pass_time_raw:
                        p_time = datetime.fromisoformat(str(pass_time_raw).replace("Z", "+00:00"))
                        valid_passes.append(p_time.astimezone(timezone.utc))

                valid_passes.sort()
                next_pass = next((p for p in valid_passes if p >= now), None)

                if next_pass:
                    self._aois.update_prediction(aoi.id, next_pass, now)

                results.append({
                    "aoi_id": aoi.id,
                    "name": aoi.name,
                    "next_pass": next_pass.isoformat() if next_pass else None,
                    "status": "SCHEDULED",
                })
            except Exception as exc:
                results.append({
                    "aoi_id": aoi.id,
                    "name": aoi.name,
                    "error": str(exc),
                    "status": "ERROR",
                })

        return results
