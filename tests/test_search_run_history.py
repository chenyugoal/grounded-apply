from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager, nullcontext
from datetime import datetime
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.materials import MaterialBlocked
from grounded_apply.services.searches import SearchIntegrityError
from grounded_apply.services.workflow import canonical, digest
from tests import test_search_scope_history, test_searches


ERROR = "Search run failed integrity checks"


class SearchRunHistoryTests(unittest.TestCase):
    setUp = test_search_scope_history.SearchScopeHistoryTests.setUp
    factory = test_searches.SearchTests.factory
    configure = test_searches.SearchTests.configure
    duplicate = test_search_scope_history.SearchScopeHistoryTests.duplicate

    def manifest(self, **changes):
        return test_searches.SearchTests.manifest(self, sources=[{"id": "fictional-manual", "provider": "manual",
            "careers_url": "https://example.com/careers"}], **changes)

    def completed(self, *, version: int = 1, key: str = "fictional-run") -> tuple[str, str]:
        search_id = self.configure(self.manifest(schema_version=version), key=f"fictional-scope-v{version}")
        result = self.service.run(search_id, idempotency_key=key)
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["requests_used"], 0)
        self.assertIsNone(result["batch_id"])
        return search_id, result["run_id"]

    def records(self, run_id: str) -> tuple[dict, dict]:
        with self.factory(True) as repository:
            record = repository.get_search_run(run_id)
            return record, repository.get_workflow_run(record["workflow_run_id"])

    def workflow_patch(self, repository: SQLiteRepository, workflow: dict, value: object):
        original = repository.get_workflow_run
        def read(identifier):
            if identifier != workflow["id"]:
                return original(identifier)
            if isinstance(value, BaseException):
                raise value
            return value
        return patch.object(repository, "get_workflow_run", side_effect=read)

    def persist_workflow(self, run_id: str, fields: dict) -> None:
        with self.factory(False) as repository, repository.transaction():
            record = repository.get_search_run(run_id)
            workflow = repository.get_workflow_run(record["workflow_run_id"])
            self.assertTrue(set(fields) <= set(workflow) - {"id"})
            repository._connection.execute("UPDATE workflow_runs SET " + ", ".join(f"{key} = ?" for key in fields)
                + " WHERE id = ?", (*fields.values(), workflow["id"]))

    def assert_rejected(self, repository: SQLiteRepository, run_id: str) -> None:
        before = repository._connection.serialize()
        with self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
            self.service._validated(repository, run_id)
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(repository._connection.serialize(), before)

    def test_v1_v2_parent_scopes_keep_valid_completed_run_origins_and_replay(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version):
                scope, run_id = self.completed(version=version)
                record, workflow = self.records(run_id)
                payload = json.loads(workflow["input_json"])
                self.assertIs(type(payload["version"]), int)
                self.assertEqual(payload["version"], 1)
                self.assertEqual(payload["search_id"], scope)
                before = self.database.read_bytes()
                result = self.service.get(run_id)
                self.assertEqual(result["run_id"], run_id)
                self.assertEqual(self.database.read_bytes(), before)
                self.assertEqual(self.service.run(scope, idempotency_key="fictional-run")["run_id"], run_id)
                self.assertEqual(self.records(run_id), (record, workflow))
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.renderer.built, [])

    def test_interrupted_legacy_and_rotated_runs_keep_original_checkpoint_prefix_on_resume(self) -> None:
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                scope = self.configure(key=f"fictional-interrupted-scope-{legacy}")
                reserve = patch.object(self.service, "_reserve_source_order", return_value=None) if legacy else nullcontext()
                sentinel = KeyboardInterrupt("fictional interruption after manual source started")
                with reserve, patch.object(self.service, "_capture_source", side_effect=sentinel), self.assertRaises(KeyboardInterrupt) as raised:
                    self.service.run(scope, idempotency_key="fictional-interrupted")
                self.assertIs(raised.exception, sentinel)
                with self.factory(True) as repository:
                    run_id = repository.list_search_runs(scope)[0]["id"]
                    prefix = repository.list_search_events(run_id)
                report = self.service.get(run_id)
                self.assertEqual("source_order" in report, not legacy)
                self.assertEqual(report["sources"][0]["stage"], "fetching")
                resumed = self.service.resume(run_id)
                self.assertEqual(resumed["phase"], "complete")
                self.assertEqual("source_order" in resumed, not legacy)
                with self.factory(True) as repository:
                    self.assertEqual(repository.list_search_events(run_id)[:len(prefix)], prefix)
        self.assertEqual(self.transport.calls, [])

    def test_persisted_boolean_payload_and_model_metadata_fail_after_exact_snapshot_validation(self) -> None:
        for defect in ("boolean", "model", "combined"):
            _, run_id = self.completed(key=f"fictional-persisted-{defect}")
            _, workflow = self.records(run_id)
            fields = {}
            if defect in {"boolean", "combined"}:
                payload = json.loads(workflow["input_json"])
                payload["version"] = True
                fields["input_json"] = canonical(payload)
            if defect in {"model", "combined"}:
                fields["model_name"] = "fictional-unrecorded-model"
            self.persist_workflow(run_id, fields)
            image = self.database.read_bytes()
            with SQLiteRepository.from_snapshot(image) as repository:
                self.assert_rejected(repository, run_id)
            self.assertEqual(self.database.read_bytes(), image)
            # Keep later cases independent: rotation validates earlier origins.
            self.persist_workflow(run_id, {key: workflow[key] for key in fields})

    def test_corrupt_origin_blocks_get_list_replay_and_resume_before_writes_or_requests(self) -> None:
        scope, run_id = self.completed()
        self.persist_workflow(run_id, {"model_name": "fictional-unrecorded-model"})
        before = self.database.read_bytes()
        calls = (lambda: self.service.get(run_id), lambda: self.service.list(scope),
            lambda: self.service.run(scope, idempotency_key="fictional-run"), lambda: self.service.resume(run_id))
        for index, call in enumerate(calls):
            with self.subTest(interface=index), self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
                call()
            self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.transport.calls, [])

    def test_duplicate_nonfinite_and_deep_workflow_json_are_rejected(self) -> None:
        _, run_id = self.completed()
        _, workflow = self.records(run_id)
        variants = [{**workflow, "input_json": self.duplicate(workflow["input_json"], key)}
            for key in ("version", "search_id", "manifest_sha256")]
        variants += [{**workflow, "retry_policy_json": '{"x":{"n":1,"n":1}}'},
            {**workflow, "retry_policy_json": '{"x":NaN}'}, {**workflow, "input_json": '{"version":1e10000}'},
            {**workflow, "input_json": "[" * 2000 + "]" * 2000}]
        for index, row in enumerate(variants):
            with self.subTest(variant=index), self.factory(True) as repository, self.workflow_patch(repository, workflow, row):
                self.assert_rejected(repository, run_id)

    def test_payload_requires_literal_one_and_exact_closed_field_set(self) -> None:
        _, run_id = self.completed()
        _, workflow = self.records(run_id)
        payload = json.loads(workflow["input_json"])
        variants = [{**payload, "version": value} for value in (True, 1.0, 2)]
        variants += [{**payload, "extra": None}, {key: value for key, value in payload.items() if key != "search_id"}]
        for index, value in enumerate(variants):
            with self.subTest(variant=index), self.factory(True) as repository, \
                 self.workflow_patch(repository, workflow, {**workflow, "input_json": canonical(value)}):
                self.assert_rejected(repository, run_id)

    def test_closed_run_and_workflow_rows_bind_returned_requested_and_parent_ids(self) -> None:
        _, run_id = self.completed()
        other_scope = self.configure(self.manifest(max_jobs=1), key="fictional-other-scope")
        record, workflow = self.records(run_id)
        rows = [{**record, "id": "fictional-other-run"}, {**record, "search_id": other_scope},
            {**record, "extra": None}, {key: value for key, value in record.items() if key != "created_at"}]
        for index, row in enumerate(rows):
            with self.subTest(run_row=index), self.factory(True) as repository, patch.object(repository, "get_search_run", return_value=row):
                self.assert_rejected(repository, run_id)
        rows = [{**workflow, "id": "fictional-other-workflow"}, {**workflow, "extra": None},
            {key: value for key, value in workflow.items() if key != "model_name"}]
        for index, row in enumerate(rows):
            with self.subTest(workflow_row=index), self.factory(True) as repository, self.workflow_patch(repository, workflow, row):
                self.assert_rejected(repository, run_id)

    def test_rehashed_input_cannot_change_manifest_or_deterministic_run_identity(self) -> None:
        _, run_id = self.completed()
        _, workflow = self.records(run_id)
        payload = json.loads(workflow["input_json"])
        variants = []
        for field in ("manifest_sha256", "idempotency_sha256"):
            value = {**payload, field: "0" * 64}
            row = {**workflow, "input_json": canonical(value), "input_hash_sha256": digest(value)}
            if field == "idempotency_sha256":
                row["idempotency_key"] = value[field]
            variants.append(row)
        variants += [{**workflow, "input_hash_sha256": "0" * 64},
            {**workflow, "generated_artifacts_json": '["fictional-other-run"]'},
            {**workflow, "generated_artifacts_json": json.dumps([run_id, run_id])}]
        for index, row in enumerate(variants):
            with self.subTest(binding=index), self.factory(True) as repository, self.workflow_patch(repository, workflow, row):
                self.assert_rejected(repository, run_id)

    def test_completed_metadata_and_raw_clocks_reject_unsupported_or_earlier_values(self) -> None:
        _, run_id = self.completed()
        record, workflow = self.records(run_id)
        changes = [(field, "fictional-unsupported") for field in ("model_name", "prompt_version", "failure_code", "failure_reason")]
        changes += [("outstanding_need_info_json", '[{"reason":"fictional"}]'), ("retry_policy_json", '{"attempts":1}'),
            ("started_at", None), ("started_at", "2029-12-31T19:00:00-05:00"),
            ("finished_at", "2030-01-01T00:00:00+00:00"), ("created_at", "2030-01-01T00:00:00+00:00"),
            ("updated_at", "malformed"), ("updated_at", "2030-01-01T00:00:00"), ("updated_at", "2030-01-01T00:30:00+01:00")]
        for field, value in changes:
            with self.subTest(field=field, value=value), self.factory(True) as repository, \
                 self.workflow_patch(repository, workflow, {**workflow, field: value}):
                self.assert_rejected(repository, run_id)
        for created in ("2030-01-01T00:00:00+00:00", "2030-01-01T01:00:00.000000+01:00"):
            with self.subTest(run_created=created), self.factory(True) as repository, \
                 patch.object(repository, "get_search_run", return_value={**record, "created_at": created}):
                self.assert_rejected(repository, run_id)

    def test_valid_formatting_and_equal_offset_or_future_updates_keep_read_only_identity(self) -> None:
        _, run_id = self.completed(version=2)
        record, workflow = self.records(run_id)
        self.persist_workflow(run_id, {"input_json": json.dumps(json.loads(workflow["input_json"]), indent=2).replace("search_id", "\\u0073earch_id"),
            "completed_steps_json": '[ "validate", "persist" ]', "generated_artifacts_json": json.dumps([run_id], indent=2)})
        for updated in ("2030-01-01T01:00:00+01:00", "2031-01-01T00:00:00+00:00"):
            self.assertGreaterEqual(datetime.fromisoformat(updated), datetime.fromisoformat(record["created_at"]))
            self.persist_workflow(run_id, {"updated_at": updated})
            before = (self.database.read_bytes(), self.database.stat().st_mtime_ns,
                sorted(path.name for path in self.database.parent.iterdir()))
            self.assertEqual(self.service.get(run_id)["run_id"], run_id)
            with SQLiteRepository.from_snapshot(before[0]) as repository:
                self.assertEqual(self.service._validated(repository, run_id)[0], record)
            self.assertEqual((self.database.read_bytes(), self.database.stat().st_mtime_ns,
                sorted(path.name for path in self.database.parent.iterdir())), before)

    def test_missing_run_parent_and_caller_errors_keep_existing_contracts(self) -> None:
        scope, run_id = self.completed()
        with self.factory(True) as repository:
            with self.assertRaisesRegex(ValueError, "^Search run does not exist$"):
                self.service._validated(repository, "fictional-missing")
            with patch.object(repository, "get_search_run", side_effect=AssertionError("Invalid ID must not query")):
                for identifier in (None, True, "invalid/run"):
                    with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                        self.service._validated(repository, identifier)
            with patch.object(repository, "get_saved_search", return_value=None), \
                 self.assertRaisesRegex(ValueError, "^Saved search does not exist$"):
                self.service._validated(repository, run_id)
            parent = repository.get_saved_search(scope)
            with patch.object(repository, "get_saved_search", return_value={**parent, "extra": None}), \
                 self.assertRaisesRegex(SearchIntegrityError, "^Saved search failed integrity checks$"):
                self.service._validated(repository, run_id)
            blocker = MaterialBlocked([])
            with patch.object(self.service, "_scope", side_effect=blocker), self.assertRaises(MaterialBlocked) as raised:
                self.service._validated(repository, run_id)
            self.assertIs(raised.exception, blocker)

    def test_one_owned_snapshot_and_borrowed_caller_work_survive_origin_failure(self) -> None:
        _, run_id = self.completed()
        _, workflow = self.records(run_id)
        with self.factory(False) as repository:
            trace = []
            repository._connection.set_trace_callback(trace.append)
            self.service._validated(repository, run_id)
            self.assertEqual([sql for sql in trace if sql in {"BEGIN", "ROLLBACK"}], ["BEGIN", "ROLLBACK"])
            before = repository._connection.serialize()
            sentinel = RuntimeError("fictional caller rollback")
            with self.assertRaises(RuntimeError) as raised:
                with repository.transaction():
                    marker = repository.add_workflow_run(workflow_type="fictional-caller", status="queued")
                    self.service._validated(repository, run_id)
                    with self.workflow_patch(repository, workflow, {**workflow, "model_name": "fictional-corrupt"}), \
                         self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
                        self.service._validated(repository, run_id)
                    self.assertTrue(repository._connection.in_transaction)
                    self.assertIsNotNone(repository.get_workflow_run(marker["id"]))
                    raise sentinel
            self.assertIs(raised.exception, sentinel)
            self.assertEqual(repository._connection.serialize(), before)

    def test_post_write_run_origin_corruption_rolls_back_run_workflow_and_initial_event(self) -> None:
        from grounded_apply.services import searches
        scope = self.configure()
        finish = searches.finish_workflow
        def corrupt(repository, workflow_id, artifacts, at):
            finish(repository, workflow_id, artifacts, at)
            repository._connection.execute("UPDATE workflow_runs SET model_name = ? WHERE id = ?", ("fictional-corrupt", workflow_id))
        before = self.database.read_bytes()
        with patch.object(searches, "finish_workflow", side_effect=corrupt), self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
            self.service.run(scope, idempotency_key="fictional-never-committed")
        self.assertEqual(self.database.read_bytes(), before)
        with self.factory(True) as repository:
            self.assertEqual(repository.list_search_runs(scope), [])

    def test_expected_failures_are_fixed_and_unexpected_errors_or_interrupts_keep_identity(self) -> None:
        _, run_id = self.completed()
        _, workflow = self.records(run_id)
        for mode in ("entry", "exit"):
            with self.subTest(transaction=mode), self.factory(True) as repository:
                original = repository.read_transaction
                depth = 0
                @contextmanager
                def boundary_failure():
                    nonlocal depth
                    outer = depth == 0
                    depth += 1
                    try:
                        if outer and mode == "entry":
                            raise sqlite3.OperationalError("fictional private transaction entry")
                        with original():
                            yield
                        if outer and mode == "exit":
                            raise sqlite3.OperationalError("fictional private transaction exit")
                    finally:
                        depth -= 1
                with patch.object(repository, "read_transaction", side_effect=boundary_failure):
                    self.assert_rejected(repository, run_id)
        for method in ("get_search_run", "get_workflow_run"):
            with self.subTest(storage=method), self.factory(True) as repository:
                error = sqlite3.OperationalError("fictional private SQL detail")
                failure = (patch.object(repository, method, side_effect=error) if method == "get_search_run"
                    else self.workflow_patch(repository, workflow, error))
                with failure:
                    self.assert_rejected(repository, run_id)
        for error in (RuntimeError("fictional programmer error"), KeyboardInterrupt("fictional read interruption")):
            with self.subTest(error=type(error).__name__), self.factory(True) as repository:
                before = repository._connection.serialize()
                with self.workflow_patch(repository, workflow, error), self.assertRaises(type(error)) as raised:
                    self.service._validated(repository, run_id)
                self.assertIs(raised.exception, error)
                self.assertFalse(repository._connection.in_transaction)
                self.assertEqual(repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
