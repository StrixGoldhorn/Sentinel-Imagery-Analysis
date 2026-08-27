"""AIS ingestion use case."""

from datetime import datetime

from sentinel_analysis.application.ports.providers import AISPluginRegistry
from sentinel_analysis.application.ports.repositories import AISRepository
from sentinel_analysis.domain.entities import BoundingBox


class IngestAIS:
    def __init__(self, registry: AISPluginRegistry, repository: AISRepository) -> None:
        self._registry = registry
        self._repository = repository

    def execute(
        self,
        bbox: BoundingBox,
        time_range: tuple[datetime | None, datetime | None],
        plugin_name: str | None = None,
    ) -> dict[str, object]:
        results: list[dict[str, object]] = []
        total_inserted = 0

        plugins = self._registry.get_plugins(plugin_name)
        if plugin_name is not None and not plugins:
            raise ValueError(f"Unknown AIS plugin: {plugin_name}")

        for plugin in plugins:
            inserted = 0
            try:
                plugin.authenticate()
                records = list(plugin.fetch(bbox, time_range))
                inserted = self._repository.save_records(records, plugin.name)
                status = "SUCCESS"
                error = None
            except Exception as exc:
                status = "FAILED" if inserted == 0 else "PARTIAL"
                error = str(exc)

            self._repository.log_execution(plugin.name, status, inserted, error)
            results.append({"plugin": plugin.name, "status": status, "records": inserted, "error": error})
            total_inserted += inserted

        return {"total_inserted": total_inserted, "logs": results}
