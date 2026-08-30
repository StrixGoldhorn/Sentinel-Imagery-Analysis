"""Filesystem implementation of the TileCache port."""

import hashlib
from pathlib import Path
from typing import Optional


class FilesystemTileCache:
    """Stores downloaded tiles on disk keyed by SHA256 hashes of tile metadata."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir).resolve()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, key: str) -> Path:
        sanitized = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{sanitized}.png"

    def get(self, key: str) -> Optional[bytes]:
        path = self._key_path(key)
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError:
                return None
        return None

    def set(self, key: str, data: bytes) -> None:
        path = self._key_path(key)
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_bytes(data)
            tmp_path.replace(path)
        except OSError:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def has(self, key: str) -> bool:
        return self._key_path(key).is_file()
