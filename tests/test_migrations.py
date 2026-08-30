import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from sentinel_analysis.infrastructure.persistence.migrations.runner import MigrationRunner


def test_migrations_applied_when_new_versions_found() -> None:
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchall.return_value = []

    with patch("sqlite3.connect", return_value=mock_conn):
        runner = MigrationRunner("in_memory_virtual.db")
        applied = runner.run_migrations()

        assert len(applied) >= 3
        assert any("001" in v for v in applied)
        assert any("002" in v for v in applied)
        assert any("003" in v for v in applied)
        assert mock_conn.executescript.call_count >= 3


def test_migrations_applied_idempotently_when_already_applied() -> None:
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    sql_dir = Path(__file__).resolve().parent.parent / "sentinel_analysis" / "infrastructure" / "persistence" / "migrations" / "sql"
    all_files = [f.name for f in sql_dir.glob("*.sql")]
    mock_conn.execute.return_value.fetchall.return_value = [(f,) for f in all_files]

    with patch("sqlite3.connect", return_value=mock_conn):
        runner = MigrationRunner("in_memory_virtual.db")
        applied = runner.run_migrations()

        assert len(applied) == 0
        mock_conn.executescript.assert_not_called()


def load_tests(loader, standard_tests, pattern):
    import inspect
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


if __name__ == "__main__":
    unittest.main()

