"""Application-owned contract for tile caching."""

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class TileCache(Protocol):
    """Protocol for persisting and retrieving raw image tiles."""

    def get(self, key: str) -> Optional[bytes]:
        """Retrieve cached tile bytes by cache key, or None if not cached."""
        ...

    def set(self, key: str, data: bytes) -> None:
        """Store tile bytes under cache key."""
        ...

    def has(self, key: str) -> bool:
        """Check if a tile key is in the cache."""
        ...
