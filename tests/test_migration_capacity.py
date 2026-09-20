from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories._schema import (
    MigrationError, default_migrations_directory, initialize_schema,
)
from grounded_apply.repositories.snapshots import validate_profile_snapshot

# Keep rollback fixtures small; the optional storage gate exercises real
# production capacity without making every base-suite run allocate hundreds of MiB.
TEST_CAPACITY_BYTES = 16 * 1024 * 1024


class MigrationCapacityTests(unittest.TestCase):
    def test_near_full_registered_snapshot_stays_restorable_when_upgrade_cannot_fit(self) -> None:
        for version in (5, 6):
            with self.subTest(version=version):
                self.check_near_full_upgrade(version)

    def check_near_full_upgrade(self, version: int) -> None:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as source:
            initialize_schema(source, default_migrations_directory(), target_version=version)
            padding = TEST_CAPACITY_BYTES - len(source.serialize()) - 64000
            # Conspicuously fictional queued metadata exercises allocated-page
            # growth without private data or a nonregistered SQL table.
            source.execute(
                "INSERT INTO workflow_runs (id,workflow_type,input_json,created_at,updated_at) VALUES (?,?,?,?,?)",
                ("fictional-size-probe", "fictional-size-probe",
                 json.dumps({"fictional_padding": "x" * padding}),
                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            image = source.serialize()
        self.assertLessEqual(len(image), TEST_CAPACITY_BYTES)
        validate_profile_snapshot(image)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fictional-near-full.db"
            path.write_bytes(image)
            os.chmod(path, 0o600)
            with closing(SQLiteRepository(path)) as repository, patch(
                "grounded_apply.services.backup.MAX_SNAPSHOT_BYTES", TEST_CAPACITY_BYTES,
            ):
                with self.assertRaisesRegex(MigrationError, "storage limit"):
                    repository.initialize()
                self.assertEqual(repository.schema_version, version)
            self.assertEqual(path.read_bytes(), image)
            self.assertFalse(Path(str(path) + "-journal").exists())
            validate_profile_snapshot(path.read_bytes())

    def test_migration_bound_validates_before_any_schema_mutation(self) -> None:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as database:
            for maximum in (True, 0, -1, 1.5):
                with self.subTest(maximum=maximum), self.assertRaises(MigrationError):
                    initialize_schema(database, default_migrations_directory(), max_database_bytes=maximum)
                self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(database.execute("SELECT name FROM sqlite_schema").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
