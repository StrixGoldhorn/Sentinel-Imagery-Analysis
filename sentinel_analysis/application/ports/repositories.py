"""Compatibility imports for the responsibility-specific repository ports.

New application code should import from the focused port module. This facade
keeps existing use cases stable until their dedicated refactoring step.
"""

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.application.ports.aoi_repository import AreaOfInterestRepository
from sentinel_analysis.application.ports.scan_repository import ScanRepository

__all__ = ["AISRepository", "AreaOfInterestRepository", "ScanRepository"]
