from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from grounded_apply.repositories import _schema
from grounded_apply.repositories import snapshots
from grounded_apply.services.backup import BackupError


FICTIONAL_MIGRATION = """CREATE TABLE fictional_conversion_payload (
    fixture_id TEXT PRIMARY KEY,
    payload_bytes BLOB NOT NULL
);
"""


class MigrationPolicyTests(unittest.TestCase):
    def new_connection(self, path: str | Path = ":memory:") -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(connection.close)
        return connection

    def copied_migrations(self, *, staged: bool = True) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="gapply-fictional-migration-policy-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "migrations"
        shutil.copytree(_schema.default_migrations_directory(), directory)
        if staged:
            (directory / "008_fictional_conversion.sql").write_text(FICTIONAL_MIGRATION, encoding="utf-8")
        return directory

    @contextmanager
    def staged_policy(
        self, *, latest: int = 7, conversion_versions: tuple[int, ...] = (8,),
    ) -> Iterator[None]:
        policy = MappingProxyType({
            version: (
                _schema.MigrationExecution.CONVERSION_ONLY
                if version in conversion_versions else _schema.MigrationExecution.IN_PLACE
            )
            for version in range(1, 9)
        })
        with patch.multiple(
            _schema, LATEST_SCHEMA_VERSION=latest, REGISTERED_SCHEMA_VERSION=8,
            _MIGRATION_EXECUTION_POLICY=policy,
        ):
            yield

    def state(self, connection: sqlite3.Connection) -> tuple:
        objects = connection.execute(_schema.SCHEMA_OBJECTS_QUERY).fetchall()
        ledger = connection.execute(
            "SELECT version, name, checksum_sha256, applied_at FROM schema_migrations ORDER BY version",
        ).fetchall() if any(row[1] == "schema_migrations" for row in objects) else []
        return _schema.read_schema_version(connection), objects, ledger, connection.total_changes

    def test_current_registration_and_checksums_remain_exactly_one_through_seven(self) -> None:
        self.assertEqual((_schema.LATEST_SCHEMA_VERSION, _schema.REGISTERED_SCHEMA_VERSION), (7, 7))
        self.assertIsInstance(_schema._MIGRATION_EXECUTION_POLICY, MappingProxyType)
        directory = _schema.default_migrations_directory()
        migrations = _schema.load_migrations(directory)
        self.assertEqual([migration.version for migration in migrations], list(range(1, 8)))
        for migration in migrations:
            self.assertIs(migration.execution, _schema.MigrationExecution.IN_PLACE)
            original = (directory / migration.name).read_text(encoding="utf-8")
            self.assertEqual(migration.sql, original)
            self.assertEqual(migration.checksum_sha256, hashlib.sha256(original.encode("utf-8")).hexdigest())

    def test_staged_migration_does_not_change_default_fresh_or_existing_target(self) -> None:
        directory = self.copied_migrations()
        for initial in (0, 4, 5, 6, 7):
            with self.subTest(initial=initial):
                connection = self.new_connection()
                if initial:
                    _schema.initialize_schema(connection, _schema.default_migrations_directory(), target_version=initial)
                before = self.state(connection)[2]
                with self.staged_policy():
                    loaded = _schema.load_migrations(directory)
                    self.assertEqual(len(loaded), 8)
                    self.assertIs(loaded[-1].execution, _schema.MigrationExecution.CONVERSION_ONLY)
                    self.assertEqual(_schema.initialize_schema(connection, directory), 7)
                    self.assertEqual(_schema.validate_schema(connection, directory), 7)
                after = self.state(connection)
                self.assertEqual(after[2][:initial], before)
                self.assertEqual(len(after[2]), 7)
                self.assertFalse(any(row[1] == "fictional_conversion_payload" for row in after[1]))

    def test_explicit_target_cannot_activate_a_registered_future_schema(self) -> None:
        directory = self.copied_migrations()
        for initial in (0, 7):
            with self.subTest(initial=initial):
                connection = self.new_connection()
                if initial:
                    _schema.initialize_schema(connection, _schema.default_migrations_directory())
                before = self.state(connection)
                with self.staged_policy(), self.assertRaises(_schema.MigrationError):
                    _schema.initialize_schema(connection, directory, target_version=8)
                self.assertEqual(self.state(connection), before)
                self.assertFalse(connection.in_transaction)

    def test_conversion_boundary_is_preflighted_before_every_pending_migration(self) -> None:
        directory = self.copied_migrations()
        for initial in (0, 4, 5, 7):
            for explicit_target in (None, 8):
                with self.subTest(initial=initial, target=explicit_target):
                    connection = self.new_connection()
                    if initial:
                        _schema.initialize_schema(connection, _schema.default_migrations_directory(), target_version=initial)
                    before = self.state(connection)
                    with self.staged_policy(latest=8), self.assertRaises(_schema.ConversionRequiredError):
                        _schema.initialize_schema(connection, directory, target_version=explicit_target)
                    self.assertEqual(self.state(connection), before)
                    self.assertFalse(connection.in_transaction)

    def test_conversion_boundary_in_middle_of_pending_range_also_blocks_earlier_steps(self) -> None:
        directory = self.copied_migrations()
        connection = self.new_connection()
        _schema.initialize_schema(connection, _schema.default_migrations_directory(), target_version=4)
        before = self.state(connection)
        with self.staged_policy(latest=8, conversion_versions=(6,)), self.assertRaises(_schema.ConversionRequiredError):
            _schema.initialize_schema(connection, directory, target_version=7)
        self.assertEqual(self.state(connection), before)

    def test_explicit_target_before_conversion_boundary_still_upgrades_normally(self) -> None:
        directory = self.copied_migrations()
        connection = self.new_connection()
        _schema.initialize_schema(connection, _schema.default_migrations_directory(), target_version=4)
        before = self.state(connection)[2]
        with self.staged_policy(latest=8):
            self.assertEqual(_schema.initialize_schema(connection, directory, target_version=7), 7)
        self.assertEqual(self.state(connection)[2][:4], before)
        self.assertIsNone(connection.execute(
            "SELECT name FROM sqlite_schema WHERE name = 'fictional_conversion_payload'",
        ).fetchone())

    def test_already_applied_conversion_policy_does_not_rewrite_existing_history(self) -> None:
        directory = self.copied_migrations()
        connection = self.new_connection()
        _schema.initialize_schema(connection, _schema.default_migrations_directory())
        before = self.state(connection)
        with self.staged_policy(conversion_versions=(6, 8)):
            self.assertEqual(_schema.initialize_schema(connection, directory), 7)
        self.assertEqual(self.state(connection), before)

    def test_policy_requires_immutable_complete_exactly_typed_entries(self) -> None:
        good = dict(_schema._MIGRATION_EXECUTION_POLICY)
        cases = (
            good,
            tuple(good.items()),
            MappingProxyType({version: mode for version, mode in good.items() if version != 4}),
            MappingProxyType(good | {8: _schema.MigrationExecution.IN_PLACE}),
            MappingProxyType({True: good[1]} | {version: mode for version, mode in good.items() if version != 1}),
            MappingProxyType(good | {4: "in_place"}),
            MappingProxyType(good | {4: None}),
            MappingProxyType({str(version): mode for version, mode in good.items()}),
        )
        for index, policy in enumerate(cases):
            with self.subTest(case=index), patch.object(_schema, "_MIGRATION_EXECUTION_POLICY", policy):
                connection = self.new_connection()
                with self.assertRaises(_schema.MigrationError):
                    _schema.load_migrations(_schema.default_migrations_directory())
                with self.assertRaises(_schema.MigrationError):
                    _schema.initialize_schema(connection, _schema.default_migrations_directory())
                self.assertEqual(connection.execute("SELECT name FROM sqlite_schema").fetchall(), [])
                self.assertEqual(_schema.read_schema_version(connection), 0)

    def test_registration_bounds_reject_booleans_nonintegers_and_invalid_order(self) -> None:
        cases = ((True, 7), (7, True), (0, 7), (8, 7), (7, 1000), (7, 0), ("7", 7), (7, 7.0))
        for latest, registered in cases:
            with self.subTest(latest=latest, registered=registered), patch.multiple(
                _schema, LATEST_SCHEMA_VERSION=latest, REGISTERED_SCHEMA_VERSION=registered,
            ), self.assertRaises(_schema.MigrationError):
                _schema.load_migrations(_schema.default_migrations_directory())

    def test_missing_duplicate_extra_empty_and_invalid_migration_files_fail_closed(self) -> None:
        for corruption in ("missing", "duplicate", "extra", "empty", "invalid_name"):
            with self.subTest(corruption=corruption):
                directory = self.copied_migrations()
                if corruption == "missing":
                    (directory / "005_preparation_batches.sql").unlink()
                elif corruption == "duplicate":
                    (directory / "008_other_fictional.sql").write_text(FICTIONAL_MIGRATION, encoding="utf-8")
                elif corruption == "extra":
                    (directory / "009_extra_fictional.sql").write_text(FICTIONAL_MIGRATION, encoding="utf-8")
                elif corruption == "empty":
                    (directory / "008_fictional_conversion.sql").write_text(" \n", encoding="utf-8")
                else:
                    (directory / "fictional.sql").write_text(FICTIONAL_MIGRATION, encoding="utf-8")
                connection = self.new_connection()
                with self.staged_policy(), self.assertRaises(_schema.MigrationError):
                    _schema.initialize_schema(connection, directory)
                self.assertEqual(connection.execute("SELECT name FROM sqlite_schema").fetchall(), [])
                self.assertEqual(_schema.read_schema_version(connection), 0)
        with self.assertRaises(_schema.MigrationError):
            _schema.load_migrations(self.copied_migrations())

    def test_staging_preserves_checksum_mismatch_refusal(self) -> None:
        directory = self.copied_migrations()
        connection = self.new_connection()
        _schema.initialize_schema(connection, _schema.default_migrations_directory(), target_version=4)
        first = directory / "001_initial.sql"
        first.write_text(first.read_text(encoding="utf-8") + "\n-- fictional altered history\n", encoding="utf-8")
        before = self.state(connection)
        with self.staged_policy(), self.assertRaisesRegex(_schema.SchemaError, "checksum"):
            _schema.initialize_schema(connection, directory)
        self.assertEqual(self.state(connection), before)

    def test_current_validation_rejects_registered_future_version_and_future_ledger(self) -> None:
        directory = self.copied_migrations()
        for future_version in (False, True):
            with self.subTest(future_version=future_version):
                connection = self.new_connection()
                _schema.initialize_schema(connection, _schema.default_migrations_directory())
                if future_version:
                    connection.execute("PRAGMA user_version = 8")
                else:
                    connection.execute(
                        "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                        (8, "008_fictional_conversion.sql", hashlib.sha256(FICTIONAL_MIGRATION.encode()).hexdigest(),
                         "2026-09-20T00:00:00Z"),
                    )
                before = self.state(connection)
                with self.staged_policy():
                    for operation in (_schema.validate_schema, _schema.initialize_schema):
                        with self.assertRaises(_schema.FutureSchemaError):
                            operation(connection, directory)
                self.assertEqual(self.state(connection), before)
                self.assertFalse(connection.in_transaction)

    def test_reference_builder_constructs_registered_conversion_schema_in_isolation(self) -> None:
        directory = self.copied_migrations()
        connection = self.new_connection()
        _schema.initialize_schema(connection, _schema.default_migrations_directory())
        before = self.state(connection)
        with self.staged_policy():
            current = _schema.reference_schema_objects(directory, target_version=7)
            staged = _schema.reference_schema_objects(directory, target_version=8)
        self.assertIsInstance(current, tuple)
        self.assertEqual(current, tuple(before[1]))
        self.assertEqual(
            [row[1] for row in staged if row[0] == "table" and row[1] == "fictional_conversion_payload"],
            ["fictional_conversion_payload"],
        )
        self.assertEqual(self.state(connection), before)
        with self.assertRaises(TypeError):
            _schema.reference_schema_objects(directory, target_version=7, connection=connection)

    def test_registration_cannot_expand_the_independent_restore_allowlist(self) -> None:
        connection = self.new_connection()
        _schema.initialize_schema(connection, _schema.default_migrations_directory())
        image = bytearray(connection.serialize())
        image[60:64] = (8).to_bytes(4, "big")
        with self.staged_policy(latest=8), patch.object(snapshots, "LATEST_SCHEMA_VERSION", 8):
            with patch.object(snapshots, "reference_schema_objects") as reference:
                with self.assertRaises(BackupError):
                    snapshots.validate_profile_snapshot(bytes(image))
            reference.assert_not_called()
        self.assertEqual(snapshots._RESTORABLE_SCHEMA_VERSIONS, frozenset({4, 5, 6, 7}))

    def test_reference_builder_closes_its_connection_on_success_and_sql_failure(self) -> None:
        directory = self.copied_migrations()
        opened = []
        real_connect = sqlite3.connect

        def isolated_connect(database: str, *args: object, **kwargs: object) -> sqlite3.Connection:
            self.assertEqual(database, ":memory:")
            connection = real_connect(database, *args, **kwargs)
            opened.append(connection)
            return connection

        with self.staged_policy(), patch.object(_schema.sqlite3, "connect", side_effect=isolated_connect):
            _schema.reference_schema_objects(directory, target_version=8)
            (directory / "008_fictional_conversion.sql").write_text(
                FICTIONAL_MIGRATION + "SELECT fictional_missing_function();\n", encoding="utf-8",
            )
            with self.assertRaises(_schema.MigrationError):
                _schema.reference_schema_objects(directory, target_version=8)
        self.assertEqual(len(opened), 2)
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_reference_and_normal_targets_are_strict_positive_registered_integers(self) -> None:
        directory = _schema.default_migrations_directory()
        for target in (True, False, 0, -1, 8, "7", 7.0):
            with self.subTest(target=target):
                connection = self.new_connection()
                with self.assertRaises(_schema.MigrationError):
                    _schema.initialize_schema(connection, directory, target_version=target)
                with self.assertRaises(_schema.MigrationError):
                    _schema.reference_schema_objects(directory, target_version=target)
                self.assertEqual(connection.execute("SELECT name FROM sqlite_schema").fetchall(), [])

    def test_competing_initializer_advancing_beyond_target_before_write_lock_is_refused(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gapply-fictional-target-race-")
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "fictional.db"
        writer = self.new_connection(database)
        directory = _schema.default_migrations_directory()
        _schema.initialize_schema(writer, directory, target_version=5)
        winner_images = []

        class RacingConnection(sqlite3.Connection):
            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                if sql == "BEGIN IMMEDIATE" and not winner_images:
                    _schema.initialize_schema(writer, directory)
                    winner_images.append(database.read_bytes())
                return super().execute(sql, parameters)

        reader = sqlite3.connect(database, isolation_level=None, factory=RacingConnection)
        self.addCleanup(reader.close)
        with self.assertRaises(_schema.MigrationError):
            _schema.initialize_schema(reader, directory, target_version=6)
        self.assertEqual(len(winner_images), 1)
        self.assertEqual(_schema.read_schema_version(reader), 7)
        self.assertFalse(reader.in_transaction)
        self.assertFalse(writer.in_transaction)
        self.assertEqual(database.read_bytes(), winner_images[0])
        self.assertEqual(_schema.validate_schema(reader, directory), 7)

    def test_competing_initializer_after_initial_validation_cannot_return_wrong_target(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gapply-fictional-final-target-race-")
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "fictional.db"
        writer = self.new_connection(database)
        reader = self.new_connection(database)
        directory = _schema.default_migrations_directory()
        _schema.initialize_schema(writer, directory, target_version=5)
        original_validate = _schema._validate_schema_state
        winner_images = []

        def validate_then_advance(connection: sqlite3.Connection, *args: object, **kwargs: object) -> int:
            version = original_validate(connection, *args, **kwargs)
            if connection is reader and not winner_images:
                _schema.initialize_schema(writer, directory)
                winner_images.append(database.read_bytes())
            return version

        with patch.object(_schema, "_validate_schema_state", side_effect=validate_then_advance):
            with self.assertRaises(_schema.MigrationError):
                _schema.initialize_schema(reader, directory, target_version=5)
        self.assertEqual(len(winner_images), 1)
        self.assertEqual(_schema.read_schema_version(reader), 7)
        self.assertFalse(reader.in_transaction)
        self.assertEqual(database.read_bytes(), winner_images[0])


if __name__ == "__main__":
    unittest.main()
