from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories._schema import (
    LATEST_SCHEMA_VERSION, MigrationError, default_migrations_directory, initialize_schema,
)
from grounded_apply.repositories.backup_files import LocalBackupStorage
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.backup import BackupError


def historical_snapshot(version: int = 4) -> bytes:
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as database:
        initialize_schema(database, default_migrations_directory(), target_version=version)
        return database.serialize()


class BackupSchemaCompatibilityTests(unittest.TestCase):
    def test_exact_v6_snapshot_remains_restorable_after_daily_migration(self) -> None:
        image = historical_snapshot(6)
        validate_profile_snapshot(image)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / "fictional-search-home"
            storage = LocalBackupStorage()
            self.assertFalse(storage.restore_profile(target, image, "c" * 64, confirm=True))
            path = target / "data" / "grounded_apply.db"
            self.assertEqual(path.read_bytes(), image)
            self.assertEqual(int.from_bytes(path.read_bytes()[60:64], "big"), 6)
            with SQLiteRepository(path).initialize() as repository:
                self.assertEqual(repository.schema_version, LATEST_SCHEMA_VERSION)
                self.assertEqual(repository.list_saved_searches(), [])

    def test_exact_v5_snapshot_remains_restorable_after_search_migration(self) -> None:
        image = historical_snapshot(5)
        validate_profile_snapshot(image)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / "fictional-batch-home"
            storage = LocalBackupStorage()
            self.assertFalse(storage.restore_profile(target, image, "b" * 64, confirm=True))
            path = target / "data" / "grounded_apply.db"
            self.assertEqual(path.read_bytes(), image)
            self.assertEqual(int.from_bytes(path.read_bytes()[60:64], "big"), 5)
            with SQLiteRepository(path).initialize() as repository:
                self.assertEqual(repository.schema_version, LATEST_SCHEMA_VERSION)
                self.assertEqual(repository.list_preparation_batches(), [])

    def test_exact_v4_restore_remains_v4_until_explicit_migration(self) -> None:
        image = historical_snapshot()
        validate_profile_snapshot(image)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / "fictional-restored"
            storage = LocalBackupStorage()
            self.assertFalse(storage.restore_profile(target, image, "a" * 64, confirm=False))
            self.assertFalse(target.exists())
            self.assertFalse(storage.restore_profile(target, image, "a" * 64, confirm=True))
            self.assertTrue(storage.restore_profile(target, image, "a" * 64, confirm=True))
            path = target / "data" / "grounded_apply.db"
            self.assertEqual(path.read_bytes(), image)
            self.assertEqual(int.from_bytes(path.read_bytes()[60:64], "big"), 4)
            with SQLiteRepository(path).initialize() as repository:
                self.assertEqual(repository.schema_version, LATEST_SCHEMA_VERSION)
                self.assertEqual(repository.list_claims(), [])
            with SQLiteRepository(path, read_only=True).initialize() as repository:
                validate_profile_snapshot(repository.snapshot_bytes(max_bytes=16 * 1024 * 1024))

    def test_old_version_number_cannot_hide_new_or_modified_sql_objects(self) -> None:
        image = historical_snapshot()
        for statement in (
            "CREATE TABLE invented (value TEXT)",
            "UPDATE schema_migrations SET checksum_sha256='" + "0" * 64 + "' WHERE version=4",
            "PRAGMA user_version=5",
        ):
            with self.subTest(statement=statement), closing(sqlite3.connect(":memory:", isolation_level=None)) as database:
                database.deserialize(image)
                database.execute(statement)
                with self.assertRaises(BackupError):
                    validate_profile_snapshot(database.serialize())

    def test_current_database_cannot_masquerade_as_legacy(self) -> None:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as database:
            initialize_schema(database, default_migrations_directory())
            database.execute("PRAGMA user_version=4")
            with self.assertRaises(BackupError):
                validate_profile_snapshot(database.serialize())

    def test_reference_target_cannot_downgrade_or_bypass_supported_versions(self) -> None:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as database:
            initialize_schema(database, default_migrations_directory())
            before = database.serialize()
            for target in (True, 0, 999, 4):
                with self.subTest(target=target), self.assertRaises(MigrationError):
                    initialize_schema(database, default_migrations_directory(), target_version=target)
                self.assertEqual(database.serialize(), before)


if __name__ == "__main__":
    unittest.main()
