"""Shared SQLite connection and transaction management."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SQLiteDatabase:
    """Open short-lived SQLite transactions with consistent configuration."""

    def __init__(self, database_path: Path | str, timeout: float = 5) -> None:
        if timeout <= 0:
            raise ValueError("SQLite timeout must be positive")
        path = Path(database_path)
        if path.name != ":memory:":
            path = path.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._timeout = timeout

    @contextmanager
    def connection(self, *, rows: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=self._timeout)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if rows:
            connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
