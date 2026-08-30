"""Unit tests for the FilesystemTileCache."""

import shutil
import unittest
from pathlib import Path

from sentinel_analysis.infrastructure.imagery.cache import FilesystemTileCache


class TileCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache_dir = Path(__file__).resolve().parent / "runtime" / "cache_test"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache = FilesystemTileCache(self.cache_dir)
        self.key = "sentinel1_2026-08-27_103.0_1.0_104.0_2.0_256_256"

    def tearDown(self) -> None:
        shutil.rmtree(self.cache_dir, ignore_errors=True)

    def test_cache_miss_store_and_hit(self) -> None:
        # Cache miss
        self.assertIsNone(self.cache.get(self.key))
        self.assertFalse(self.cache.has(self.key))

        # Store data
        dummy_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        self.cache.set(self.key, dummy_data)

        # Cache hit
        self.assertTrue(self.cache.has(self.key))
        hit = self.cache.get(self.key)
        self.assertIsNotNone(hit)
        self.assertEqual(hit, dummy_data)


if __name__ == "__main__":
    unittest.main()
