"""Built-in AIS plugins."""

from sentinel_analysis.infrastructure.ais.plugins.ais_friends import AISFriendsPlugin
from sentinel_analysis.infrastructure.ais.plugins.aprs_fi import AprsFiPlugin
from sentinel_analysis.infrastructure.ais.plugins.mock import MockAISPlugin
from sentinel_analysis.infrastructure.ais.plugins.public_mock import MockPublicAISPlugin
from sentinel_analysis.infrastructure.ais.plugins.udp_listener import UDPListenerPlugin
from sentinel_analysis.infrastructure.ais.plugins.vessel_finder import VesselFinderPlugin

__all__ = [
    "AISFriendsPlugin",
    "AprsFiPlugin",
    "MockAISPlugin",
    "MockPublicAISPlugin",
    "UDPListenerPlugin",
    "VesselFinderPlugin",
]

