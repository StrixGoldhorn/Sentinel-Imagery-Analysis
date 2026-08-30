"""Registry for configured AIS plugin adapters."""

from collections.abc import Sequence

from sentinel_analysis.application.ports.ais import AISPlugin
from sentinel_analysis.infrastructure.ais.plugins import (
    AISFriendsPlugin,
    AprsFiPlugin,
    MockAISPlugin,
    MockPublicAISPlugin,
    UDPListenerPlugin,
    VesselFinderPlugin,
)


class DynamicAISPluginRegistry:
    """Select plugins by stable name.

    Additional real provider adapters can be injected by the composition root
    without changing the application use case.
    """

    def __init__(self, plugins: Sequence[AISPlugin] | None = None) -> None:
        self._plugins = (
            list(plugins)
            if plugins is not None
            else [
                MockAISPlugin(),
                MockPublicAISPlugin(),
                AISFriendsPlugin(),
                VesselFinderPlugin(),
                AprsFiPlugin(),
                UDPListenerPlugin(),
            ]
        )
        names = [plugin.name for plugin in self._plugins]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("AIS plugin names must be non-empty strings")
        if len(names) != len(set(names)):
            raise ValueError("AIS plugin names must be unique")

    def get_plugins(self, name: str | None = None) -> list[AISPlugin]:
        if name is None:
            return list(self._plugins)
        return [plugin for plugin in self._plugins if plugin.name == name]
