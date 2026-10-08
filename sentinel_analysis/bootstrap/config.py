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

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> "Settings":
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        if load_dotenv is not None:
            load_dotenv(root / ".env")

        def environment_path(name: str, default: Path) -> Path:
            configured = os.getenv(name)
            path = Path(configured) if configured else default
            return path if path.is_absolute() else root / path

        try:
            port = int(os.getenv("PORT", "5050"))
        except ValueError as exc:
            raise ValueError("PORT must be an integer") from exc
        debug_value = os.getenv("FLASK_DEBUG", "false").strip().lower()
        if debug_value not in {"0", "1", "false", "true", "no", "yes"}:
            raise ValueError("FLASK_DEBUG must be one of: 0, 1, false, true, no, yes")

        rate_limit_raw = os.getenv("SENTINEL_RATE_LIMIT", "120")
        try:
            rate_limit_per_minute = max(1, int(rate_limit_raw))
        except ValueError:
            rate_limit_per_minute = 120

        rate_limit_enabled_raw = os.getenv("SENTINEL_RATE_LIMITING_ENABLED", "true").strip().lower()
        rate_limiting_enabled = rate_limit_enabled_raw not in {"0", "false", "no", "off"}

        storage_quota_raw = os.getenv("SENTINEL_STORAGE_QUOTA_BYTES", str(10 * 1024 * 1024 * 1024))
        try:
            storage_quota_bytes = max(1024 * 1024, int(storage_quota_raw))
        except ValueError:
            storage_quota_bytes = 10 * 1024 * 1024 * 1024

        scan_retention_raw = os.getenv("SENTINEL_SCAN_RETENTION_DAYS", "30")
        try:
            scan_retention_days = max(1, int(scan_retention_raw))
        except ValueError:
            scan_retention_days = 30

        cache_retention_raw = os.getenv("SENTINEL_CACHE_RETENTION_DAYS", "7")
        try:
            cache_retention_days = max(1, int(cache_retention_raw))
        except ValueError:
            cache_retention_days = 7

        return cls(
            project_root=root,
            database_path=environment_path("DATABASE_PATH", root / "data.db"),
            output_root=environment_path("OUTPUT_ROOT", root / "static" / "output"),
            copernicus_username=os.getenv("COP_USERNAME"),
            copernicus_password=os.getenv("COP_PASSWORD"),
            n2yo_api_key=os.getenv("N2YO_API_KEY"),
            debug=debug_value in {"1", "true", "yes"},
            port=port,
            cache_root=environment_path("CACHE_ROOT", root / ".cache"),
            api_key=os.getenv("SENTINEL_API_KEY"),
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limiting_enabled=rate_limiting_enabled,
            storage_quota_bytes=storage_quota_bytes,
            scan_retention_days=scan_retention_days,
            cache_retention_days=cache_retention_days,
        )
