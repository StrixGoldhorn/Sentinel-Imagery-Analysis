"""Persistence adapters."""

from sentinel_analysis.infrastructure.persistence.filesystem_scans import FilesystemScanRepository
from sentinel_analysis.infrastructure.persistence.sqlite_ais import SQLiteAISRepository
from sentinel_analysis.infrastructure.persistence.sqlite_aois import SQLiteAreaOfInterestRepository

__all__ = ["FilesystemScanRepository", "SQLiteAISRepository", "SQLiteAreaOfInterestRepository"]
