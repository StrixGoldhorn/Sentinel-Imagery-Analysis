from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SettingsRepository(Protocol):
    """Persist and retrieve system-wide configuration by feature section."""

    def get(self, key: str, default: Any = None) -> Any:
        ...

    def get_section(self, section: str) -> dict[str, Any]:
        ...

    def get_all(self) -> dict[str, dict[str, Any]]:
        ...

    def set(self, section: str, key: str, value: Any, description: str | None = None) -> None:
        ...

    def update_bulk(self, settings: dict[str, dict[str, Any]]) -> None:
        ...

    def reset_section(self, section: str | None = None) -> None:
        ...
