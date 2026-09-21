from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grounded_apply.config import RuntimePaths, resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories import snapshot_capture as capture
from grounded_apply.repositories._schema import SchemaError, default_migrations_directory, initialize_schema
from grounded_apply.repositories.backup_files import LocalBackupStorage
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES, SNAPSHOT_WORK_SECONDS


class SnapshotCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gapply-fictional-capture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture_index = 0

    def profile(self, version: int = 7) -> RuntimePaths:
        self.fixture_index += 1
        paths = resolve_runtime_paths({
            "GROUNDED_APPLY_HOME": str(self.root / f"fictional-home-{self.fixture_index}"),
        })
        paths.ensure_private_directories()
        with closing(sqlite3.connect(paths.database, isolation_level=None)) as connection:
            initialize_schema(connection, default_migrations_directory(), target_version=version)
        paths.database.chmod(0o600)
        return paths

    def source_state(self, paths: RuntimePaths) -> tuple:
        root = paths.portable_root
        assert root is not None
        metadata = paths.database.stat()
        return (
            paths.database.read_bytes(), metadata.st_mtime_ns,
            tuple(sorted(str(path.relative_to(root)) for path in root.rglob("*"))),
        )

    def capture(self, paths: RuntimePaths, versions: frozenset[int] = frozenset({4, 5, 6, 7})) -> bytes:
        return capture.capture_registered_profile_snapshot(paths, allowed_source_versions=versions)

    def read_only_source(
        self, paths: RuntimePaths, *, factory: type[sqlite3.Connection] = sqlite3.Connection,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{paths.database.as_uri()}?mode=ro", uri=True, isolation_level=None, factory=factory,
        )
        self.addCleanup(connection.close)
        connection.execute("PRAGMA query_only = ON")
        return connection

    def assert_closed(self, connections: list[sqlite3.Connection]) -> None:
        self.assertTrue(connections)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_registered_historical_versions_capture_without_source_changes(self) -> None:
        for version in (4, 5, 6, 7):
            with self.subTest(version=version):
                paths = self.profile(version)
                before = self.source_state(paths)
                snapshot = self.capture(paths, frozenset({version}))
                self.assertEqual(int.from_bytes(snapshot[60:64], "big"), version)
                validate_profile_snapshot(snapshot)
                self.assertEqual(self.capture(paths, frozenset({version})), snapshot)
                self.assertEqual(self.source_state(paths), before)
                with closing(sqlite3.connect(":memory:")) as restored:
                    restored.deserialize(snapshot)
                    self.assertEqual(restored.execute("PRAGMA user_version").fetchone()[0], version)
                    self.assertEqual(restored.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], version)

    def test_historical_capture_does_not_relax_current_only_repository_or_backup(self) -> None:
        paths = self.profile(4)
        before = self.source_state(paths)
        self.capture(paths, frozenset({4}))
        with self.assertRaises(SchemaError):
            with SQLiteRepository(paths.database, read_only=True):
                self.fail("A current-only repository accepted a historical schema")
        with self.assertRaises(SchemaError):
            LocalBackupStorage(paths).capture_profile()
        self.assertEqual(self.source_state(paths), before)

    def test_invalid_version_policy_is_rejected_before_filesystem_or_sqlite_access(self) -> None:
        paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(self.root / "absent-fictional-home")})
        invalid = (None, (), {7}, [7], frozenset(), frozenset({True}), frozenset({7.0}),
                   frozenset({"7"}), frozenset({3}), frozenset({8}), frozenset({4, 7, 8}))
        for policy in invalid:
            with self.subTest(policy=policy), patch.object(capture, "require_initialized_profile_storage") as check, patch.object(
                capture.sqlite3, "connect",
            ) as connect:
                with self.assertRaises(capture.SnapshotCaptureError):
                    self.capture(paths, policy)
                check.assert_not_called()
                connect.assert_not_called()
        self.assertFalse(paths.portable_root.exists())

    def test_invalid_or_relative_runtime_paths_fail_before_filesystem_access(self) -> None:
        paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(self.root / "absent-fictional-home")})
        invalid = (None, {}, str(paths.portable_root), replace(paths, data_dir=Path("fictional-relative-data")),
                   replace(paths, data_dir="fictional-invalid-path-type"))
        for supplied in invalid:
            with self.subTest(type=type(supplied).__name__), patch.object(
                capture, "require_initialized_profile_storage",
            ) as check, patch.object(capture.sqlite3, "connect") as connect:
                with self.assertRaises(capture.SnapshotCaptureError):
                    self.capture(supplied, frozenset({7}))
                check.assert_not_called()
                connect.assert_not_called()

    def test_missing_profile_is_not_created_or_initialized(self) -> None:
        paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(self.root / "missing-fictional-home")})
        with patch.object(capture.sqlite3, "connect") as connect, self.assertRaises(capture.SnapshotCaptureError):
            self.capture(paths)
        connect.assert_not_called()
        self.assertFalse(paths.portable_root.exists())

    def test_source_version_policy_is_applied_only_after_snapshot_validation(self) -> None:
        paths = self.profile(7)
        before = self.source_state(paths)
        with patch.object(capture, "validate_profile_snapshot", wraps=validate_profile_snapshot) as validate:
            with self.assertRaises(capture.SnapshotCaptureError):
                self.capture(paths, frozenset({4}))
        validate.assert_called_once()
        snapshot = validate.call_args.args[0]
        self.assertEqual(int.from_bytes(snapshot[60:64], "big"), 7)
        self.assertEqual(self.source_state(paths), before)

    def test_unknown_sql_and_corrupt_ledger_or_future_version_fail_unchanged(self) -> None:
        modifications = (
            "CREATE TABLE fictional_unregistered_table (value TEXT)",
            "DROP INDEX ix_artifacts_content_sha256",
            "UPDATE schema_migrations SET checksum_sha256 = '" + "0" * 64 + "' WHERE version = 4",
            "PRAGMA user_version = 8",
        )
        for sql in modifications:
            with self.subTest(sql=sql):
                paths = self.profile()
                with closing(sqlite3.connect(paths.database, isolation_level=None)) as connection:
                    connection.execute(sql)
                before = self.source_state(paths)
                with self.assertRaises(capture.SnapshotCaptureError):
                    self.capture(paths)
                self.assertEqual(self.source_state(paths), before)

    def test_corrupt_database_bytes_fail_unchanged(self) -> None:
        paths = self.profile()
        image = bytearray(paths.database.read_bytes())
        image[100] = 255
        paths.database.write_bytes(image)
        before = self.source_state(paths)
        with self.assertRaises(capture.SnapshotCaptureError):
            self.capture(paths)
        self.assertEqual(self.source_state(paths), before)

    def test_live_source_uses_guarded_read_only_metadata_and_closes_every_connection(self) -> None:
        paths = self.profile(4)
        original_connect = sqlite3.connect
        connections = []
        source_statements = []
        observed = []

        class SourceProbe(sqlite3.Connection):
            def backup(self, destination: sqlite3.Connection, **kwargs: object) -> None:
                observed.append((
                    self.in_transaction,
                    self.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE),
                    self.execute("PRAGMA query_only").fetchone()[0],
                    self.execute("PRAGMA trusted_schema").fetchone()[0],
                ))
                return super().backup(destination, **kwargs)

        def open_probe(database: str, **kwargs: object) -> sqlite3.Connection:
            if database != ":memory:":
                self.assertEqual(database, f"{paths.database.as_uri()}?mode=ro")
                self.assertIs(kwargs.get("uri"), True)
                connection = original_connect(database, **kwargs, factory=SourceProbe)
                connection.set_trace_callback(source_statements.append)
            else:
                connection = original_connect(database, **kwargs)
            connections.append(connection)
            return connection

        with patch.object(capture.sqlite3, "connect", side_effect=open_probe):
            self.capture(paths, frozenset({4}))
        self.assertEqual(observed, [(True, True, 1, 0)])
        self.assertFalse(any("schema_migrations" in sql.lower() for sql in source_statements))
        self.assertTrue(all(
            sql.startswith("PRAGMA ") or sql in {"BEGIN", "ROLLBACK", "SELECT count(*) FROM sqlite_schema"}
            for sql in source_statements
        ), source_statements)
        self.assertGreaterEqual(len(connections), 2)
        self.assert_closed(connections)

    def test_replaced_ledger_view_is_not_interpreted_before_exact_schema_rejection(self) -> None:
        paths = self.profile()
        with closing(sqlite3.connect(paths.database, isolation_level=None)) as connection:
            connection.execute("DROP TABLE schema_migrations")
            connection.execute("CREATE VIEW schema_migrations AS SELECT fictional_untrusted_function() AS version")
        untrusted_ledger_queries = []
        original_connect = sqlite3.connect

        class SchemaProbe(sqlite3.Connection):
            trusted_reference = False

            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                # The validator builds an independent reference from trusted
                # local migrations. Its own migration ledger may be read; the
                # live source and deserialized image must first pass exact SQL.
                if "CREATE TABLE schema_migrations (" in sql:
                    self.trusted_reference = True
                if "FROM schema_migrations" in sql and not self.trusted_reference:
                    untrusted_ledger_queries.append(sql)
                return super().execute(sql, parameters)

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            return original_connect(*args, **kwargs, factory=SchemaProbe)

        before = self.source_state(paths)
        with patch.object(capture.sqlite3, "connect", side_effect=traced_connect):
            with self.assertRaises(capture.SnapshotCaptureError):
                self.capture(paths)
        self.assertEqual(untrusted_ledger_queries, [])
        self.assertEqual(self.source_state(paths), before)

    def test_database_aliases_and_nonregular_files_fail_before_sqlite_open(self) -> None:
        for kind in ("symlink", "hard_link", "directory", "fifo"):
            with self.subTest(kind=kind):
                paths = self.profile()
                original = paths.database.with_name("fictional-original.db")
                paths.database.rename(original)
                original_bytes = original.read_bytes()
                if kind == "symlink":
                    paths.database.symlink_to(original)
                elif kind == "hard_link":
                    paths.database.hardlink_to(original)
                elif kind == "directory":
                    paths.database.mkdir(mode=0o700)
                else:
                    os.mkfifo(paths.database, 0o600)
                with patch.object(capture.sqlite3, "connect") as connect:
                    with self.assertRaises(capture.SnapshotCaptureError):
                        self.capture(paths)
                    connect.assert_not_called()
                self.assertEqual(original.read_bytes(), original_bytes)

    def test_permission_drift_and_repository_paths_are_refused_without_repair(self) -> None:
        for kind in ("database", "data_directory", "portable_root", "git_worktree"):
            with self.subTest(kind=kind):
                paths = self.profile()
                changed = {"database": paths.database, "data_directory": paths.data_dir,
                           "portable_root": paths.portable_root, "git_worktree": paths.portable_root}[kind]
                if kind == "git_worktree":
                    (changed / ".git").mkdir(mode=0o700)
                else:
                    changed.chmod(0o644 if kind == "database" else 0o755)
                before = self.source_state(paths)
                mode = stat.S_IMODE(changed.stat().st_mode)
                with patch.object(capture.sqlite3, "connect") as connect:
                    with self.assertRaises(capture.SnapshotCaptureError):
                        self.capture(paths)
                    connect.assert_not_called()
                self.assertEqual(stat.S_IMODE(changed.stat().st_mode), mode)
                self.assertEqual(self.source_state(paths), before)

    def test_portable_child_escape_fails_before_sqlite_open(self) -> None:
        paths = self.profile()
        outside = self.root / "fictional-external-state"
        outside.mkdir(mode=0o700)
        escaped = replace(paths, state_dir=outside)
        before = self.source_state(paths)
        with patch.object(capture.sqlite3, "connect") as connect:
            with self.assertRaises(capture.SnapshotCaptureError):
                self.capture(escaped)
            connect.assert_not_called()
        self.assertEqual(self.source_state(paths), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_lexical_symlink_dotdot_path_keeps_its_actual_filesystem_target(self) -> None:
        paths = self.profile(7)
        home = paths.portable_root
        assert home is not None
        nested = home / "fictional-nested"
        nested.mkdir(mode=0o700)
        child = nested / "child"
        child.mkdir(mode=0o700)
        link = home / "fictional-link"
        link.symlink_to(child, target_is_directory=True)
        intended = replace(paths, data_dir=nested / "data")
        intended.ensure_private_directories()
        with closing(sqlite3.connect(intended.database, isolation_level=None)) as connection:
            initialize_schema(connection, default_migrations_directory(), target_version=4)
        intended.database.chmod(0o600)
        before_default = self.source_state(paths)
        before_intended = self.source_state(intended)
        lexical = replace(paths, data_dir=link / ".." / "data")
        snapshot = self.capture(lexical, frozenset({4}))
        self.assertEqual(int.from_bytes(snapshot[60:64], "big"), 4)
        self.assertEqual(self.source_state(paths), before_default)
        self.assertEqual(self.source_state(intended), before_intended)

    def test_sidecars_or_orphans_are_left_untouched_and_never_opened(self) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            for orphan in (False, True):
                with self.subTest(suffix=suffix, orphan=orphan):
                    paths = self.profile()
                    if orphan:
                        paths.database.unlink()
                    sidecar = Path(f"{paths.database}{suffix}")
                    sidecar.write_bytes(b"Fictional recovery sentinel")
                    sidecar.chmod(0o600)
                    before = None if orphan else self.source_state(paths)
                    with patch.object(capture.sqlite3, "connect") as connect:
                        with self.assertRaises(capture.SnapshotCaptureError):
                            self.capture(paths)
                        connect.assert_not_called()
                    self.assertEqual(sidecar.read_bytes(), b"Fictional recovery sentinel")
                    if orphan:
                        self.assertFalse(paths.database.exists())
                    else:
                        self.assertEqual(self.source_state(paths), before)

    def test_persistent_wal_mode_is_refused_without_creating_sidecars(self) -> None:
        paths = self.profile()
        with closing(sqlite3.connect(paths.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
        before = self.source_state(paths)
        with patch.object(capture.sqlite3, "connect") as connect:
            with self.assertRaises(capture.SnapshotCaptureError):
                self.capture(paths)
            connect.assert_not_called()
        self.assertEqual(self.source_state(paths), before)
        self.assertFalse(Path(f"{paths.database}-wal").exists())
        self.assertFalse(Path(f"{paths.database}-shm").exists())

    def test_replacement_immediately_after_open_closes_source_before_any_query(self) -> None:
        paths = self.profile()
        original_connect = sqlite3.connect
        connections = []
        statements = []
        original_bytes = paths.database.read_bytes()
        original = paths.database.with_name("fictional-original.db")

        def substitute_after_open(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            connection.set_trace_callback(statements.append)
            paths.database.rename(original)
            paths.database.write_bytes(original_bytes)
            paths.database.chmod(0o600)
            return connection

        with patch.object(capture.sqlite3, "connect", side_effect=substitute_after_open):
            with self.assertRaises(capture.SnapshotCaptureError):
                self.capture(paths)
        self.assertEqual(statements, [])
        self.assertEqual(len(connections), 1)
        self.assert_closed(connections)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(paths.database.read_bytes(), original_bytes)

    def test_post_serialization_storage_change_is_rejected_and_connections_close(self) -> None:
        for drift in ("permissions", "sidecar"):
            with self.subTest(drift=drift):
                paths = self.profile()
                before = paths.database.read_bytes()
                original_connect = sqlite3.connect
                connections = []

                class ChangeAfterSerialization(sqlite3.Connection):
                    def serialize(self, *, name: str = "main") -> bytes:
                        result = super().serialize(name=name)
                        if drift == "permissions":
                            paths.database.chmod(0o644)
                        else:
                            sidecar = Path(f"{paths.database}-journal")
                            sidecar.write_bytes(b"Fictional late sidecar")
                            sidecar.chmod(0o600)
                        return result

                def open_probe(database: str, **kwargs: object) -> sqlite3.Connection:
                    connection = original_connect(
                        database, **kwargs,
                        factory=ChangeAfterSerialization if database == ":memory:" else sqlite3.Connection,
                    )
                    connections.append(connection)
                    return connection

                with patch.object(capture.sqlite3, "connect", side_effect=open_probe):
                    with self.assertRaises(capture.SnapshotCaptureError):
                        self.capture(paths)
                self.assert_closed(connections)
                self.assertEqual(paths.database.read_bytes(), before)

    def test_post_validation_storage_change_and_cumulative_deadline_fail_closed(self) -> None:
        for failure in ("permission_drift", "deadline"):
            with self.subTest(failure=failure):
                paths = self.profile()
                before = paths.database.read_bytes()
                now = [0.0]

                def validate_then_change(snapshot: bytes) -> None:
                    validate_profile_snapshot(snapshot)
                    if failure == "permission_drift":
                        paths.database.chmod(0o644)
                    else:
                        now[0] = SNAPSHOT_WORK_SECONDS + 1

                with patch.object(capture, "validate_profile_snapshot", side_effect=validate_then_change), patch.object(
                    capture.time, "monotonic", side_effect=lambda: now[0],
                ), self.assertRaises(capture.SnapshotCaptureError):
                    self.capture(paths)
                self.assertEqual(paths.database.read_bytes(), before)

    def test_validation_error_is_content_free_and_closes_connections(self) -> None:
        paths = self.profile()
        connections = []
        original_connect = sqlite3.connect
        private_detail = "fictional-private-sql-or-path-marker"

        def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch.object(capture.sqlite3, "connect", side_effect=tracking_connect), patch.object(
            capture, "validate_profile_snapshot", side_effect=ValueError(private_detail),
        ), self.assertRaises(capture.SnapshotCaptureError) as error:
            self.capture(paths)
        self.assertNotIn(private_detail, str(error.exception))
        self.assertNotIn(str(paths.database), str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)
        self.assert_closed(connections)

    def test_validation_interrupt_is_preserved_and_closes_connections(self) -> None:
        paths = self.profile()
        connections = []
        original_connect = sqlite3.connect
        interruption = KeyboardInterrupt("fictional interrupted validation")

        def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch.object(capture.sqlite3, "connect", side_effect=tracking_connect), patch.object(
            capture, "validate_profile_snapshot", side_effect=interruption,
        ), self.assertRaises(KeyboardInterrupt) as error:
            self.capture(paths)
        self.assertIs(error.exception, interruption)
        self.assert_closed(connections)

    def test_source_or_destination_setup_failure_closes_owned_connections(self) -> None:
        for failure in ("source_setconfig", "source_pragma", "destination_pragma"):
            with self.subTest(failure=failure):
                paths = self.profile()
                before = self.source_state(paths)
                original_connect = sqlite3.connect
                connections = []
                private_detail = "fictional-private-setup-failure"

                class SetupFailure(sqlite3.Connection):
                    is_source = False

                    def setconfig(self, op: int, enable: bool = True, /) -> None:
                        if self.is_source and failure == "source_setconfig":
                            raise sqlite3.OperationalError(private_detail)
                        super().setconfig(op, enable)

                    def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                        if (self.is_source and failure == "source_pragma" and sql == "PRAGMA trusted_schema = OFF") or (
                            not self.is_source and failure == "destination_pragma" and sql == "PRAGMA temp_store = MEMORY"
                        ):
                            raise sqlite3.OperationalError(private_detail)
                        return super().execute(sql, parameters)

                def setup_probe(database: str, **kwargs: object) -> sqlite3.Connection:
                    connection = original_connect(database, **kwargs, factory=SetupFailure)
                    connection.is_source = database != ":memory:"
                    connections.append(connection)
                    return connection

                with patch.object(capture.sqlite3, "connect", side_effect=setup_probe), self.assertRaises(capture.SnapshotCaptureError) as error:
                    self.capture(paths)
                self.assertNotIn(private_detail, str(error.exception))
                self.assertEqual(len(connections), 2 if failure == "destination_pragma" else 1)
                self.assert_closed(connections)
                self.assertEqual(self.source_state(paths), before)

    def test_low_level_rejects_invalid_bounds_before_destination_open(self) -> None:
        paths = self.profile()
        source = self.read_only_source(paths)
        for maximum in (True, 0, -1, 1.5, None):
            with self.subTest(maximum=maximum), patch.object(capture.sqlite3, "connect") as connect:
                with self.assertRaises(capture.SnapshotCaptureError):
                    capture._capture_read_only_snapshot(source, max_bytes=maximum, revalidate_storage=lambda: None)
                connect.assert_not_called()
                self.assertFalse(source.in_transaction)

    def test_low_level_refuses_non_query_only_and_existing_caller_transaction(self) -> None:
        paths = self.profile()
        source = self.read_only_source(paths)
        for state in ("not_query_only", "existing_transaction"):
            with self.subTest(state=state):
                if state == "not_query_only":
                    source.execute("PRAGMA query_only = OFF")
                else:
                    source.execute("PRAGMA query_only = ON")
                    source.execute("BEGIN")
                with patch.object(capture.sqlite3, "connect") as connect:
                    with self.assertRaises(capture.SnapshotCaptureError):
                        capture._capture_read_only_snapshot(source, max_bytes=MAX_SNAPSHOT_BYTES, revalidate_storage=lambda: None)
                    connect.assert_not_called()
                self.assertEqual(source.in_transaction, state == "existing_transaction")
                source.rollback()

    def test_low_level_size_failure_rolls_back_and_leaves_source_reusable(self) -> None:
        paths = self.profile()
        source = self.read_only_source(paths)
        revalidations = []
        with self.assertRaises(capture.SnapshotCaptureError):
            capture._capture_read_only_snapshot(source, max_bytes=100, revalidate_storage=lambda: revalidations.append(True))
        self.assertFalse(source.in_transaction)
        snapshot = capture._capture_read_only_snapshot(
            source, max_bytes=MAX_SNAPSHOT_BYTES, revalidate_storage=lambda: revalidations.append(True),
        )
        validate_profile_snapshot(snapshot)
        self.assertFalse(source.in_transaction)
        self.assertGreaterEqual(len(revalidations), 2)

    def test_low_level_expiry_after_serialization_rolls_back_and_closes_destination(self) -> None:
        paths = self.profile()
        source = self.read_only_source(paths)
        now = [0.0]
        destinations = []
        original_connect = sqlite3.connect

        class SlowSerialization(sqlite3.Connection):
            def serialize(self, *, name: str = "main") -> bytes:
                result = super().serialize(name=name)
                now[0] = SNAPSHOT_WORK_SECONDS + 1
                return result

        def destination_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs, factory=SlowSerialization)
            destinations.append(connection)
            return connection

        with patch.object(capture.sqlite3, "connect", side_effect=destination_connect), patch.object(
            capture.time, "monotonic", side_effect=lambda: now[0],
        ), self.assertRaises(capture.SnapshotCaptureError):
            capture._capture_read_only_snapshot(source, max_bytes=MAX_SNAPSHOT_BYTES, revalidate_storage=lambda: None)
        self.assertFalse(source.in_transaction)
        self.assert_closed(destinations)
        self.assertEqual(source.execute("PRAGMA user_version").fetchone()[0], 7)

    def test_low_level_interrupt_survives_rollback_error_and_destination_still_closes(self) -> None:
        paths = self.profile()
        interruption = KeyboardInterrupt("fictional interrupted copy")
        rollback_attempts = []

        class InterruptedSource(sqlite3.Connection):
            def backup(self, destination: sqlite3.Connection, **kwargs: object) -> None:
                raise interruption

            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                if sql == "ROLLBACK":
                    rollback_attempts.append(True)
                    raise sqlite3.OperationalError("fictional rollback detail")
                return super().execute(sql, parameters)

        source = self.read_only_source(paths, factory=InterruptedSource)
        destinations = []
        original_connect = sqlite3.connect

        def destination_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            destinations.append(connection)
            return connection

        with patch.object(capture.sqlite3, "connect", side_effect=destination_connect), self.assertRaises(KeyboardInterrupt) as error:
            capture._capture_read_only_snapshot(source, max_bytes=MAX_SNAPSHOT_BYTES, revalidate_storage=lambda: None)
        self.assertIs(error.exception, interruption)
        self.assertEqual(rollback_attempts, [True])
        self.assert_closed(destinations)
        # The helper owns the transaction, not the source. A failed rollback
        # cannot be claimed successful; the caller must close this connection.
        self.assertTrue(source.in_transaction)
        source.close()

    def test_interrupt_immediately_after_begin_rolls_back_owned_transaction(self) -> None:
        paths = self.profile()
        interruption = KeyboardInterrupt("fictional interruption after BEGIN")

        class BeginInterruptedSource(sqlite3.Connection):
            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                result = super().execute(sql, parameters)
                if sql == "BEGIN":
                    raise interruption
                return result

        source = self.read_only_source(paths, factory=BeginInterruptedSource)
        destinations = []
        original_connect = sqlite3.connect

        def destination_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            destinations.append(connection)
            return connection

        with patch.object(capture.sqlite3, "connect", side_effect=destination_connect), self.assertRaises(KeyboardInterrupt) as error:
            capture._capture_read_only_snapshot(source, max_bytes=MAX_SNAPSHOT_BYTES, revalidate_storage=lambda: None)
        self.assertIs(error.exception, interruption)
        self.assertFalse(source.in_transaction)
        self.assert_closed(destinations)
        self.assertEqual(source.execute("PRAGMA user_version").fetchone()[0], 7)


if __name__ == "__main__":
    unittest.main()
