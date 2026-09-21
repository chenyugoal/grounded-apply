from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from grounded_apply.repositories import RepositoryClosedError, RepositoryNotInitializedError, SQLiteRepository
from tests import test_application_material_history


class _FailingInventoryConnection:
    def __init__(self, connection: sqlite3.Connection, failure: BaseException) -> None:
        self.connection, self.failure = connection, failure

    def execute(self, sql: str, *arguments: Any) -> sqlite3.Cursor:
        if sql.startswith("SELECT id, application_id, workflow_run_id"):
            raise self.failure
        return self.connection.execute(sql, *arguments)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)


class ApplicationInventoryRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = test_application_material_history.ApplicationMaterialHistoryTests()
        fixture.setUp()
        try:
            material_id = fixture.build()
            fixture.approve(material_id)
            fixture.history(material_id)
            fixture.preparing()
            # Inventory must expose an orphan even when its workflow is queued,
            # and leave every unrelated workflow kind outside this component.
            fixture.repository.add_workflow_run(workflow_type="application_transition",
                run_id="fictional-orphan-inventory", status="queued")
            fixture.repository.add_workflow_run(workflow_type="application_transition_extra",
                run_id="fictional-unrelated-inventory", input_data={"fictional_marker": "not inventory metadata"})
            applications = fixture.repository.list_applications()
            events = [event for row in applications for event in fixture.repository.list_application_events(row["id"])]
            submissions = [record for row in applications
                           if (record := fixture.repository.get_submission_snapshot(row["id"])) is not None]
            workflows = [row for row in fixture.repository.list_workflow_runs()
                         if row["workflow_type"] in {"application_create", "application_transition"}]
            cls.expected = {
                "applications": tuple({"id": row["id"]} for row in sorted(applications, key=lambda row: row["id"])),
                "events": tuple({key: row[key] for key in ("id", "application_id", "workflow_run_id")}
                                for row in sorted(events, key=lambda row: row["id"])),
                "submissions": tuple({key: row[key] for key in ("application_id", "event_id", "material_id")}
                                     for row in sorted(submissions, key=lambda row: row["application_id"])),
                "workflows": tuple({key: row[key] for key in ("id", "workflow_type")}
                                   for row in sorted(workflows, key=lambda row: row["id"])),
            }
            cls.snapshot = fixture.repository._connection.serialize()
        finally:
            fixture.doCleanups()

    def repository(self) -> SQLiteRepository:
        repository = SQLiteRepository.from_snapshot(self.snapshot)
        self.addCleanup(repository.close)
        return repository

    def test_requires_initialized_open_repository_and_empty_inventory_has_all_keys(self) -> None:
        repository = SQLiteRepository(":memory:")
        self.addCleanup(repository.close)
        with self.assertRaises(RepositoryNotInitializedError):
            repository.application_history_inventory()
        repository.initialize()
        self.assertEqual(repository.application_history_inventory(),
            {"applications": (), "events": (), "submissions": (), "workflows": ()})
        repository.close()
        with self.assertRaises(RepositoryClosedError):
            repository.application_history_inventory()

    def test_inventory_is_ordered_metadata_including_unowned_application_workflows(self) -> None:
        repository = self.repository()
        result = repository.application_history_inventory()
        self.assertEqual(result, self.expected)
        self.assertEqual(len(result["applications"]), 2)
        self.assertEqual(len(result["submissions"]), 1)
        self.assertTrue(all(type(rows) is tuple and all(type(row) is dict for row in rows)
                            for rows in result.values()))
        self.assertIn({"id": "fictional-orphan-inventory", "workflow_type": "application_transition"}, result["workflows"])
        self.assertNotIn("fictional-unrelated-inventory", [row["id"] for row in result["workflows"]])
        result["events"][0]["id"] = "fictional-changed-result"
        self.assertEqual(repository.application_history_inventory(), self.expected)
        self.assertEqual(repository._connection.serialize(), self.snapshot)

    def test_all_four_reads_share_one_snapshot_and_cannot_read_private_columns(self) -> None:
        repository = self.repository()
        allowed = {
            "applications": {"id"},
            "application_events": {"id", "application_id", "workflow_run_id"},
            "submission_snapshots": {"application_id", "event_id", "material_id"},
            "workflow_runs": {"id", "workflow_type"},
        }
        statements, reads = [], []

        def authorize(action: int, table: str | None, column: str | None, database: str | None, source: str | None) -> int:
            if action == sqlite3.SQLITE_READ and column not in allowed.get(table, set()):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def trace(statement: str) -> None:
            statements.append(statement)
            if statement.startswith("SELECT"):
                reads.append(repository._connection.in_transaction)

        output, errors = io.StringIO(), io.StringIO()
        repository._connection.set_authorizer(authorize)
        repository._connection.set_trace_callback(trace)
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(repository.application_history_inventory(), self.expected)
        finally:
            repository._connection.set_authorizer(None)
            repository._connection.set_trace_callback(None)
        self.assertEqual(reads, [True] * 4)
        self.assertEqual(sum(statement == "BEGIN" for statement in statements), 1)
        self.assertEqual(sum(statement == "ROLLBACK" for statement in statements), 1)
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(repository._connection.serialize(), self.snapshot)

    def test_read_only_file_inventory_preserves_bytes_mtime_and_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gapply-fictional-inventory-") as directory:
            path = Path(directory) / "fictional.db"
            path.write_bytes(self.snapshot)
            path.chmod(0o600)
            before = path.read_bytes(), path.stat().st_mtime_ns, tuple(sorted(Path(directory).iterdir()))
            with SQLiteRepository(path, existing_only=True, read_only=True).initialize() as repository:
                self.assertEqual(repository.application_history_inventory(), self.expected)
                self.assertFalse(repository._connection.in_transaction)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, tuple(sorted(Path(directory).iterdir()))), before)

    def test_owned_failures_and_interruptions_roll_back_without_hiding_the_original(self) -> None:
        repository = self.repository()
        connection = repository._connection
        for failure in (sqlite3.OperationalError("fictional interrupted read"), KeyboardInterrupt("fictional cancellation")):
            with self.subTest(failure=type(failure).__name__), patch.object(repository, "_connection",
                _FailingInventoryConnection(connection, failure)):
                with self.assertRaises(type(failure)) as raised:
                    repository.application_history_inventory()
                self.assertIs(raised.exception, failure)
                self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.serialize(), self.snapshot)

    def test_borrowed_transaction_success_and_failure_leave_caller_work_intact(self) -> None:
        with SQLiteRepository(":memory:").initialize() as repository:
            before = repository._connection.serialize()
            abort = RuntimeError("fictional caller rollback")
            with self.assertRaises(RuntimeError) as raised:
                with repository.transaction():
                    repository.add_workflow_run(workflow_type="application_create", run_id="fictional-caller-work")
                    statements = []
                    repository._connection.set_trace_callback(statements.append)
                    try:
                        inventory = repository.application_history_inventory()
                    finally:
                        repository._connection.set_trace_callback(None)
                    self.assertEqual(inventory["workflows"], ({"id": "fictional-caller-work", "workflow_type": "application_create"},))
                    self.assertFalse(any(statement in {"BEGIN", "ROLLBACK", "COMMIT"} for statement in statements))
                    failure = sqlite3.OperationalError("fictional borrowed read failure")
                    with patch.object(repository, "_connection", _FailingInventoryConnection(repository._connection, failure)):
                        with self.assertRaises(sqlite3.OperationalError) as caught:
                            repository.application_history_inventory()
                        self.assertIs(caught.exception, failure)
                    self.assertTrue(repository._connection.in_transaction)
                    self.assertIsNotNone(repository.get_workflow_run("fictional-caller-work"))
                    raise abort
            self.assertIs(raised.exception, abort)
            self.assertIsNone(repository.get_workflow_run("fictional-caller-work"))
            self.assertEqual(repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
