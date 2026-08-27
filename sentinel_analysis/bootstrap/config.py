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
        try:
            port = int(os.getenv("PORT", "5050"))
        except ValueError as exc:
            raise ValueError("PORT must be an integer") from exc
        return cls(
            project_root=root,
            database_path=Path(os.getenv("DATABASE_PATH", str(root / "data.db"))).resolve(),
            output_root=Path(os.getenv("OUTPUT_ROOT", str(root / "static" / "output"))).resolve(),
            copernicus_username=os.getenv("COP_USERNAME"),
            copernicus_password=os.getenv("COP_PASSWORD"),
            n2yo_api_key=os.getenv("N2YO_API_KEY"),
            debug=os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"},
            port=port,
        )
