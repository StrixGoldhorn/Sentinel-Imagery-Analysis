"""Unit tests for the FilesystemTileCache."""

import shutil
import unittest
from pathlib import Path

from sentinel_analysis.infrastructure.imagery.cache import FilesystemTileCache


def test_cache_miss_store_and_hit() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        cache_dir = Path(temp_dir) / "cache_test"
        cache = FilesystemTileCache(cache_dir)
        key = "sentinel1_2026-08-27_103.0_1.0_104.0_2.0_256_256"

        # Cache miss
        assert cache.get(key) is None
        assert cache.has(key) is False

        # Store data
        dummy_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        cache.set(key, dummy_data)

        # Cache hit
        assert cache.has(key) is True
        hit = cache.get(key)
        assert hit is not None
        assert hit == dummy_data


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

