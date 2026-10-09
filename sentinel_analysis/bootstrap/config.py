"""Typed runtime configuration."""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional outside the packaged app
    load_dotenv = None


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_path: Path
    output_root: Path
    copernicus_username: str | None
    copernicus_password: str | None
    n2yo_api_key: str | None
    debug: bool = False
    port: int = 5050
    cache_root: Path | None = None
    api_key: str | None = None
    rate_limit_per_minute: int = 120
    rate_limiting_enabled: bool = True
    storage_quota_bytes: int = 10 * 1024 * 1024 * 1024
    scan_retention_days: int = 30
    cache_retention_days: int = 7
    trusted_proxies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve())
        object.__setattr__(self, "database_path", Path(self.database_path).resolve())
        object.__setattr__(self, "output_root", Path(self.output_root).resolve())
        cache = self.cache_root if self.cache_root is not None else self.output_root / ".cache"
        object.__setattr__(self, "cache_root", Path(cache).resolve())
        if self.api_key is not None:
            clean_key = str(self.api_key).strip()
            object.__setattr__(self, "api_key", clean_key if clean_key else None)
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("Application port must be between 1 and 65535")
        if isinstance(self.rate_limit_per_minute, bool) or not isinstance(self.rate_limit_per_minute, int) or self.rate_limit_per_minute <= 0:
            raise ValueError("rate_limit_per_minute must be a positive integer")
        if isinstance(self.storage_quota_bytes, bool) or not isinstance(self.storage_quota_bytes, int) or self.storage_quota_bytes <= 0:
            raise ValueError("storage_quota_bytes must be a positive integer")
        if isinstance(self.scan_retention_days, bool) or not isinstance(self.scan_retention_days, int) or self.scan_retention_days <= 0:
            raise ValueError("scan_retention_days must be a positive integer")
        if isinstance(self.cache_retention_days, bool) or not isinstance(self.cache_retention_days, int) or self.cache_retention_days <= 0:
            raise ValueError("cache_retention_days must be a positive integer")
        if isinstance(self.trusted_proxies, (list, tuple, set)):
            clean_proxies = tuple(str(p).strip() for p in self.trusted_proxies if str(p).strip())
            object.__setattr__(self, "trusted_proxies", clean_proxies)
        elif isinstance(self.trusted_proxies, str):
            clean_proxies = tuple(p.strip() for p in self.trusted_proxies.split(",") if p.strip())
            object.__setattr__(self, "trusted_proxies", clean_proxies)
        else:
            object.__setattr__(self, "trusted_proxies", ())

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> "Settings":
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        if load_dotenv is not None:
            load_dotenv(root / ".env")

        def first_env(*names: str, default: str | None = None) -> str | None:
            for name in names:
                val = os.getenv(name)
                if val is not None and val != "":
                    return val
            return default

        def environment_path(*names: str, default: Path) -> Path:
            configured = None
            for name in names:
                val = os.getenv(name)
                if val:
                    configured = val
                    break
            path = Path(configured) if configured else default
            return path if path.is_absolute() else root / path

        try:
            port = int(first_env("SENTINEL_PORT", "PORT", default="5050") or "5050")
        except ValueError as exc:
            raise ValueError("PORT must be an integer") from exc
        debug_value = (first_env("SENTINEL_DEBUG", "FLASK_DEBUG", default="false") or "false").strip().lower()
        if debug_value not in {"0", "1", "false", "true", "no", "yes"}:
            raise ValueError("FLASK_DEBUG must be one of: 0, 1, false, true, no, yes")

        rate_limit_raw = first_env("SENTINEL_RATE_LIMIT", "RATE_LIMIT_PER_MINUTE", default="120") or "120"
        try:
            rate_limit_per_minute = max(1, int(rate_limit_raw))
        except ValueError:
            rate_limit_per_minute = 120

        rate_limit_enabled_raw = (first_env("SENTINEL_RATE_LIMITING_ENABLED", "RATE_LIMITING_ENABLED", default="true") or "true").strip().lower()
        rate_limiting_enabled = rate_limit_enabled_raw not in {"0", "false", "no", "off"}

        storage_quota_raw = first_env("SENTINEL_STORAGE_QUOTA_BYTES", "STORAGE_QUOTA_BYTES", default=str(10 * 1024 * 1024 * 1024)) or str(10 * 1024 * 1024 * 1024)
        try:
            storage_quota_bytes = max(1024 * 1024, int(storage_quota_raw))
        except ValueError:
            storage_quota_bytes = 10 * 1024 * 1024 * 1024

        scan_retention_raw = first_env("SENTINEL_SCAN_RETENTION_DAYS", "SCAN_RETENTION_DAYS", default="30") or "30"
        try:
            scan_retention_days = max(1, int(scan_retention_raw))
        except ValueError:
            scan_retention_days = 30

        cache_retention_raw = first_env("SENTINEL_CACHE_RETENTION_DAYS", "CACHE_RETENTION_DAYS", default="7") or "7"
        try:
            cache_retention_days = max(1, int(cache_retention_raw))
        except ValueError:
            cache_retention_days = 7

        trusted_proxies_raw = first_env("SENTINEL_TRUSTED_PROXIES", "TRUSTED_PROXIES", default="") or ""
        trusted_proxies = tuple(p.strip() for p in trusted_proxies_raw.split(",") if p.strip())

        return cls(
            project_root=root,
            database_path=environment_path("SENTINEL_DATABASE_PATH", "DATABASE_PATH", default=root / "data.db"),
            output_root=environment_path("SENTINEL_OUTPUT_ROOT", "OUTPUT_ROOT", default=root / "static" / "output"),
            copernicus_username=first_env("SENTINEL_COPERNICUS_USERNAME", "COPERNICUS_USERNAME", "COP_USERNAME"),
            copernicus_password=first_env("SENTINEL_COPERNICUS_PASSWORD", "COPERNICUS_PASSWORD", "COP_PASSWORD"),
            n2yo_api_key=first_env("SENTINEL_N2YO_API_KEY", "N2YO_API_KEY"),
            debug=debug_value in {"1", "true", "yes"},
            port=port,
            cache_root=environment_path("SENTINEL_CACHE_ROOT", "CACHE_ROOT", default=root / ".cache"),
            api_key=first_env("SENTINEL_API_KEY", "API_KEY"),
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limiting_enabled=rate_limiting_enabled,
            storage_quota_bytes=storage_quota_bytes,
            scan_retention_days=scan_retention_days,
            cache_retention_days=cache_retention_days,
            trusted_proxies=trusted_proxies,
        )
