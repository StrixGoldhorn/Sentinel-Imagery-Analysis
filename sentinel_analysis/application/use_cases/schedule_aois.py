from datetime import datetime, timedelta, timezone
from typing import Optional

from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.satellite import PassPredictor
from sentinel_analysis.application.use_cases.create_scan import CreateScan
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS
from sentinel_analysis.domain.entities import AreaOfInterest


class CheckAndScheduleAOIs:
    """Evaluate active AOIs, update satellite pass forecasts, and trigger automated scans / AIS scrapes."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        pass_predictor: PassPredictor,
        create_scan: Optional[CreateScan] = None,
        ingest_ais: Optional[IngestAIS] = None,
    ) -> None:
        self._aois = aoi_repository
        self._predictor = pass_predictor
        self._create_scan = create_scan
        self._ingest_ais = ingest_ais

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

                ais_records_scraped = 0
                if next_pass:
                    self._aois.update_prediction(aoi.id, next_pass, now)

                    # When an automated satellite pass occurs / is active (within pass window),
                    # trigger scrapers across the -5min to +5min window around the pass
                    if self._ingest_ais is not None and abs((next_pass - now).total_seconds()) <= 300:
                        start_time = next_pass - timedelta(minutes=5)
                        end_time = next_pass + timedelta(minutes=5)
                        ingest_res = self._ingest_ais.execute(aoi.bbox, (start_time, end_time))
                        ais_records_scraped = ingest_res["total_inserted"]

                results.append({
                    "aoi_id": aoi.id,
                    "name": aoi.name,
                    "next_pass": next_pass.isoformat() if next_pass else None,
                    "ais_records": ais_records_scraped,
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
