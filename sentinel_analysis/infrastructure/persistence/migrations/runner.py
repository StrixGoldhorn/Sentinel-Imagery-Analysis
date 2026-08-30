"""Lightweight SQLite schema migration engine."""

import sqlite3
from pathlib import Path


class MigrationRunner:
    """Discovers and executes numbered SQL migration files on an SQLite database."""

    def __init__(self, database_path: Path | str, migrations_dir: Path | str | None = None) -> None:
        self._database_path = Path(database_path).resolve()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        if migrations_dir is None:
            self._migrations_dir = Path(__file__).resolve().parent / "sql"
        else:
            self._migrations_dir = Path(migrations_dir).resolve()
        self._migrations_dir.mkdir(parents=True, exist_ok=True)

    def run_migrations(self) -> list[str]:
        """Apply all pending migrations in alphabetical order within a transaction."""
        applied: list[str] = []
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE,
                    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            already_applied = {
                row[0]
                for row in connection.execute("SELECT version FROM _schema_migrations").fetchall()
            }

            migration_files = sorted(self._migrations_dir.glob("*.sql"))
            for file_path in migration_files:
                version = file_path.name
                if version not in already_applied:
                    sql = file_path.read_text(encoding="utf-8")
                    connection.executescript(sql)
                    connection.execute(
                        "INSERT INTO _schema_migrations (version) VALUES (?)",
                        (version,),
                    )
                    applied.append(version)
        return applied
