"""Built-in AIS plugins."""

from sentinel_analysis.infrastructure.ais.plugins.mock import MockAISPlugin
from sentinel_analysis.infrastructure.ais.plugins.public_mock import MockPublicAISPlugin

__all__ = ["MockAISPlugin", "MockPublicAISPlugin"]

