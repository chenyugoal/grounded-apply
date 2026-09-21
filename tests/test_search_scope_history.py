from __future__ import annotations

import json
import sqlite3
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.searches import SearchIntegrityError, validate_search_manifest
from grounded_apply.services.workflow import canonical, digest
from tests import test_searches


ERROR = "Saved search failed integrity checks"
CREATED = datetime(2030, 1, 1, tzinfo=UTC)


class SearchScopeHistoryTests(unittest.TestCase):
    factory = test_searches.SearchTests.factory
    manifest = test_searches.SearchTests.manifest
    configure = test_searches.SearchTests.configure

    def setUp(self) -> None:
        test_searches.SearchTests.setUp(self)
        self.service._clock = lambda: CREATED
        for target, method in ((self.transport, "get"), (self.renderer, "render"), (self.renderer, "validate"),
                               (ProfileService, "validated_profile")):
            forbidden = patch.object(target, method, side_effect=AssertionError("Scope custody must not fetch or inspect candidate artifacts"))
            forbidden.start()
            self.addCleanup(forbidden.stop)

    def records(self, search_id: str) -> tuple[dict, dict]:
        with self.factory(True) as repository:
            record = repository.get_saved_search(search_id)
            return record, repository.get_workflow_run(record["workflow_run_id"])

    def persist(self, search_id: str, *, scope: dict | None = None, workflow: dict | None = None) -> None:
        """Mutate only this external synthetic fixture and restore exact SQL."""
        with self.factory(False) as repository, repository.transaction():
            record = repository.get_saved_search(search_id)
            connection = repository._connection
            if scope:
                self.assertTrue(set(scope) <= set(record) - {"id"})
                trigger = connection.execute("SELECT sql FROM sqlite_schema WHERE name = 'saved_searches_no_update'").fetchone()[0]
                connection.execute("DROP TRIGGER saved_searches_no_update")
                connection.execute("UPDATE saved_searches SET " + ", ".join(f"{key} = ?" for key in scope)
                    + " WHERE id = ?", (*scope.values(), search_id))
                connection.execute(trigger)
                self.assertEqual(connection.execute("SELECT sql FROM sqlite_schema WHERE name = 'saved_searches_no_update'").fetchone()[0], trigger)
            if workflow:
                original = repository.get_workflow_run(record["workflow_run_id"])
                self.assertTrue(set(workflow) <= set(original) - {"id"})
                connection.execute("UPDATE workflow_runs SET " + ", ".join(f"{key} = ?" for key in workflow)
                    + " WHERE id = ?", (*workflow.values(), original["id"]))

    def duplicate(self, raw: str, key: str) -> str:
        return raw.rstrip()[:-1] + "," + canonical(key) + ":" + canonical(json.loads(raw)[key]) + "}"

    def assert_rejected(self, repository: SQLiteRepository, search_id: str) -> None:
        before = repository._connection.serialize()
        with self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
            self.service._scope(repository, search_id)
        self.assertFalse(repository._connection.in_transaction)
        self.assertEqual(repository._connection.serialize(), before)

    def test_v1_v2_configuration_and_replay_preserve_normalized_scope_and_payload_one(self) -> None:
        for version in (1, 2):
            spec = self.manifest(schema_version=version)
            if version == 2:
                spec["preparation_filters"] = {"title_excludes": ["Manager"], "location_contains": ["Zürich"]}
            before = deepcopy(spec)
            search_id = self.configure(spec, key=f"fictional-v{version}")
            self.assertEqual(self.configure(spec, key=f"fictional-v{version}"), search_id)
            record, workflow = self.records(search_id)
            self.assertIs(type(json.loads(workflow["input_json"])["version"]), int)
            self.assertEqual(json.loads(workflow["input_json"])["version"], 1)
            with self.factory(True) as repository:
                saved, normalized = self.service._scope(repository, search_id)
            self.assertEqual(saved, record)
            self.assertEqual(normalized, validate_search_manifest(spec))
            self.assertEqual(spec, before)
        self.assertEqual(len(self.service.list_searches()), 2)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.renderer.built, [])

    def test_persisted_boolean_version_and_model_metadata_fail_after_exact_snapshot_validation(self) -> None:
        for defect in ("boolean", "model", "combined"):
            search_id = self.configure(key=f"fictional-persisted-{defect}")
            _, workflow = self.records(search_id)
            fields = {}
            if defect in {"boolean", "combined"}:
                payload = json.loads(workflow["input_json"])
                payload["version"] = True
                fields["input_json"] = canonical(payload)
            if defect in {"model", "combined"}:
                fields["model_name"] = "fictional-unexpected-model"
            self.persist(search_id, workflow=fields)
            image = self.database.read_bytes()
            with SQLiteRepository.from_snapshot(image) as repository:
                self.assert_rejected(repository, search_id)
            self.assertEqual(self.database.read_bytes(), image)

    def test_corrupt_scope_blocks_public_replay_listing_and_run_before_any_new_work(self) -> None:
        search_id = self.configure()
        self.persist(search_id, workflow={"model_name": "fictional-unexpected-model"})
        before = self.database.read_bytes()
        calls = (lambda: self.configure(), self.service.list_searches,
                 lambda: self.service.list(search_id), lambda: self.service.run(search_id, idempotency_key="fictional-never-started"))
        for index, call in enumerate(calls):
            with self.subTest(interface=index), self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
                call()
            self.assertEqual(self.database.read_bytes(), before)
        with self.factory(True) as repository:
            self.assertEqual(repository.list_search_runs(), [])
        self.assertEqual(self.transport.calls, [])

    def test_recursive_duplicate_keys_in_scope_and_workflow_are_not_normalized_away(self) -> None:
        search_id = self.configure(self.manifest(schema_version=2))
        record, workflow = self.records(search_id)
        manifest = json.loads(record["manifest_json"])
        source, filters = canonical(manifest["sources"][0]), canonical(manifest["preparation_filters"])
        variants = [("scope", self.duplicate(record["manifest_json"], "schema_version")),
            ("scope", canonical(manifest).replace(source, self.duplicate(source, "id"), 1)),
            ("scope", canonical(manifest).replace(filters, self.duplicate(filters, "missing_location"), 1)),
            ("workflow", self.duplicate(workflow["input_json"], "version"))]
        for index, (kind, raw) in enumerate(variants):
            with self.subTest(location=index), self.factory(True) as repository:
                field = "manifest_json" if kind == "scope" else "input_json"
                original = record if kind == "scope" else workflow
                self.assertEqual(json.loads(raw), json.loads(original[field]))
                self.assertNotEqual(raw, original[field])
                method = "get_saved_search" if kind == "scope" else "get_workflow_run"
                with patch.object(repository, method, return_value={**original, field: raw}):
                    self.assert_rejected(repository, search_id)

    def test_nonfinite_deep_json_and_typed_manifest_or_input_aliases_fail_closed(self) -> None:
        search_id = self.configure()
        record, workflow = self.records(search_id)
        variants = []
        for value in (True, 1.0):
            payload = json.loads(workflow["input_json"])
            payload["version"] = value
            variants.append(("get_workflow_run", {**workflow, "input_json": canonical(payload)}))
        for key, value in (("schema_version", True), ("max_jobs", 2.0)):
            manifest = json.loads(record["manifest_json"])
            manifest[key] = value
            variants.append(("get_saved_search", {**record, "manifest_json": canonical(manifest), "manifest_sha256": digest(manifest)}))
        manifest = json.loads(record["manifest_json"])
        manifest["layout"]["schema_version"] = True
        variants.append(("get_saved_search", {**record, "manifest_json": canonical(manifest), "manifest_sha256": digest(manifest)}))
        variants += [("get_workflow_run", {**workflow, "retry_policy_json": '{"x":NaN}'}),
            ("get_workflow_run", {**workflow, "input_json": '{"version":1e10000}'}),
            ("get_saved_search", {**record, "manifest_json": "[" * 2000 + "]" * 2000})]
        for index, (method, row) in enumerate(variants):
            with self.subTest(variant=index), self.factory(True) as repository, patch.object(repository, method, return_value=row):
                if method == "get_saved_search":
                    payload = json.loads(workflow["input_json"])
                    payload["manifest_sha256"] = row["manifest_sha256"]
                    rebound = {**workflow, "input_json": canonical(payload), "input_hash_sha256": digest(payload)}
                    with patch.object(repository, "get_workflow_run", return_value=rebound):
                        self.assert_rejected(repository, search_id)
                else:
                    self.assert_rejected(repository, search_id)

    def test_closed_saved_and_workflow_rows_require_exact_linked_and_requested_ids(self) -> None:
        first = self.configure(key="fictional-first")
        second = self.configure(key="fictional-second")
        record, workflow = self.records(first)
        other, _ = self.records(second)
        variants = [("get_saved_search", {**record, "id": "fictional-wrong-id"}),
            ("get_saved_search", other), ("get_saved_search", {**record, "extra": None}),
            ("get_saved_search", {key: value for key, value in record.items() if key != "manifest_sha256"}),
            ("get_workflow_run", {**workflow, "id": "fictional-wrong-workflow"}),
            ("get_workflow_run", {**workflow, "extra": None}),
            ("get_workflow_run", {key: value for key, value in workflow.items() if key != "model_name"})]
        for index, (method, row) in enumerate(variants):
            with self.subTest(variant=index), self.factory(True) as repository, patch.object(repository, method, return_value=row):
                self.assert_rejected(repository, first)

    def test_hashes_and_exact_single_artifact_cannot_be_rebound(self) -> None:
        search_id = self.configure()
        record, workflow = self.records(search_id)
        variants = [("get_saved_search", {**record, "manifest_sha256": "0" * 64}),
            ("get_workflow_run", {**workflow, "input_hash_sha256": "0" * 64}),
            ("get_workflow_run", {**workflow, "idempotency_key": "0" * 64}),
            ("get_workflow_run", {**workflow, "generated_artifacts_json": '["fictional-other-search"]'}),
            ("get_workflow_run", {**workflow, "generated_artifacts_json": json.dumps([search_id, search_id])})]
        for index, (method, row) in enumerate(variants):
            with self.subTest(variant=index), self.factory(True) as repository, patch.object(repository, method, return_value=row):
                self.assert_rejected(repository, search_id)

    def test_workflow_defaults_and_original_clock_bindings_are_required(self) -> None:
        search_id = self.configure()
        record, workflow = self.records(search_id)
        changes = [(field, "fictional-unsupported") for field in ("model_name", "prompt_version", "failure_code", "failure_reason")]
        changes += [("outstanding_need_info_json", '[{"reason":"fictional"}]'), ("retry_policy_json", '{"attempts":1}'),
            ("started_at", None), ("started_at", "2029-12-31T19:00:00-05:00"),
            ("finished_at", "2030-01-01T00:00:00+00:00"), ("created_at", "2030-01-01T00:00:00+00:00"),
            ("updated_at", "malformed"), ("updated_at", "2030-01-01T00:00:00"), ("updated_at", "2030-01-01T00:30:00+01:00")]
        for field, value in changes:
            with self.subTest(field=field, value=value), self.factory(True) as repository, \
                 patch.object(repository, "get_workflow_run", return_value={**workflow, field: value}):
                self.assert_rejected(repository, search_id)
        for created in ("2030-01-01T00:00:00+00:00", "2030-01-01T01:00:00.000000+01:00"):
            with self.subTest(noncanonical_creation=created), self.factory(True) as repository, \
                 patch.object(repository, "get_saved_search", return_value={**record, "created_at": created}):
                self.assert_rejected(repository, search_id)

    def test_harmless_formatting_and_valid_offset_or_future_updates_preserve_file_identity(self) -> None:
        search_id = self.configure(self.manifest(schema_version=2,
            preparation_filters={"location_contains": ["Zürich"]}))
        record, workflow = self.records(search_id)
        manifest = json.loads(record["manifest_json"])
        self.persist(search_id, scope={"manifest_json": json.dumps(manifest, indent=2, ensure_ascii=True)},
            workflow={"input_json": json.dumps(json.loads(workflow["input_json"]), indent=2),
                "completed_steps_json": '[ "validate", "persist" ]', "generated_artifacts_json": json.dumps([search_id], indent=2)})
        for updated in ("2030-01-01T01:00:00+01:00", "2031-01-01T00:00:00+00:00"):
            self.persist(search_id, workflow={"updated_at": updated})
            before = (self.database.read_bytes(), self.database.stat().st_mtime_ns,
                sorted(path.name for path in self.database.parent.iterdir()))
            self.assertEqual(self.service.list_searches()[0]["manifest"], manifest)
            with SQLiteRepository.from_snapshot(before[0]) as repository:
                self.assertEqual(self.service._scope(repository, search_id)[1], manifest)
            self.assertEqual((self.database.read_bytes(), self.database.stat().st_mtime_ns,
                sorted(path.name for path in self.database.parent.iterdir())), before)

    def test_missing_search_and_invalid_caller_inputs_keep_their_existing_contracts(self) -> None:
        with self.factory(True) as repository:
            with self.assertRaisesRegex(ValueError, "^Saved search does not exist$"):
                self.service._scope(repository, "fictional-missing")
            with patch.object(repository, "get_saved_search", side_effect=AssertionError("Invalid ID must not query")):
                for identifier in (None, True, "invalid/id"):
                    with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                        self.service._scope(repository, identifier)
        with patch.object(self.service, "_open", side_effect=AssertionError("Invalid input must not open")):
            for changes in ({"schema_version": True}, {"max_jobs": True}, {"unexpected": "fictional"}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    self.configure(self.manifest(**changes))
            self.assertTrue(self.service.configure(self.manifest(), idempotency_key="fictional-preview", dry_run=True)["dry_run"])

    def test_scope_owns_one_snapshot_and_borrows_caller_work_without_committing_it(self) -> None:
        search_id = self.configure()
        with self.factory(False) as repository:
            trace = []
            repository._connection.set_trace_callback(trace.append)
            self.service._scope(repository, search_id)
            self.assertEqual([sql for sql in trace if sql in {"BEGIN", "ROLLBACK"}], ["BEGIN", "ROLLBACK"])
            before = repository._connection.serialize()
            sentinel = RuntimeError("fictional caller rollback")
            with self.assertRaises(RuntimeError) as raised:
                with repository.transaction():
                    marker = repository.add_workflow_run(workflow_type="fictional-caller", status="queued")
                    self.service._scope(repository, search_id)
                    with patch.object(repository, "get_workflow_run", side_effect=sqlite3.OperationalError("fictional private SQL")), \
                         self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
                        self.service._scope(repository, search_id)
                    self.assertTrue(repository._connection.in_transaction)
                    self.assertIsNotNone(repository.get_workflow_run(marker["id"]))
                    raise sentinel
            self.assertIs(raised.exception, sentinel)
            self.assertEqual(repository._connection.serialize(), before)

    def test_post_write_configuration_corruption_rolls_back_search_workflow_and_lease(self) -> None:
        from grounded_apply.services import searches
        finish = searches.finish_workflow
        def corrupt(repository, workflow_id, artifacts, at):
            finish(repository, workflow_id, artifacts, at)
            repository._connection.execute("UPDATE workflow_runs SET model_name = ? WHERE id = ?", ("fictional-unexpected-model", workflow_id))
        before = self.database.read_bytes()
        with patch.object(searches, "finish_workflow", side_effect=corrupt), self.assertRaisesRegex(SearchIntegrityError, f"^{ERROR}$"):
            self.configure()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.service.list_searches(), ())

    def test_expected_storage_and_record_errors_have_fixed_content_free_messages(self) -> None:
        search_id = self.configure()
        for method in ("get_saved_search", "get_workflow_run"):
            with self.subTest(method=method), self.factory(True) as repository, \
                 patch.object(repository, method, side_effect=sqlite3.OperationalError("fictional private storage detail")):
                self.assert_rejected(repository, search_id)
        for failure in (ValueError("fictional private manifest"), RecursionError("fictional private depth")):
            with self.subTest(failure=type(failure).__name__), self.factory(True) as repository, \
                 patch("grounded_apply.services.searches.validate_search_manifest", side_effect=failure):
                self.assert_rejected(repository, search_id)

    def test_unexpected_errors_and_interrupts_preserve_identity_and_release_owned_snapshot(self) -> None:
        search_id = self.configure()
        for failure in (RuntimeError("fictional programmer error"), KeyboardInterrupt("fictional interrupted read")):
            for method in ("get_saved_search", "get_workflow_run"):
                with self.subTest(method=method, failure=type(failure).__name__), self.factory(True) as repository:
                    before = repository._connection.serialize()
                    with patch.object(repository, method, side_effect=failure), self.assertRaises(type(failure)) as raised:
                        self.service._scope(repository, search_id)
                    self.assertIs(raised.exception, failure)
                    self.assertFalse(repository._connection.in_transaction)
                    self.assertEqual(repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
