from __future__ import annotations

import sqlite3
import unittest
from collections.abc import Iterator
from contextlib import closing, contextmanager
from hashlib import sha256
from unittest.mock import patch

from grounded_apply.repositories import RepositoryClosedError, RepositoryError, SQLiteRepository
from grounded_apply.repositories import snapshots
from grounded_apply.repositories import sqlite as repository_module
from grounded_apply.repositories._schema import default_migrations_directory, initialize_schema
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES, SNAPSHOT_WORK_SECONDS
from grounded_apply.services.jobs import JobService
from grounded_apply.services.materials import MaterialService
from tests.test_materials import SyntheticRenderer, approved_fixture


class SnapshotRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # A single conspicuously fictional fixture exercises existing services.
        # The constructor itself must not render, approve, or modify anything.
        with SQLiteRepository(":memory:") as fixture:
            cls.job_id, cls.claim_ids = approved_fixture(fixture)
            material = MaterialService(fixture, SyntheticRenderer()).build(
                cls.job_id, cls.claim_ids, idempotency_key="fictional-snapshot-material",
            )
            cls.material_id = material["material_id"]
            cls.claims = fixture.list_claims()
            cls.evidence = fixture.list_evidence()
            cls.job = fixture.get_job_snapshot(cls.job_id)
            cls.requirements = fixture.list_job_requirements(cls.job_id)
            cls.material = fixture.get_material_version(cls.material_id)
            cls.material_claim_ids = fixture.list_material_claim_ids(cls.material_id)
            cls.workflow_runs = fixture.list_workflow_runs()
            cls.snapshot = fixture._connection.serialize()
        snapshots.validate_profile_snapshot(cls.snapshot)

    def repository(self) -> SQLiteRepository:
        repository = SQLiteRepository.from_snapshot(self.snapshot)
        self.addCleanup(repository.close)
        return repository

    def changed_snapshot(self, sql: str) -> bytes:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as connection:
            connection.deserialize(self.snapshot)
            connection.execute(sql)
            return connection.serialize()

    @contextmanager
    def probe_owned_connections(
        self, factory: type[sqlite3.Connection] = sqlite3.Connection,
    ) -> Iterator[list[sqlite3.Connection]]:
        # Faults target the repository-owned connection after real validation,
        # never the independent schema-reference or validation connections.
        validated = [False]
        owned = []
        real_validate = snapshots.validate_profile_snapshot
        real_connect = sqlite3.connect

        def validate_then_mark(snapshot: bytes) -> None:
            real_validate(snapshot)
            validated[0] = True

        def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(
                *args, **kwargs, factory=factory if validated[0] else sqlite3.Connection,
            )
            if validated[0]:
                owned.append(connection)
            return connection

        with patch.object(snapshots, "validate_profile_snapshot", side_effect=validate_then_mark), patch.object(
            repository_module.sqlite3, "connect", side_effect=tracked_connect,
        ):
            yield owned

    def assert_connections_closed(self, connections: list[sqlite3.Connection]) -> None:
        self.assertTrue(connections)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_constructor_returns_initialized_exact_values_and_preserves_input(self) -> None:
        original_digest = sha256(self.snapshot).hexdigest()
        with SQLiteRepository.from_snapshot(self.snapshot) as repository:
            self.assertEqual(repository.schema_version, 7)
            self.assertEqual(repository.list_claims(), self.claims)
            self.assertEqual(repository.list_evidence(), self.evidence)
            self.assertEqual(repository.get_job_snapshot(self.job_id), self.job)
            self.assertEqual(repository.list_job_requirements(self.job_id), self.requirements)
            self.assertEqual(repository.get_material_version(self.material_id), self.material)
            self.assertEqual(repository.list_material_claim_ids(self.material_id), self.material_claim_ids)
            self.assertEqual(repository.list_workflow_runs(), self.workflow_runs)
            self.assertIsNone(repository.get_material_approval(self.material_id))
        self.assertEqual(sha256(self.snapshot).hexdigest(), original_digest)

    def test_snapshot_repository_uses_defensive_query_only_memory_without_retaining_raw_input(self) -> None:
        repository = self.repository()
        connection = repository._connection
        self.assertEqual(repository.database, ":memory:")
        self.assertTrue(connection.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE))
        self.assertEqual(connection.execute("PRAGMA trusted_schema").fetchone()[0], 0)
        self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA temp_store").fetchone()[0], 2)
        self.assertTrue(all(row[2] == "" for row in connection.execute("PRAGMA database_list")))
        self.assertFalse(any(type(value) is bytes and value == self.snapshot for value in vars(repository).values()))
        self.assertEqual(repository.list_claims(), self.claims)

    def test_constructor_and_reads_do_not_open_runtime_files_render_or_use_transport(self) -> None:
        opened = []
        real_connect = sqlite3.connect

        def memory_only(database: str, **kwargs: object) -> sqlite3.Connection:
            self.assertEqual(database, ":memory:")
            opened.append(database)
            return real_connect(database, **kwargs)

        with patch.object(repository_module.sqlite3, "connect", side_effect=memory_only), patch.object(
            repository_module, "prepare_sqlite_path", side_effect=AssertionError("Unexpected filesystem preparation"),
        ) as prepare, patch.object(
            repository_module, "require_safe_sqlite_path", side_effect=AssertionError("Unexpected filesystem guard"),
        ) as guard, patch.object(
            repository_module, "initialize_schema", side_effect=AssertionError("Unexpected profile migration"),
        ) as migrate, patch.object(
            SyntheticRenderer, "render", side_effect=AssertionError("Unexpected rendering"),
        ) as renderer, patch("socket.create_connection", side_effect=AssertionError("Unexpected transport")) as transport, patch(
            "subprocess.run", side_effect=AssertionError("Unexpected external process"),
        ) as process:
            with SQLiteRepository.from_snapshot(self.snapshot) as repository:
                self.assertEqual(repository.get_material_version(self.material_id), self.material)
                self.assertEqual(JobService(repository).get(self.job_id).source_text, self.job["source_text"])
            for forbidden in (prepare, guard, migrate, renderer, transport, process):
                forbidden.assert_not_called()
        self.assertGreaterEqual(len(opened), 2)

    def test_constructor_has_no_writable_or_external_connection_options(self) -> None:
        for option in ("read_only", "existing_only", "connection", "migrations_dir", "database"):
            with self.subTest(option=option), self.assertRaises(TypeError):
                SQLiteRepository.from_snapshot(self.snapshot, **{option: False})

    def test_mutation_transactions_and_typed_insertions_are_blocked(self) -> None:
        repository = self.repository()
        before = repository.snapshot_bytes(max_bytes=MAX_SNAPSHOT_BYTES)
        with self.assertRaises(RepositoryError):
            with repository.transaction():
                self.fail("A snapshot repository entered a write transaction")
        with self.assertRaises(RepositoryError):
            repository.add_claim(
                claim_type="skill_use", value="Rust", canonical_text="Fictional attempted new skill",
                source_type="user_statement", source_ref="fictional test mutation",
            )
        with self.assertRaises(RepositoryError):
            JobService(repository).add(
                "https://example.com/jobs/fictional-new", "Fictional engineer requiring Python.",
                idempotency_key="fictional-new-job",
            )
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(repository.snapshot_bytes(max_bytes=MAX_SNAPSHOT_BYTES), before)

    def test_direct_insert_update_and_sql_write_paths_are_blocked_by_query_only(self) -> None:
        repository = self.repository()
        before = repository.snapshot_bytes(max_bytes=MAX_SNAPSHOT_BYTES)
        operations = (
            lambda: repository.insert_material_approval(
                material_id=self.material_id, bundle_sha256=self.material["bundle_sha256"],
                actor_id="fictional-actor", approved_at="2026-09-20T00:00:00Z", workflow_run_id="fictional-workflow",
            ),
            lambda: repository.acquire_preparation_lease(
                "fictional-batch", expected_epoch=0, owner="fictional-owner",
                now="2026-09-20T00:00:00Z", expires_at="2026-09-20T00:01:00Z",
            ),
            lambda: repository._connection.execute("UPDATE claims SET canonical_text = 'fictional mutation'"),
        )
        for operation in operations:
            with self.assertRaises(sqlite3.OperationalError) as error:
                operation()
            self.assertEqual(error.exception.sqlite_errorcode, sqlite3.SQLITE_READONLY)
        self.assertEqual(repository.snapshot_bytes(max_bytes=MAX_SNAPSHOT_BYTES), before)

    def test_nested_reads_keep_outer_transaction_owned_until_outer_exit(self) -> None:
        repository = self.repository()
        with repository.read_transaction() as outer:
            self.assertIs(outer, repository)
            self.assertTrue(repository._connection.in_transaction)
            with repository.read_transaction() as inner:
                self.assertIs(inner, repository)
                self.assertEqual(inner.get_job_snapshot(self.job_id), self.job)
            self.assertTrue(repository._connection.in_transaction)
            with self.assertRaisesRegex(ValueError, "fictional inner failure"):
                with repository.read_transaction():
                    raise ValueError("fictional inner failure")
            self.assertTrue(repository._connection.in_transaction)
            self.assertEqual(repository.list_claims(), self.claims)
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(repository.get_material_version(self.material_id), self.material)

    def test_read_body_failure_or_interrupt_rolls_back_and_preserves_original(self) -> None:
        repository = self.repository()
        for original in (ValueError("fictional read failure"), KeyboardInterrupt("fictional interrupted read")):
            with self.subTest(error=type(original).__name__), self.assertRaises(type(original)) as raised:
                with repository.read_transaction():
                    raise original
            self.assertIs(raised.exception, original)
            self.assertFalse(repository._connection.in_transaction)
            self.assertEqual(repository.list_claims(), self.claims)

    def test_close_is_idempotent_and_safe_inside_a_read_context(self) -> None:
        repository = self.repository()
        connection = repository._connection
        with repository.read_transaction():
            repository.close()
        repository.close()
        with self.assertRaises(RepositoryClosedError):
            repository.list_claims()
        self.assert_connections_closed([connection])

    def test_restorable_historical_snapshots_still_fail_the_current_repository_boundary(self) -> None:
        for version in (4, 5, 6):
            with self.subTest(version=version), closing(sqlite3.connect(":memory:", isolation_level=None)) as historical:
                initialize_schema(historical, default_migrations_directory(), target_version=version)
                image = historical.serialize()
                snapshots.validate_profile_snapshot(image)
                with patch.object(SQLiteRepository, "initialize") as initialize, self.assertRaises(RepositoryError):
                    SQLiteRepository.from_snapshot(image)
                initialize.assert_not_called()
                self.assertEqual(historical.serialize(), image)

    def test_invalid_types_and_headers_fail_before_owned_sqlite_open(self) -> None:
        for image in (None, "fictional string", bytearray(self.snapshot), memoryview(self.snapshot), b"", b"fictional not SQLite"):
            with self.subTest(type=type(image).__name__), patch.object(repository_module.sqlite3, "connect") as connect:
                with self.assertRaises(RepositoryError):
                    SQLiteRepository.from_snapshot(image)
                connect.assert_not_called()

    def test_corrupt_forged_and_future_images_fail_before_repository_initialization(self) -> None:
        corrupt = bytearray(self.snapshot)
        corrupt[100] = 255
        images = (
            self.snapshot[:-1], self.snapshot + b"fictional padding", bytes(corrupt),
            self.changed_snapshot("PRAGMA user_version = 8"),
            self.changed_snapshot("CREATE TABLE fictional_unknown (value TEXT)"),
            self.changed_snapshot("UPDATE schema_migrations SET checksum_sha256 = '" + "0" * 64 + "' WHERE version = 7"),
        )
        for index, image in enumerate(images):
            with self.subTest(image=index), patch.object(SQLiteRepository, "initialize") as initialize:
                with self.assertRaises(RepositoryError):
                    SQLiteRepository.from_snapshot(image)
                initialize.assert_not_called()

    def test_exact_schema_validation_precedes_untrusted_ledger_interpretation(self) -> None:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as connection:
            connection.deserialize(self.snapshot)
            connection.execute("DROP TABLE schema_migrations")
            connection.execute("CREATE VIEW schema_migrations AS SELECT fictional_untrusted_function() AS version")
            image = connection.serialize()
        untrusted_ledger_queries = []
        real_connect = sqlite3.connect

        class SchemaProbe(sqlite3.Connection):
            trusted_reference = False

            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                if "CREATE TABLE schema_migrations (" in sql:
                    self.trusted_reference = True
                if "FROM schema_migrations" in sql and not self.trusted_reference:
                    untrusted_ledger_queries.append(sql)
                return super().execute(sql, parameters)

        with patch.object(repository_module.sqlite3, "connect", side_effect=lambda *args, **kwargs: real_connect(
            *args, **kwargs, factory=SchemaProbe,
        )), self.assertRaises(RepositoryError):
            SQLiteRepository.from_snapshot(image)
        self.assertEqual(untrusted_ledger_queries, [])

    def test_validation_is_always_internal_and_failures_have_fixed_content_free_errors(self) -> None:
        private_detail = "fictional-private-profile-path-and-sql"
        for failure in (ValueError(private_detail), KeyboardInterrupt("fictional validation interrupt")):
            with self.subTest(failure=type(failure).__name__), patch.object(
                snapshots, "validate_profile_snapshot", side_effect=failure,
            ) as validate, patch.object(repository_module.sqlite3, "connect") as connect:
                expected = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else RepositoryError
                with self.assertRaises(expected) as raised:
                    SQLiteRepository.from_snapshot(self.snapshot)
                validate.assert_called_once_with(self.snapshot)
                connect.assert_not_called()
                if isinstance(failure, KeyboardInterrupt):
                    self.assertIs(raised.exception, failure)
                else:
                    self.assertNotIn(private_detail, str(raised.exception))
                    self.assertTrue(raised.exception.__suppress_context__)

    def test_owned_setup_deserialization_and_initialization_failures_close_connection(self) -> None:
        private_detail = "fictional-private-constructor-failure"
        for failure in ("row_factory", "defensive", "trusted_schema", "deserialize", "query_only", "initialize"):
            with self.subTest(failure=failure):
                class FaultyConnection(sqlite3.Connection):
                    def __setattr__(self, name: str, value: object) -> None:
                        if failure == "row_factory" and name == "row_factory":
                            raise sqlite3.OperationalError(private_detail)
                        super().__setattr__(name, value)

                    def setconfig(self, op: int, enable: bool = True, /) -> None:
                        if failure == "defensive":
                            raise sqlite3.OperationalError(private_detail)
                        super().setconfig(op, enable)

                    def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                        if (failure == "trusted_schema" and sql == "PRAGMA trusted_schema = OFF") or (
                            failure == "query_only" and sql == "PRAGMA query_only = ON"
                        ):
                            raise sqlite3.OperationalError(private_detail)
                        return super().execute(sql, parameters)

                    def deserialize(self, data: bytes, *, name: str = "main") -> None:
                        if failure == "deserialize":
                            raise sqlite3.OperationalError(private_detail)
                        super().deserialize(data, name=name)

                with self.probe_owned_connections(FaultyConnection) as owned:
                    if failure == "initialize":
                        with patch.object(SQLiteRepository, "initialize", side_effect=ValueError(private_detail)), self.assertRaises(RepositoryError) as raised:
                            SQLiteRepository.from_snapshot(self.snapshot)
                    else:
                        with self.assertRaises(RepositoryError) as raised:
                            SQLiteRepository.from_snapshot(self.snapshot)
                self.assertEqual(len(owned), 1)
                self.assert_connections_closed(owned)
                self.assertNotIn(private_detail, str(raised.exception))
                self.assertTrue(raised.exception.__suppress_context__)

    def test_deserialization_interrupt_survives_owned_connection_close_failure(self) -> None:
        interruption = KeyboardInterrupt("fictional deserialize interruption")

        class InterruptedConnection(sqlite3.Connection):
            def deserialize(self, data: bytes, *, name: str = "main") -> None:
                raise interruption

            def close(self) -> None:
                super().close()
                raise sqlite3.OperationalError("fictional cleanup failure")

        with self.probe_owned_connections(InterruptedConnection) as owned, self.assertRaises(KeyboardInterrupt) as raised:
            SQLiteRepository.from_snapshot(self.snapshot)
        self.assertIs(raised.exception, interruption)
        self.assertEqual(len(owned), 1)
        self.assert_connections_closed(owned)

    def test_repository_context_cleanup_preserves_setup_and_body_failures(self) -> None:
        class CloseFailure(sqlite3.Connection):
            def close(self) -> None:
                super().close()
                raise sqlite3.OperationalError("fictional context cleanup failure")

        for phase in ("enter", "body"):
            for original in (ValueError("fictional context failure"), KeyboardInterrupt("fictional context interruption")):
                with self.subTest(phase=phase, error=type(original).__name__):
                    with self.probe_owned_connections(CloseFailure) as owned:
                        repository = SQLiteRepository.from_snapshot(self.snapshot)
                    with self.assertRaises(type(original)) as raised:
                        if phase == "enter":
                            with patch.object(repository, "initialize", side_effect=original):
                                with repository:
                                    self.fail("Failed initialization entered repository context")
                        else:
                            with repository:
                                raise original
                    self.assertIs(raised.exception, original)
                    self.assert_connections_closed(owned)
                    repository.close()

    def test_constructor_defenses_precede_deserialization_and_deadline_includes_validation(self) -> None:
        now = [0.0]
        observed = []
        real_validate = snapshots.validate_profile_snapshot
        real_connect = sqlite3.connect
        owned = []
        validated = [False]

        def slow_validation(image: bytes) -> None:
            real_validate(image)
            now[0] = SNAPSHOT_WORK_SECONDS * 0.75
            validated[0] = True

        class SlowDeserialization(sqlite3.Connection):
            def deserialize(self, data: bytes, *, name: str = "main") -> None:
                observed.append((self.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE), self.execute("PRAGMA trusted_schema").fetchone()[0]))
                super().deserialize(data, name=name)
                now[0] += SNAPSHOT_WORK_SECONDS * 0.5

        def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs, factory=SlowDeserialization if validated[0] else sqlite3.Connection)
            if validated[0]:
                owned.append(connection)
            return connection

        with patch.object(snapshots, "validate_profile_snapshot", side_effect=slow_validation), patch.object(
            repository_module.sqlite3, "connect", side_effect=tracked_connect,
        ), patch.object(repository_module.time, "monotonic", side_effect=lambda: now[0]), self.assertRaises(RepositoryError):
            SQLiteRepository.from_snapshot(self.snapshot)
        self.assertEqual(observed, [(True, 0)])
        self.assert_connections_closed(owned)

    def test_successful_construction_does_not_leave_a_deadline_on_future_reads(self) -> None:
        repository = self.repository()
        with patch.object(repository_module.time, "monotonic", return_value=10**12):
            with repository.read_transaction():
                self.assertEqual(repository.list_claims(), self.claims)
                # Enough SQLite work to trigger a mistakenly retained progress
                # handler, without requiring wall-clock sleeps or large data.
                total = repository._connection.execute("""WITH RECURSIVE fictional_numbers(value) AS (
                    SELECT 1 UNION ALL SELECT value + 1 FROM fictional_numbers WHERE value < 10000
                ) SELECT sum(value) FROM fictional_numbers""").fetchone()[0]
                self.assertEqual(total, 50005000)
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(repository.get_material_version(self.material_id), self.material)

    def test_interruption_immediately_after_read_begin_rolls_back_and_keeps_repository_usable(self) -> None:
        interruption = KeyboardInterrupt("fictional interruption after read BEGIN")

        class BeginInterrupt(sqlite3.Connection):
            interrupt_begin = False

            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                result = super().execute(sql, parameters)
                if self.interrupt_begin and sql == "BEGIN":
                    raise interruption
                return result

        with self.probe_owned_connections(BeginInterrupt):
            repository = self.repository()
        repository._connection.interrupt_begin = True
        with self.assertRaises(KeyboardInterrupt) as raised:
            with repository.read_transaction():
                self.fail("Interrupted BEGIN entered the read body")
        self.assertIs(raised.exception, interruption)
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(repository.list_claims(), self.claims)

    def test_failed_rollback_closes_owned_connection_without_masking_body_interrupt(self) -> None:
        interruption = KeyboardInterrupt("fictional read-body interruption")

        class RollbackFailure(sqlite3.Connection):
            fail_rollback = False

            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                if self.fail_rollback and sql == "ROLLBACK":
                    raise sqlite3.OperationalError("fictional rollback failure")
                return super().execute(sql, parameters)

        with self.probe_owned_connections(RollbackFailure):
            repository = self.repository()
        connection = repository._connection
        connection.fail_rollback = True
        with self.assertRaises(KeyboardInterrupt) as raised:
            with repository.read_transaction():
                raise interruption
        self.assertIs(raised.exception, interruption)
        self.assert_connections_closed([connection])
        with self.assertRaises(RepositoryClosedError):
            repository.list_claims()

    def test_explicit_close_still_closes_connection_when_rollback_fails(self) -> None:
        class RollbackFailure(sqlite3.Connection):
            fail_rollback = False

            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                if self.fail_rollback and sql == "ROLLBACK":
                    raise sqlite3.OperationalError("fictional close rollback failure")
                return super().execute(sql, parameters)

        with self.probe_owned_connections(RollbackFailure):
            repository = self.repository()
        connection = repository._connection
        connection.execute("BEGIN")
        connection.fail_rollback = True
        with self.assertRaises((RepositoryError, sqlite3.Error)):
            repository.close()
        self.assert_connections_closed([connection])
        repository.close()
        with self.assertRaises(RepositoryClosedError):
            repository.list_claims()

    def test_rollback_interruption_after_successful_read_still_closes_repository(self) -> None:
        interruption = KeyboardInterrupt("fictional interrupted rollback")

        class RollbackInterrupt(sqlite3.Connection):
            interrupt_rollback = False

            def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
                if self.interrupt_rollback and sql == "ROLLBACK":
                    raise interruption
                return super().execute(sql, parameters)

        with self.probe_owned_connections(RollbackInterrupt):
            repository = self.repository()
        connection = repository._connection
        connection.interrupt_rollback = True
        with self.assertRaises(KeyboardInterrupt) as raised:
            with repository.read_transaction():
                self.assertEqual(repository.list_claims(), self.claims)
        self.assertIs(raised.exception, interruption)
        self.assert_connections_closed([connection])
        with self.assertRaises(RepositoryClosedError):
            repository.list_claims()


if __name__ == "__main__":
    unittest.main()
