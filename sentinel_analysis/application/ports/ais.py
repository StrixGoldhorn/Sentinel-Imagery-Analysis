"""Application-owned contracts for AIS providers and plugin selection."""

from datetime import datetime
from typing import Iterable, Protocol, Sequence, runtime_checkable

from sentinel_analysis.domain.entities import AISRecord, BoundingBox


AISTimeRange = tuple[datetime | None, datetime | None]


@runtime_checkable
class AISPlugin(Protocol):
    """Authenticate with and fetch normalized records from one AIS source."""

    name: str

    def authenticate(self) -> None:
        ...

    def fetch(self, bbox: BoundingBox, time_range: AISTimeRange) -> Iterable[AISRecord]:
        ...


@runtime_checkable
class AISPluginRegistry(Protocol):
    """Select configured AIS provider plugins by stable name."""

    def get_plugins(self, name: str | None = None) -> Sequence[AISPlugin]:
        ...
