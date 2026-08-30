"""Scrape AIS data for an Area of Interest within -5min to +5min of its predicted satellite pass."""

from datetime import datetime, timedelta, timezone

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.application.ports.ais import AISTimeRange
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.results import IngestionResult
from sentinel_analysis.application.use_cases.ingest_ais import IngestAIS


def calculate_pass_window(
    pass_time: datetime,
    window_minutes: int = 5,
) -> AISTimeRange:
    """Compute the [-window_minutes, +window_minutes] UTC time range around a satellite pass."""
    if not isinstance(pass_time, datetime):
        raise ValueError("Pass time must be a datetime instance")
    if pass_time.utcoffset() is None:
        pass_time = pass_time.replace(tzinfo=timezone.utc)
    utc_pass = pass_time.astimezone(timezone.utc)
    delta = timedelta(minutes=max(1, int(window_minutes)))
    return (utc_pass - delta, utc_pass + delta)


class ScrapeAreaOfInterestAIS:
    """Scrapes AIS provider data for an AOI around its predicted satellite pass (-5min to +5min)."""

    def __init__(
        self,
        aoi_repository: AreaOfInterestRepository,
        ingest_ais: IngestAIS,
    ) -> None:
        self._aoi_repository = aoi_repository
        self._ingest_ais = ingest_ais

    def execute(
        self,
        aoi_id: int,
        plugin_name: str | None = None,
        pass_time: datetime | None = None,
        window_minutes: int = 5,
    ) -> IngestionResult:
        if isinstance(aoi_id, bool) or not isinstance(aoi_id, int) or aoi_id <= 0:
            raise ValueError("Area-of-interest ID must be a positive integer")

        aoi = self._aoi_repository.get(aoi_id)
        if aoi is None:
            raise AreaOfInterestNotFoundError(f"Area of interest not found: {aoi_id}")

        target_pass = pass_time or aoi.next_scan or datetime.now(timezone.utc)
        time_range = calculate_pass_window(target_pass, window_minutes=window_minutes)

        return self._ingest_ais.execute(
            aoi.bbox,
            time_range,
            plugin_name=plugin_name,
        )
