"""Port contract for generating maritime intelligence briefs."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from sentinel_analysis.domain.entities import Scan


@runtime_checkable
class IntelligenceBriefGenerator(Protocol):
    """Generates decision-ready intelligence briefings for maritime scans."""

    def generate_brief(self, scan: Scan, output_path: Path) -> Path:
        """Create an intelligence briefing file (e.g. PDF) for the specified scan."""
        ...
