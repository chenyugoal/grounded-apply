from __future__ import annotations

import hashlib
import sqlite3
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from grounded_apply.repositories._schema import (
    LATEST_SCHEMA_VERSION,
    FutureSchemaError,
    SchemaError,
    default_migrations_directory,
    initialize_schema,
    read_schema_version,
)
from grounded_apply.repositories import inspect_schema
from grounded_apply.config import UnsafeRuntimePathError


class SchemaMigrationTests(unittest.TestCase):
    def connect(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def test_fresh_schema_initialization_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = self.connect(Path(directory) / "profile.db")
            self.addCleanup(connection.close)

            first = initialize_schema(connection, default_migrations_directory())
            second = initialize_schema(connection, default_migrations_directory())

            self.assertEqual(first, LATEST_SCHEMA_VERSION)
            self.assertEqual(second, LATEST_SCHEMA_VERSION)
            ledger_count = connection.execute(
                "SELECT count(*) FROM schema_migrations"
            ).fetchone()[0]
            self.assertEqual(ledger_count, LATEST_SCHEMA_VERSION)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_future_schema_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = self.connect(Path(directory) / "future.db")
            self.addCleanup(connection.close)
            future_version = LATEST_SCHEMA_VERSION + 1
            connection.execute(f"PRAGMA user_version = {future_version}")

            with self.assertRaises(FutureSchemaError):
                initialize_schema(connection, default_migrations_directory())

            self.assertEqual(read_schema_version(connection), future_version)
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name = 'schema_migrations'"
                ).fetchone()
            )

    def test_unversioned_user_tables_are_not_silently_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = self.connect(Path(directory) / "unknown.db")
            self.addCleanup(connection.close)
            connection.execute("CREATE TABLE unknown_user_data (value TEXT)")
            connection.commit()

            with self.assertRaisesRegex(SchemaError, "unversioned schema"):
                initialize_schema(connection, default_migrations_directory())

            self.assertEqual(read_schema_version(connection), 0)

    def test_read_only_inspection_validates_without_changing_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "profile.db"
            connection = self.connect(database)
            initialize_schema(connection, default_migrations_directory())
            connection.close()
            database.chmod(0o640)

            before = database.read_bytes()
            with self.assertRaises(UnsafeRuntimePathError):
                inspect_schema(database)
            self.assertEqual(database.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o640)
            database.chmod(0o600)
            self.assertEqual(inspect_schema(database), LATEST_SCHEMA_VERSION)

    def test_concurrent_initialization_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "concurrent.db"
            worker_count = 8
            barrier = threading.Barrier(worker_count)

            def initialize() -> int:
                repository = None
                try:
                    from grounded_apply.repositories import SQLiteRepository

                    repository = SQLiteRepository(database, timeout=10)
                    barrier.wait(timeout=10)
                    repository.initialize()
                    return repository.schema_version
                finally:
                    if repository is not None:
                        repository.close()

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                versions = list(executor.map(lambda _: initialize(), range(worker_count)))

            self.assertEqual(versions, [LATEST_SCHEMA_VERSION] * worker_count)
            self.assertEqual(inspect_schema(database), LATEST_SCHEMA_VERSION)

    def test_version_one_database_upgrades_without_rewriting_the_initial_migration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "version-one.db"
            connection = self.connect(database)
            self.addCleanup(connection.close)
            migrations = default_migrations_directory()
            first_path = migrations / "001_initial.sql"
            first_sql = first_path.read_text(encoding="utf-8")
            connection.executescript(first_sql)
            connection.execute(
                """
                INSERT INTO schema_migrations (
                    version,
                    name,
                    checksum_sha256,
                    applied_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    1,
                    first_path.name,
                    hashlib.sha256(first_sql.encode("utf-8")).hexdigest(),
                    "2026-08-11T12:00:00Z",
                ),
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_schema "
                    "WHERE type = 'table' AND name = 'profile_import_review_items'"
                ).fetchone()
            )

            version = initialize_schema(connection, migrations)

            self.assertEqual(version, LATEST_SCHEMA_VERSION)
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_schema "
                    "WHERE type = 'table' AND name = 'profile_import_review_items'"
                ).fetchone()
            )
            applied = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual(
                applied,
                [(1, "001_initial.sql"), (2, "002_profile_import_review_items.sql"),
                 (3, "003_claim_retirements.sql"), (4, "004_application_pilot.sql")],
            )


if __name__ == "__main__":
    unittest.main()
