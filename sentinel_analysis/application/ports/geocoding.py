"""Application-owned reverse-geocoding contract."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class LocationResolver(Protocol):
    """Resolve geographic coordinates into a displayable location."""

    def resolve(self, latitude: float, longitude: float) -> str:
        ...
