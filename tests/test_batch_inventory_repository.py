from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from grounded_apply.repositories import RepositoryClosedError, RepositoryNotInitializedError, SQLiteRepository
from tests import test_batch_search_history


class _FailingInventoryConnection:
    def __init__(self, connection: sqlite3.Connection, failure: BaseException) -> None:
        self.connection, self.failure = connection, failure

    def execute(self, sql: str, *arguments: Any) -> sqlite3.Cursor:
        if sql.startswith("SELECT id, item_id FROM preparation_batch_events"):
            raise self.failure
        return self.connection.execute(sql, *arguments)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)


class BatchInventoryRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = test_batch_search_history.BatchSearchHistoryTests()
        fixture.setUp()
        try:
            batch_id, _ = fixture.create(2, partial=True)
            manifest = json.loads(fixture.repository.get_preparation_batch(batch_id)["manifest_json"])
            fixture.service.create(manifest, idempotency_key="fictional-pending-inventory-batch")
            # Orphan workflows belong in the inventory regardless of status.
            fixture.repository.add_workflow_run(workflow_type="batch_create",
                run_id="fictional-orphan-batch-inventory", status="queued")
            fixture.repository.add_workflow_run(workflow_type="batch_create_extra",
                run_id="fictional-unrelated-batch-inventory", input_data={"fictional_marker": "not inventory metadata"})
            batches = fixture.repository.list_preparation_batches()
            items = [item for row in batches for item in fixture.repository.list_preparation_items(row["id"])]
            events = [event for row in items for event in fixture.repository.list_preparation_events(row["id"])]
            leases = [fixture.repository.get_preparation_lease(row["id"]) for row in batches]
            workflows = [row for row in fixture.repository.list_workflow_runs() if row["workflow_type"] == "batch_create"]
            cls.expected = {
                "batches": tuple({key: row[key] for key in ("id", "workflow_run_id")}
                                 for row in sorted(batches, key=lambda row: row["id"])),
                "items": tuple({key: row[key] for key in ("id", "batch_id")}
                               for row in sorted(items, key=lambda row: row["id"])),
                "events": tuple({key: row[key] for key in ("id", "item_id")}
                                for row in sorted(events, key=lambda row: row["id"])),
                "leases": tuple({"batch_id": row["batch_id"]} for row in sorted(leases, key=lambda row: row["batch_id"])),
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
            repository.batch_history_inventory()
        repository.initialize()
        self.assertEqual(repository.batch_history_inventory(),
            {"batches": (), "items": (), "events": (), "leases": (), "workflows": ()})
        repository.close()
        with self.assertRaises(RepositoryClosedError):
            repository.batch_history_inventory()

    def test_inventory_is_ordered_metadata_including_unowned_batch_workflows(self) -> None:
        repository = self.repository()
        result = repository.batch_history_inventory()
        self.assertEqual(result, self.expected)
        self.assertEqual(len(result["batches"]), 2)
        self.assertEqual(len(result["items"]), 4)
        self.assertEqual(len(result["leases"]), 2)
        self.assertTrue(all(type(rows) is tuple and all(type(row) is dict for row in rows)
                            for rows in result.values()))
        self.assertIn({"id": "fictional-orphan-batch-inventory", "workflow_type": "batch_create"}, result["workflows"])
        self.assertNotIn("fictional-unrelated-batch-inventory", [row["id"] for row in result["workflows"]])
        result["events"][0]["id"] = "fictional-changed-result"
        self.assertEqual(repository.batch_history_inventory(), self.expected)
        self.assertEqual(repository._connection.serialize(), self.snapshot)

    def test_all_five_reads_share_one_snapshot_and_cannot_read_private_columns(self) -> None:
        repository = self.repository()
        allowed = {
            "preparation_batches": {"id", "workflow_run_id"},
            "preparation_batch_items": {"id", "batch_id"},
            "preparation_batch_events": {"id", "item_id"},
            "preparation_batch_leases": {"batch_id"},
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
                self.assertEqual(repository.batch_history_inventory(), self.expected)
        finally:
            repository._connection.set_authorizer(None)
            repository._connection.set_trace_callback(None)
        self.assertEqual(reads, [True] * 5)
        self.assertEqual(sum(statement == "BEGIN" for statement in statements), 1)
        self.assertEqual(sum(statement == "ROLLBACK" for statement in statements), 1)
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(repository._connection.serialize(), self.snapshot)

    def test_read_only_file_inventory_preserves_bytes_mtime_and_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gapply-fictional-batch-inventory-") as directory:
            path = Path(directory) / "fictional.db"
            path.write_bytes(self.snapshot)
            path.chmod(0o600)
            before = path.read_bytes(), path.stat().st_mtime_ns, tuple(sorted(Path(directory).iterdir()))
            with SQLiteRepository(path, existing_only=True, read_only=True).initialize() as repository:
                self.assertEqual(repository.batch_history_inventory(), self.expected)
                self.assertFalse(repository._connection.in_transaction)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, tuple(sorted(Path(directory).iterdir()))), before)

    def test_owned_failures_and_interruptions_roll_back_without_hiding_the_original(self) -> None:
        repository = self.repository()
        connection = repository._connection
        for failure in (sqlite3.OperationalError("fictional interrupted read"), KeyboardInterrupt("fictional cancellation")):
            with self.subTest(failure=type(failure).__name__), patch.object(repository, "_connection",
                _FailingInventoryConnection(connection, failure)):
                with self.assertRaises(type(failure)) as raised:
                    repository.batch_history_inventory()
                self.assertIs(raised.exception, failure)
                self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.serialize(), self.snapshot)

    def test_borrowed_transaction_success_and_failure_leave_caller_work_intact(self) -> None:
        with SQLiteRepository(":memory:").initialize() as repository:
            before = repository._connection.serialize()
            abort = RuntimeError("fictional caller rollback")
            with self.assertRaises(RuntimeError) as raised:
                with repository.transaction():
                    repository.add_workflow_run(workflow_type="batch_create", run_id="fictional-caller-work")
                    statements = []
                    repository._connection.set_trace_callback(statements.append)
                    try:
                        inventory = repository.batch_history_inventory()
                    finally:
                        repository._connection.set_trace_callback(None)
                    self.assertEqual(inventory["workflows"], ({"id": "fictional-caller-work", "workflow_type": "batch_create"},))
                    self.assertFalse(any(statement in {"BEGIN", "ROLLBACK", "COMMIT"} for statement in statements))
                    failure = sqlite3.OperationalError("fictional borrowed read failure")
                    with patch.object(repository, "_connection", _FailingInventoryConnection(repository._connection, failure)):
                        with self.assertRaises(sqlite3.OperationalError) as caught:
                            repository.batch_history_inventory()
                        self.assertIs(caught.exception, failure)
                    self.assertTrue(repository._connection.in_transaction)
                    self.assertIsNotNone(repository.get_workflow_run("fictional-caller-work"))
                    raise abort
            self.assertIs(raised.exception, abort)
            self.assertIsNone(repository.get_workflow_run("fictional-caller-work"))
            self.assertEqual(repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
