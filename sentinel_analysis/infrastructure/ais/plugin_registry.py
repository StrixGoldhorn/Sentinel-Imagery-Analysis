"""Registry for configured AIS plugin adapters."""

from sentinel_analysis.application.ports.providers import AISPlugin
from sentinel_analysis.infrastructure.ais.plugins import MockAISPlugin, MockPublicAISPlugin


class DynamicAISPluginRegistry:
    """Select plugins by stable name.

    Additional real provider adapters can be injected by the composition root
    without changing the application use case.
    """

    def __init__(self, plugins: list[AISPlugin] | None = None) -> None:
        self._plugins = plugins or [MockAISPlugin(), MockPublicAISPlugin()]
        names = [plugin.name for plugin in self._plugins]
        if len(names) != len(set(names)):
            raise ValueError("AIS plugin names must be unique")

    def get_plugins(self, name: str | None = None) -> list[AISPlugin]:
        if name is None:
            return list(self._plugins)
        return [plugin for plugin in self._plugins if plugin.name == name]

