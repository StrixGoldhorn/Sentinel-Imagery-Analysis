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

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve())
        object.__setattr__(self, "database_path", Path(self.database_path).resolve())
        object.__setattr__(self, "output_root", Path(self.output_root).resolve())
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("Application port must be between 1 and 65535")

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
        return cls(
            project_root=root,
            database_path=environment_path("DATABASE_PATH", root / "data.db"),
            output_root=environment_path("OUTPUT_ROOT", root / "static" / "output"),
            copernicus_username=os.getenv("COP_USERNAME"),
            copernicus_password=os.getenv("COP_PASSWORD"),
            n2yo_api_key=os.getenv("N2YO_API_KEY"),
            debug=debug_value in {"1", "true", "yes"},
            port=port,
        )
