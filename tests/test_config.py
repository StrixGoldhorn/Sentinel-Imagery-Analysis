"""Tests for environment-backed application settings."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_analysis.bootstrap.config import Settings


class SettingsTests(unittest.TestCase):
    def test_relative_paths_are_resolved_from_project_root(self) -> None:
        root = (Path.cwd() / "settings-test-root").resolve()
        environment = {
            "DATABASE_PATH": "runtime/app.db",
            "OUTPUT_ROOT": "runtime/output",
            "FLASK_DEBUG": " yes ",
            "PORT": "5051",
        }
        with patch.dict(os.environ, environment, clear=False):
            settings = Settings.from_environment(root)

        self.assertEqual(settings.database_path, (root / "runtime/app.db").resolve())
        self.assertEqual(settings.output_root, (root / "runtime/output").resolve())
        self.assertEqual(settings.cache_root, (root / "runtime/cache").resolve())
        self.assertTrue(settings.debug)
        self.assertEqual(settings.port, 5051)


    def test_absolute_paths_are_preserved(self) -> None:
        root = (Path.cwd() / "settings-test-root").resolve()
        database_path = (Path.cwd() / "absolute-database.db").resolve()
        output_root = (Path.cwd() / "absolute-output").resolve()
        with patch.dict(
            os.environ,
            {
                "DATABASE_PATH": str(database_path),
                "OUTPUT_ROOT": str(output_root),
                "FLASK_DEBUG": "false",
                "PORT": "5050",
            },
            clear=False,
        ):
            settings = Settings.from_environment(root)

        self.assertEqual(settings.database_path, database_path)
        self.assertEqual(settings.output_root, output_root)
        self.assertFalse(settings.debug)

    def test_invalid_debug_value_is_rejected(self) -> None:
        root = (Path.cwd() / "settings-test-root").resolve()
        with patch.dict(os.environ, {"FLASK_DEBUG": "sometimes"}, clear=False):
            with self.assertRaisesRegex(ValueError, "FLASK_DEBUG"):
                Settings.from_environment(root)


if __name__ == "__main__":
    unittest.main()
