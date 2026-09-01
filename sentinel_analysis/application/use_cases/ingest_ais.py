"""Authenticate, fetch, normalize, and persist AIS data by provider."""

from datetime import datetime, timezone

from sentinel_analysis.application.exceptions import PluginNotFoundError
from sentinel_analysis.application.ports.ais import AISPluginRegistry, AISTimeRange
from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.application.results import IngestionLog, IngestionResult, IngestionStatus
from sentinel_analysis.domain.entities import BoundingBox


class IngestAIS:
    def __init__(self, registry: AISPluginRegistry, repository: AISRepository) -> None:
        self._registry = registry
        self._repository = repository

    @staticmethod
    def _normalize_time_range(time_range: AISTimeRange) -> AISTimeRange:
        start, end = time_range

        def as_utc(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            if not isinstance(value, datetime):
                raise ValueError("AIS time range values must be datetimes or None")
            if value.utcoffset() is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        normalized = as_utc(start), as_utc(end)
        if normalized[0] is not None and normalized[1] is not None and normalized[0] > normalized[1]:
            raise ValueError("AIS time-range start must not be after its end")
        return normalized

    def execute(
        self,
        bbox: BoundingBox,
        time_range: AISTimeRange,
        plugin_name: str | None = None,
    ) -> IngestionResult:
        normalized_time_range = self._normalize_time_range(time_range)
        if plugin_name is not None:
            if not isinstance(plugin_name, str) or not plugin_name.strip():
                raise ValueError("AIS plugin name must be a non-empty string")
            plugin_name = plugin_name.strip()

        results: list[IngestionLog] = []
        total_inserted = 0

        plugins = self._registry.get_plugins(plugin_name)
        if plugin_name is not None and not plugins:
            raise PluginNotFoundError(f"Unknown AIS plugin: {plugin_name}")

        configs = {}
        if hasattr(self._repository, "get_all_scraper_configs"):
            try:
                configs = self._repository.get_all_scraper_configs()
            except Exception:
                configs = {}

        for plugin in plugins:
            # If multi-provider ingestion and plugin is explicitly disabled in config, skip
            if plugin_name is None and configs.get(plugin.name) is False:
                continue

            inserted = 0
            try:
                plugin.authenticate()
                records = list(plugin.fetch(bbox, normalized_time_range))
                inserted = self._repository.save_records(records, plugin.name)
                status: IngestionStatus = "SUCCESS"
                error = None
            except Exception as exc:

                # A provider failure is isolated so the remaining configured
                # providers still get a chance to run.
                status = "FAILED"
                error = str(exc)

            self._repository.log_execution(plugin.name, status, inserted, error)
            results.append({"plugin": plugin.name, "status": status, "records": inserted, "error": error})
            total_inserted += inserted

        return IngestionResult(total_inserted=total_inserted, logs=results)
