"""Use cases for managing AIS scraper plugin activation, configurations, and logs."""

from datetime import datetime, timezone
from typing import Any, Optional

from sentinel_analysis.application.exceptions import PluginNotFoundError
from sentinel_analysis.application.ports.ais import AISPluginRegistry
from sentinel_analysis.application.ports.ais_repository import AISRepository


def _parse_cooldown_datetime(cooldown_val: Any) -> datetime | None:
    if not cooldown_val:
        return None
    if isinstance(cooldown_val, datetime):
        if cooldown_val.utcoffset() is None:
            return cooldown_val.replace(tzinfo=timezone.utc)
        return cooldown_val.astimezone(timezone.utc)
    if isinstance(cooldown_val, str):
        try:
            dt = datetime.fromisoformat(cooldown_val.replace("Z", "+00:00"))
            if dt.utcoffset() is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


class ListScrapers:
    """Lists all configured AIS scraper plugins with active state, metadata, configuration, and performance stats."""

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

        details = {}
        if hasattr(self._repository, "get_all_scraper_details"):
            try:
                details = self._repository.get_all_scraper_details()
            except Exception:
                details = {}
        elif hasattr(self._repository, "get_all_scraper_configs"):
            try:
                simple_configs = self._repository.get_all_scraper_configs()
                for name, enabled in simple_configs.items():
                    details[name] = {"enabled": enabled}
            except Exception:
                details = {}

        stats = {}
        if hasattr(self._repository, "get_scraper_stats"):
            stats = self._repository.get_scraper_stats()

        now = datetime.now(timezone.utc)
        scrapers = []
        total_runs_all = 0
        total_records_all = 0
        total_success_all = 0

        for meta in all_meta:
            name = meta["name"]
            p_detail = details.get(name, {})
            default_enabled = meta.get("default_enabled", True)
            enabled = p_detail.get("enabled", default_enabled) if "enabled" in p_detail else default_enabled
            config = p_detail.get("config", {})
            cooldown_raw = p_detail.get("cooldown_until")
            cooldown_dt = _parse_cooldown_datetime(cooldown_raw)
            consecutive_failures = p_detail.get("consecutive_failures", 0)
            last_failure_reason = p_detail.get("last_failure_reason")

            is_cooling_down = False
            remaining_seconds = 0
            if cooldown_dt and cooldown_dt > now:
                is_cooling_down = True
                remaining_seconds = max(0, int((cooldown_dt - now).total_seconds()))

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

            tag = p_detail.get("tag") or meta.get("category", "General")
            category = tag

            scrapers.append({
                "name": name,
                "display_name": meta.get("display_name", name),
                "category": category,
                "tag": tag,
                "default_category": meta.get("category", "General"),
                "description": p_detail.get("description") or meta.get("description", ""),
                "default_description": meta.get("description", ""),
                "requires_network": meta.get("requires_network", True),
                "enabled": enabled,
                "config": config,
                "cooldown_until": cooldown_dt.isoformat() if cooldown_dt else None,
                "is_cooling_down": is_cooling_down,
                "cooldown_remaining_seconds": remaining_seconds,
                "consecutive_failures": consecutive_failures,
                "last_failure_reason": last_failure_reason,
                "total_runs": total_runs,
                "total_records": total_records,
                "success_runs": success_runs,
                "failed_runs": failed_runs,
                "success_rate": round(success_rate, 1),
                "last_run_at": last_run_at,
            })

        active_count = sum(1 for s in scrapers if s["enabled"])
        cooling_count = sum(1 for s in scrapers if s["is_cooling_down"])
        overall_success_rate = (
            round(total_success_all / total_runs_all * 100.0, 1) if total_runs_all > 0 else 100.0
        )

        return {
            "scrapers": scrapers,
            "metrics": {
                "total_scrapers": len(scrapers),
                "active_scrapers": active_count,
                "cooling_scrapers": cooling_count,
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


class UpdateScraperConfig:
    """Updates custom network and authentication settings for an AIS scraper plugin."""

    def __init__(
        self,
        registry: AISPluginRegistry,
        repository: AISRepository,
    ) -> None:
        self._registry = registry
        self._repository = repository

    def execute(self, plugin_name: str, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("Scraper plugin name must be a non-empty string")
        plugin_name = plugin_name.strip()

        plugins = self._registry.get_plugins(plugin_name)
        if not plugins:
            raise PluginNotFoundError(f"Unknown AIS plugin: {plugin_name}")

        if not isinstance(config, dict):
            raise ValueError("Config must be a dictionary")

        self._repository.update_scraper_settings(plugin_name, config)
        for p in plugins:
            if hasattr(p, "configure"):
                try:
                    p.configure(config)
                except Exception:
                    pass

        return {
            "plugin_name": plugin_name,
            "status": "updated",
            "config": config,
        }


class ResetScraperCooldown:
    """Resets the rate-limiting cooldown and failure counter for a scraper plugin."""

    def __init__(
        self,
        registry: AISPluginRegistry,
        repository: AISRepository,
    ) -> None:
        self._registry = registry
        self._repository = repository

    def execute(self, plugin_name: str) -> dict[str, Any]:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("Scraper plugin name must be a non-empty string")
        plugin_name = plugin_name.strip()

        plugins = self._registry.get_plugins(plugin_name)
        if not plugins:
            raise PluginNotFoundError(f"Unknown AIS plugin: {plugin_name}")

        self._repository.reset_scraper_cooldown(plugin_name)
        return {
            "plugin_name": plugin_name,
            "status": "reset",
        }


class GetScraperDetail:
    """Retrieves full details, configuration, status, and stats for a single AIS scraper plugin."""

    def __init__(
        self,
        registry: AISPluginRegistry,
        repository: AISRepository,
    ) -> None:
        self._registry = registry
        self._repository = repository

    def execute(self, plugin_name: str) -> dict[str, Any]:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("Scraper plugin name must be a non-empty string")
        plugin_name = plugin_name.strip()

        plugins = self._registry.get_plugins(plugin_name)
        if not plugins:
            raise PluginNotFoundError(f"Unknown AIS plugin: {plugin_name}")

        meta = {}
        if hasattr(self._registry, "get_plugin_metadata"):
            meta = self._registry.get_plugin_metadata(plugin_name)
        else:
            meta = {
                "name": plugin_name,
                "display_name": plugin_name,
                "category": "Custom",
                "description": "",
                "requires_network": True,
                "default_enabled": True,
            }

        p_detail = {}
        if hasattr(self._repository, "get_scraper_detail"):
            p_detail = self._repository.get_scraper_detail(plugin_name) or {}
        elif hasattr(self._repository, "get_scraper_config"):
            p_detail = self._repository.get_scraper_config(plugin_name) or {}

        default_enabled = meta.get("default_enabled", True)
        enabled = p_detail.get("enabled", default_enabled) if "enabled" in p_detail else default_enabled
        config = p_detail.get("config", {})
        description = p_detail.get("description") or meta.get("description", "")
        cooldown_raw = p_detail.get("cooldown_until")
        cooldown_dt = _parse_cooldown_datetime(cooldown_raw)
        consecutive_failures = p_detail.get("consecutive_failures", 0)
        last_failure_reason = p_detail.get("last_failure_reason")

        now = datetime.now(timezone.utc)
        is_cooling_down = False
        remaining_seconds = 0
        if cooldown_dt and cooldown_dt > now:
            is_cooling_down = True
            remaining_seconds = max(0, int((cooldown_dt - now).total_seconds()))

        stats = {}
        if hasattr(self._repository, "get_scraper_stats"):
            stats = self._repository.get_scraper_stats()
        p_stats = stats.get(plugin_name, {})

        total_runs = p_stats.get("total_runs", 0)
        total_records = p_stats.get("total_records", 0)
        success_runs = p_stats.get("success_runs", 0)
        failed_runs = p_stats.get("failed_runs", 0)
        success_rate = (success_runs / total_runs * 100.0) if total_runs > 0 else 100.0

        tag = p_detail.get("tag") or meta.get("category", "General")
        category = tag

        return {
            "name": plugin_name,
            "display_name": meta.get("display_name", plugin_name),
            "category": category,
            "tag": tag,
            "default_category": meta.get("category", "General"),
            "description": description,
            "default_description": meta.get("description", ""),
            "requires_network": meta.get("requires_network", True),
            "enabled": enabled,
            "config": config,
            "cooldown_until": cooldown_dt.isoformat() if cooldown_dt else None,
            "is_cooling_down": is_cooling_down,
            "cooldown_remaining_seconds": remaining_seconds,
            "consecutive_failures": consecutive_failures,
            "last_failure_reason": last_failure_reason,
            "total_runs": total_runs,
            "total_records": total_records,
            "success_runs": success_runs,
            "failed_runs": failed_runs,
            "success_rate": round(success_rate, 1),
            "last_run_at": p_stats.get("last_run_at"),
            "updated_at": p_detail.get("updated_at"),
        }


class UpdateScraper:
    """Updates custom metadata, description, enabled state, tag, and network configuration for an AIS scraper plugin."""

    def __init__(
        self,
        registry: AISPluginRegistry,
        repository: AISRepository,
    ) -> None:
        self._registry = registry
        self._repository = repository

    def execute(
        self,
        plugin_name: str,
        enabled: bool | None = None,
        description: str | None = None,
        tag: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise ValueError("Scraper plugin name must be a non-empty string")
        plugin_name = plugin_name.strip()

        plugins = self._registry.get_plugins(plugin_name)
        if not plugins:
            raise PluginNotFoundError(f"Unknown AIS plugin: {plugin_name}")

        if config is not None and not isinstance(config, dict):
            raise ValueError("Config must be a dictionary")

        if hasattr(self._repository, "update_scraper"):
            self._repository.update_scraper(
                plugin_name=plugin_name,
                enabled=enabled,
                description=description,
                tag=tag,
                config=config,
            )
        else:
            if enabled is not None:
                self._repository.set_scraper_config(plugin_name, enabled)
            if config is not None:
                self._repository.update_scraper_settings(plugin_name, config)

        if config is not None:
            for p in plugins:
                if hasattr(p, "configure"):
                    try:
                        p.configure(config)
                    except Exception:
                        pass

        getter = GetScraperDetail(self._registry, self._repository)
        return getter.execute(plugin_name)


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

