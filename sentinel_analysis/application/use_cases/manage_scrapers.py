"""Use cases for managing AIS scraper plugin activation, configurations, and logs."""

from typing import Any, Optional

from sentinel_analysis.application.exceptions import PluginNotFoundError
from sentinel_analysis.application.ports.ais import AISPluginRegistry
from sentinel_analysis.application.ports.ais_repository import AISRepository


class ListScrapers:
    """Lists all configured AIS scraper plugins with active state, metadata, and performance stats."""

    def __init__(
        self,
        registry: AISPluginRegistry,
        repository: AISRepository,
    ) -> None:
        self._registry = registry
        self._repository = repository

    def execute(self) -> dict[str, Any]:
        all_meta = []
        if hasattr(self._registry, "list_all_metadata"):
            all_meta = self._registry.list_all_metadata()
        else:
            for p in self._registry.get_plugins():
                all_meta.append({
                    "name": p.name,
                    "display_name": p.name,
                    "category": "Custom",
                    "description": "AIS plugin",
                    "requires_network": True,
                    "default_enabled": True,
                })

        configs = {}
        if hasattr(self._repository, "get_all_scraper_configs"):
            configs = self._repository.get_all_scraper_configs()

        stats = {}
        if hasattr(self._repository, "get_scraper_stats"):
            stats = self._repository.get_scraper_stats()

        scrapers = []
        total_runs_all = 0
        total_records_all = 0
        total_success_all = 0

        for meta in all_meta:
            name = meta["name"]
            default_enabled = meta.get("default_enabled", True)
            enabled = configs.get(name, default_enabled)

            p_stats = stats.get(name, {})
            total_runs = p_stats.get("total_runs", 0)
            total_records = p_stats.get("total_records", 0)
            success_runs = p_stats.get("success_runs", 0)
            failed_runs = p_stats.get("failed_runs", 0)
            last_run_at = p_stats.get("last_run_at")

            success_rate = (success_runs / total_runs * 100.0) if total_runs > 0 else 100.0

            total_runs_all += total_runs
            total_records_all += total_records
            total_success_all += success_runs

            scrapers.append({
                "name": name,
                "display_name": meta.get("display_name", name),
                "category": meta.get("category", "General"),
                "description": meta.get("description", ""),
                "requires_network": meta.get("requires_network", True),
                "enabled": enabled,
                "total_runs": total_runs,
                "total_records": total_records,
                "success_runs": success_runs,
                "failed_runs": failed_runs,
                "success_rate": round(success_rate, 1),
                "last_run_at": last_run_at,
            })

        active_count = sum(1 for s in scrapers if s["enabled"])
        overall_success_rate = (
            round(total_success_all / total_runs_all * 100.0, 1) if total_runs_all > 0 else 100.0
        )

        return {
            "scrapers": scrapers,
            "metrics": {
                "total_scrapers": len(scrapers),
                "active_scrapers": active_count,
                "total_records_ingested": total_records_all,
                "overall_success_rate": overall_success_rate,
                "total_runs": total_runs_all,
            },
        }


class ToggleScraper:
    """Toggles the enabled/disabled state of an AIS scraper plugin."""

    def __init__(
        self,
        registry: AISPluginRegistry,
        repository: AISRepository,
    ) -> None:
        self._registry = registry
        self._repository = repository

    def execute(self, plugin_name: str, enabled: bool) -> dict[str, Any]:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("Scraper plugin name must be a non-empty string")
        plugin_name = plugin_name.strip()

        plugins = self._registry.get_plugins(plugin_name)
        if not plugins:
            raise PluginNotFoundError(f"Unknown AIS plugin: {plugin_name}")

        self._repository.set_scraper_config(plugin_name, bool(enabled))
        return {
            "plugin_name": plugin_name,
            "enabled": bool(enabled),
        }


class GetScraperLogsUseCase:
    """Retrieves paginated, filterable scraper execution audit logs."""

    def __init__(self, repository: AISRepository) -> None:
        self._repository = repository

    def execute(
        self,
        plugin_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        logs = self._repository.get_scraper_logs(
            plugin_name=plugin_name,
            status=status,
            limit=limit,
            offset=offset,
        )

        stats = {}
        if hasattr(self._repository, "get_scraper_stats"):
            stats = self._repository.get_scraper_stats()

        total_runs = sum(s.get("total_runs", 0) for s in stats.values())
        total_records = sum(s.get("total_records", 0) for s in stats.values())
        total_success = sum(s.get("success_runs", 0) for s in stats.values())
        overall_rate = round(total_success / total_runs * 100.0, 1) if total_runs > 0 else 100.0

        return {
            "logs": logs,
            "count": len(logs),
            "stats": stats,
            "metrics": {
                "total_runs": total_runs,
                "total_records": total_records,
                "overall_success_rate": overall_rate,
            },
        }
