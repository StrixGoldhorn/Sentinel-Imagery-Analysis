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


PLUGIN_METADATA: dict[str, dict[str, object]] = {
    "VesselFinderPlugin": {
        "display_name": "VesselFinder (Playwright Stealth)",
        "category": "Live Web Scraper",
        "description": "Stealth browser scraping of VesselFinder map tiles passing Cloudflare/WAF challenges.",
        "requires_network": True,
        "default_enabled": True,
    },
    "AISFriendsPlugin": {
        "display_name": "AIS Friends Community Feed",
        "category": "Community API",
        "description": "Pulls real-time marine vessel positions from the AIS Friends community network.",
        "requires_network": True,
        "default_enabled": True,
    },
    "AprsFiPlugin": {
        "display_name": "Aprs.fi (APRS-IS Gateway)",
        "category": "Radio Gateway",
        "description": "Ingests marine AIS positions reported through APRS-IS radio internet gateways.",
        "requires_network": True,
        "default_enabled": True,
    },
    "UDPListenerPlugin": {
        "display_name": "Local UDP NMEA Receiver",
        "category": "Hardware / NMEA",
        "description": "Listens on local UDP port for live NMEA-0183 AIVDM/AIVDO AIS receiver sentences.",
        "requires_network": False,
        "default_enabled": False,
    },
    "MockAISPlugin": {
        "display_name": "Mock AIS Simulator",
        "category": "Simulator",
        "description": "Deterministic synthetic vessel trajectories for repeatable offline testing.",
        "requires_network": False,
        "default_enabled": True,
    },
    "MockPublicAISPlugin": {
        "display_name": "Public Mock AIS",
        "category": "Simulator",
        "description": "Simulates public open-source AIS feeds with high-density vessel distribution.",
        "requires_network": False,
        "default_enabled": True,
    },
}


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

    def get_plugin_metadata(self, name: str) -> dict[str, object]:
        return PLUGIN_METADATA.get(
            name,
            {
                "display_name": name,
                "category": "Custom Plugin",
                "description": "Custom AIS scraper plugin adapter.",
                "requires_network": True,
                "default_enabled": True,
            },
        )

    def list_all_metadata(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for plugin in self._plugins:
            meta = dict(self.get_plugin_metadata(plugin.name))
            meta["name"] = plugin.name
            result.append(meta)
        return result

