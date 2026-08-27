"""Adapter around the legacy dynamic AIS plugin loader."""

from datetime import datetime
from typing import Any

from ingestion.plugin_manager import PluginManager
from sentinel_analysis.domain.entities import AISRecord, BoundingBox, Vessel, VesselPosition


class LegacyAISPluginAdapter:
    """Normalize old plugin implementations to the clean AIS port."""

    def __init__(self, plugin_class: type, config: dict[str, Any] | None = None) -> None:
        self._plugin = plugin_class(config or {})
        self.name = plugin_class.__name__

    def authenticate(self) -> None:
        self._plugin.authenticate()

    def fetch(
        self,
        bbox: BoundingBox,
        time_range: tuple[datetime | None, datetime | None],
    ) -> list[AISRecord]:
        raw_data = self._plugin.fetch_data(bbox.as_list(), time_range)
        parsed = self._plugin.parse_data(raw_data)

        # Current plugins use either normalized dictionaries or (vessels,
        # locations) dataclass tuples. Keep that compatibility in this adapter
        # so the application layer sees exactly one format.
        if isinstance(parsed, tuple) and len(parsed) == 2:
            vessels, locations = parsed
            by_mmsi = {v.mmsi: v for v in vessels}
            return [
                AISRecord(
                    vessel=Vessel(
                        imo=by_mmsi[location.mmsi].imo,
                        mmsi=by_mmsi[location.mmsi].mmsi,
                        name=by_mmsi[location.mmsi].vessel_name,
                        vessel_type=by_mmsi[location.mmsi].vessel_type,
                        callsign=by_mmsi[location.mmsi].callsign,
                    ),
                    position=VesselPosition(
                        mmsi=location.mmsi,
                        latitude=location.latitude,
                        longitude=location.longitude,
                        timestamp=location.timestamp,
                        speed=location.speed,
                        heading=location.heading,
                    ),
                )
                for location in locations
                if location.mmsi in by_mmsi
            ]

        records: list[AISRecord] = []
        for item in parsed:
            vessel = item["vessel"]
            location = item["location"]
            timestamp = location["timestamp"]
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            records.append(
                AISRecord(
                    vessel=Vessel(
                        imo=str(vessel["imo"]),
                        mmsi=str(vessel["mmsi"]),
                        name=vessel.get("vessel_name"),
                        vessel_type=vessel.get("vessel_type"),
                        callsign=vessel.get("callsign"),
                    ),
                    position=VesselPosition(
                        mmsi=str(vessel["mmsi"]),
                        latitude=float(location["latitude"]),
                        longitude=float(location["longitude"]),
                        timestamp=timestamp,
                        speed=location.get("speed"),
                        heading=location.get("heading"),
                    ),
                )
            )
        return records


class DynamicAISPluginRegistry:
    def __init__(self, plugin_manager: PluginManager | None = None) -> None:
        self._manager = plugin_manager or PluginManager()

    def get_plugins(self, name: str | None = None) -> list[LegacyAISPluginAdapter]:
        plugins = [LegacyAISPluginAdapter(plugin_class) for plugin_class in self._manager.get_plugins()]
        if name is not None:
            plugins = [plugin for plugin in plugins if plugin.name == name]
        return plugins
