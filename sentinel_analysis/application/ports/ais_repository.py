from datetime import datetime
from typing import Iterable, Protocol, runtime_checkable

from sentinel_analysis.domain.entities import AISRecord, BoundingBox


@runtime_checkable
class AISRepository(Protocol):
    """Persist AIS records and ingestion execution outcomes."""

    def save_records(self, records: Iterable[AISRecord], source_plugin: str) -> int:
        ...

    def log_execution(
        self,
        plugin_name: str,
        status: str,
        records_inserted: int,
        error_message: str | None = None,
    ) -> None:
        ...

    def get_vessel_positions(
        self,
        bbox: BoundingBox | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        limit: int = 500,
        latest_only: bool = True,
    ) -> list[dict]:
        ...

    def get_timeline_bounds(self) -> dict[str, object]:
        ...

    def get_vessel_by_id(self, vessel_id: int) -> dict | None:
        ...

    def update_vessel(
        self,
        vessel_id: int,
        name: str | None = None,
        vessel_type: str | None = None,
        callsign: str | None = None,
        imo: str | None = None,
    ) -> dict | None:
        ...

    def get_scraper_config(self, plugin_name: str) -> dict | None:
        ...

    def get_scraper_detail(self, plugin_name: str) -> dict | None:
        ...

    def get_all_scraper_configs(self) -> dict[str, bool]:
        ...

    def set_scraper_config(self, plugin_name: str, enabled: bool) -> None:
        ...

    def get_scraper_logs(
        self,
        plugin_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        ...

    def get_scraper_stats(self) -> dict[str, dict]:
        ...

    def update_scraper_settings(self, plugin_name: str, config: dict) -> None:
        ...

    def update_scraper(
        self,
        plugin_name: str,
        enabled: bool | None = None,
        description: str | None = None,
        tag: str | None = None,
        config: dict | None = None,
    ) -> dict | None:
        ...

    def record_scraper_failure(
        self,
        plugin_name: str,
        reason: str,
        cooldown_until: datetime | None,
        consecutive_failures: int,
    ) -> None:
        ...

    def record_scraper_success(self, plugin_name: str) -> None:
        ...

    def reset_scraper_cooldown(self, plugin_name: str) -> None:
        ...


