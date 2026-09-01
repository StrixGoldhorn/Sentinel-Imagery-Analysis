"""Authenticate, fetch, normalize, and persist AIS data by provider."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sentinel_analysis.application.exceptions import PluginNotFoundError
from sentinel_analysis.application.ports.ais import AISPluginRegistry, AISTimeRange
from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.application.results import IngestionLog, IngestionResult, IngestionStatus
from sentinel_analysis.domain.entities import BoundingBox


def _parse_cooldown(cooldown_val: Any) -> datetime | None:
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


def is_rate_limit_or_bot_block(error_str: str) -> bool:
    err = str(error_str).lower()
    keywords = [
        "429", "too many requests", "rate limit",
        "403", "forbidden", "cloudflare", "waf", "access denied",
        "captcha", "turnstile", "challenge", "bot detected",
        "timed out", "timeout", "connection refused", "proxyerror",
    ]
    return any(kw in err for kw in keywords)


def calculate_cooldown(consecutive_failures: int) -> timedelta:
    if consecutive_failures <= 1:
        return timedelta(minutes=15)
    elif consecutive_failures == 2:
        return timedelta(hours=1)
    else:
        return timedelta(hours=4)


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

        scraper_details: dict[str, dict] = {}
        if hasattr(self._repository, "get_all_scraper_details"):
            try:
                scraper_details = self._repository.get_all_scraper_details()
            except Exception:
                scraper_details = {}
        elif hasattr(self._repository, "get_all_scraper_configs"):
            try:
                simple_configs = self._repository.get_all_scraper_configs()
                for name, enabled in simple_configs.items():
                    scraper_details[name] = {"enabled": enabled}
            except Exception:
                scraper_details = {}

        now = datetime.now(timezone.utc)

        for plugin in plugins:
            detail = None
            if hasattr(self._repository, "get_scraper_config"):
                try:
                    detail = self._repository.get_scraper_config(plugin.name)
                except Exception:
                    detail = None
            if detail is None:
                detail = scraper_details.get(plugin.name)

            # Dynamic configuration injection
            if detail and detail.get("config") and hasattr(plugin, "configure"):
                try:
                    plugin.configure(detail["config"])
                except Exception:
                    pass

            # If multi-provider automated ingestion and plugin is disabled, skip
            if plugin_name is None and detail and detail.get("enabled") is False:
                continue

            # Check cooldown status for automated runs (allow manual single-target test to bypass)
            if plugin_name is None and detail:
                cooldown_until = _parse_cooldown(detail.get("cooldown_until"))
                if cooldown_until and cooldown_until > now:
                    skip_msg = f"Skipped: cooling down until {cooldown_until.isoformat()}"
                    self._repository.log_execution(plugin.name, "COOLDOWN_SKIPPED", 0, skip_msg)
                    results.append({
                        "plugin": plugin.name,
                        "status": "COOLDOWN_SKIPPED",
                        "records": 0,
                        "error": skip_msg,
                    })
                    continue

            inserted = 0
            try:
                plugin.authenticate()
                records = list(plugin.fetch(bbox, normalized_time_range))
                inserted = self._repository.save_records(records, plugin.name)
                status: IngestionStatus = "SUCCESS"
                error = None
                if hasattr(self._repository, "record_scraper_success"):
                    self._repository.record_scraper_success(plugin.name)
            except Exception as exc:
                status = "FAILED"
                error = str(exc)

                # Evaluate rate-limit or bot protection triggers
                consecutive = int(detail.get("consecutive_failures", 0) if detail else 0) + 1
                if is_rate_limit_or_bot_block(error):
                    backoff = calculate_cooldown(consecutive)
                    cool_until = now + backoff
                    if hasattr(self._repository, "record_scraper_failure"):
                        self._repository.record_scraper_failure(plugin.name, error, cool_until, consecutive)
                else:
                    if hasattr(self._repository, "record_scraper_failure"):
                        self._repository.record_scraper_failure(plugin.name, error, None, consecutive)

            self._repository.log_execution(plugin.name, status, inserted, error)
            results.append({"plugin": plugin.name, "status": status, "records": inserted, "error": error})
            total_inserted += inserted

        return IngestionResult(total_inserted=total_inserted, logs=results)

