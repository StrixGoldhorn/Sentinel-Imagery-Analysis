"""Tests for environment-backed application settings."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_analysis.bootstrap.config import Settings


def test_relative_paths_are_resolved_from_project_root() -> None:
    root = (Path.cwd() / "settings-test-root").resolve()
    environment = {
        "DATABASE_PATH": "runtime/app.db",
        "OUTPUT_ROOT": "runtime/output",
        "CACHE_ROOT": "runtime/cache",
        "FLASK_DEBUG": " yes ",
        "PORT": "5051",
    }
    with patch.dict(os.environ, environment, clear=False):
        settings = Settings.from_environment(root)

    assert settings.database_path == (root / "runtime/app.db").resolve()
    assert settings.output_root == (root / "runtime/output").resolve()
    assert settings.cache_root == (root / "runtime/cache").resolve()
    assert settings.debug is True
    assert settings.port == 5051


def test_absolute_paths_are_preserved() -> None:
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

    assert settings.database_path == database_path
    assert settings.output_root == output_root
    assert settings.debug is False


def test_invalid_debug_value_is_rejected() -> None:
    root = (Path.cwd() / "settings-test-root").resolve()
    with patch.dict(os.environ, {"FLASK_DEBUG": "sometimes"}, clear=False):
        try:
            Settings.from_environment(root)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "FLASK_DEBUG" in str(e)


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

