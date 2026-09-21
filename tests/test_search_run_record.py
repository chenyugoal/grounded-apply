from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from grounded_apply.repositories import RepositoryError
from grounded_apply.services import search_run_history
from grounded_apply.services.workflow import canonical, digest


AT = "2026-09-20T12:00:00.000000+00:00"
ERROR = "^Search run failed integrity checks$"


class SearchRunRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.search_id = "fictional-search"
        self.payload = {"version": 1, "search_id": self.search_id,
                        "manifest_sha256": "a" * 64, "idempotency_sha256": "b" * 64}
        self.run_id = str(uuid5(NAMESPACE_URL,
            f"grounded-apply.search-run@1/{self.search_id}/{self.payload['idempotency_sha256']}"))
        self.record = {"id": self.run_id, "search_id": self.search_id,
                       "created_at": AT, "workflow_run_id": "fictional-run-workflow"}
        self.workflow = {"id": self.record["workflow_run_id"], "workflow_type": "search_run_create",
            "status": "succeeded", "idempotency_key": self.payload["idempotency_sha256"],
            "input_hash_sha256": digest(self.payload), "input_json": canonical(self.payload),
            "current_step": "complete", "completed_steps_json": '["validate","persist"]',
            "generated_artifacts_json": canonical([self.run_id]), "outstanding_need_info_json": "[]",
            "model_name": None, "prompt_version": None, "retry_policy_json": "{}",
            "failure_code": None, "failure_reason": None, "started_at": AT,
            "finished_at": AT, "created_at": AT, "updated_at": AT}

    def validate(self, *, record=None, workflow=None, run_id=None, search_id=None, manifest_sha256=None) -> None:
        search_run_history.validate_search_run_record(
            self.record if record is None else record, self.workflow if workflow is None else workflow,
            self.run_id if run_id is None else run_id, search_id=self.search_id if search_id is None else search_id,
            manifest_sha256=self.payload["manifest_sha256"] if manifest_sha256 is None else manifest_sha256)

    def test_origin_returns_parent_and_full_validation_preserves_harmless_encodings(self) -> None:
        self.workflow["input_json"] = json.dumps(self.payload, indent=2)
        self.workflow["completed_steps_json"] = ' [ "validate", "persist" ] '
        self.workflow["generated_artifacts_json"] = canonical([self.run_id]).replace("-", "\\u002d")
        before = deepcopy((self.record, self.workflow))
        self.assertEqual(search_run_history.validate_search_run_origin(self.record, self.run_id), self.search_id)
        self.assertIsNone(self.validate())
        self.assertEqual((self.record, self.workflow), before)

    def test_both_apis_reject_closed_row_shape_identity_and_noncanonical_creation(self) -> None:
        records = [None, [], {**self.record, "extra": "fictional"},
                   {key: value for key, value in self.record.items() if key != "created_at"}]
        for field in ("id", "search_id", "workflow_run_id"):
            records.extend({**self.record, field: value} for value in (None, True, "invalid/id"))
        records.append({**self.record, "id": "wrong-fictional-run"})
        for value in (None, AT[:-6], "fictional-private-time", "2026-09-20T12:00:00+00:00",
                      "2026-09-20T14:00:00.000000+02:00"):
            records.append({**self.record, "created_at": value})
        for record in records:
            for parser in (
                lambda value: search_run_history.validate_search_run_origin(value, self.run_id),
                lambda value: search_run_history.validate_search_run_record(value, self.workflow, self.run_id,
                    search_id=self.search_id, manifest_sha256=self.payload["manifest_sha256"]),
            ):
                with self.subTest(record=record), self.assertRaisesRegex(ValueError, ERROR):
                    parser(record)
        for value in (True, "wrong-fictional-run", "invalid/id"):
            with self.subTest(run_id=value), self.assertRaisesRegex(ValueError, ERROR):
                search_run_history.validate_search_run_origin(self.record, value)

    def test_origin_does_not_infer_parent_or_workflow_custody_before_full_check(self) -> None:
        row = {**self.record, "search_id": "different-fictional-parent", "workflow_run_id": "different-fictional-workflow"}
        self.assertEqual(search_run_history.validate_search_run_origin(row, self.run_id), row["search_id"])
        with self.assertRaisesRegex(ValueError, ERROR):
            self.validate(record=row)

    def test_full_workflow_shape_parent_and_linked_identity_are_exact(self) -> None:
        for workflow in (None, [], {**self.workflow, "extra": "fictional"},
                         {key: value for key, value in self.workflow.items() if key != "updated_at"},
                         {**self.workflow, "id": "wrong-fictional-workflow"}, {**self.workflow, "id": True}):
            with self.subTest(workflow=workflow), self.assertRaisesRegex(ValueError, ERROR):
                search_run_history.validate_search_run_record(self.record, workflow, self.run_id,
                    search_id=self.search_id, manifest_sha256=self.payload["manifest_sha256"])
        for value in (True, "invalid/id", "different-fictional-parent"):
            with self.subTest(search_id=value), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(search_id=value)
        with self.assertRaisesRegex(ValueError, ERROR):
            self.validate(record={**self.record, "workflow_run_id": "different-fictional-workflow"})

    def test_payload_rejects_aliases_unknown_fields_and_rehashed_changed_parent_or_manifest(self) -> None:
        values = [{**self.payload, "version": value} for value in (True, 1.0, "1", 2)]
        values += [{**self.payload, "extra": "fictional"}, {**self.payload, "search_id": "different-fictional-parent"},
                   {**self.payload, "manifest_sha256": "c" * 64}, {**self.payload, "idempotency_sha256": "d" * 64}]
        for value in values:
            for rehash in (False, True):
                workflow = {**self.workflow, "input_json": canonical(value)}
                if rehash:
                    workflow["input_hash_sha256"] = digest(value)
                with self.subTest(value=value, rehash=rehash), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(workflow=workflow)

    def test_digest_shapes_and_deterministic_run_identity_are_bound(self) -> None:
        for field in ("idempotency_key", "input_hash_sha256"):
            for value in ("B" * 64, "b" * 63, 1, "c" * 64):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(workflow={**self.workflow, field: value})
        for value in ("A" * 64, "a" * 63, True, "c" * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(manifest_sha256=value)
        changed_id = "fictional-rebound-run"
        with self.assertRaisesRegex(ValueError, ERROR):
            self.validate(run_id=changed_id, record={**self.record, "id": changed_id},
                workflow={**self.workflow, "generated_artifacts_json": canonical([changed_id])})

    def test_all_json_fields_reject_recursive_duplicates_nonfinite_depth_and_wrong_types(self) -> None:
        malformed = (None, b"{}", '{"x":1,"x":1}', '{"nested":[{"x":1,"x":2}]}',
                     '{"number":NaN}', '[Infinity]', '[-Infinity]', '{"nested":[1e999]}',
                     '["fictional-private-detail",]', "[" * 10000 + "]" * 10000)
        for field in ("input_json", "completed_steps_json", "generated_artifacts_json",
                      "outstanding_need_info_json", "retry_policy_json"):
            for value in malformed:
                with self.subTest(field=field, value_type=type(value)), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(workflow={**self.workflow, field: value})
        duplicate = '{"version":1,' + self.workflow["input_json"][1:]
        with self.assertRaisesRegex(ValueError, ERROR):
            self.validate(workflow={**self.workflow, "input_json": duplicate})

    def test_completed_workflow_defaults_cannot_claim_extra_execution_metadata(self) -> None:
        for field, value in (("workflow_type", "search_configure"), ("status", "running"), ("current_step", "persist"),
                            ("model_name", "fictional-model"), ("prompt_version", "fictional-prompt"),
                            ("failure_code", "fictional-failure"), ("failure_reason", "fictional-private-detail"),
                            ("outstanding_need_info_json", '["fictional-pending"]'), ("retry_policy_json", '{"attempts":1}'),
                            ("completed_steps_json", '["persist","validate"]'),
                            ("generated_artifacts_json", '["wrong-fictional-run"]')):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(workflow={**self.workflow, field: value})

    def test_workflow_creation_clocks_bind_raw_and_updates_compare_aware_instants(self) -> None:
        for field in ("created_at", "started_at", "finished_at"):
            for value in (None, AT[:-6], "2026-09-20T14:00:00.000000+02:00", "2026-09-20T12:00:01.000000+00:00"):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(workflow={**self.workflow, field: value})
        for value in ("2026-09-20T14:00:00+02:00", "2100-01-01T00:00:00+00:00"):
            self.assertIsNone(self.validate(workflow={**self.workflow, "updated_at": value}))
        for value in (None, AT[:-6], "2026-09-20T14:00:00+03:00", "fictional-private-time"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(workflow={**self.workflow, "updated_at": value})

    def test_fixed_expected_errors_and_original_unexpected_failures_for_both_apis(self) -> None:
        for error in (RepositoryError("fictional-private-detail"), ValueError("fictional-private-detail"),
                      TypeError("fictional-private-detail"), RecursionError("fictional-private-detail")):
            with patch.object(search_run_history, "opaque", side_effect=error):
                for call in (lambda: search_run_history.validate_search_run_origin(self.record, self.run_id), self.validate):
                    with self.assertRaisesRegex(ValueError, ERROR):
                        call()
        for error in (RuntimeError("fictional-programmer-error"), AssertionError("fictional-assertion"),
                      KeyboardInterrupt(), SystemExit()):
            with patch.object(search_run_history, "opaque", side_effect=error):
                for call in (lambda: search_run_history.validate_search_run_origin(self.record, self.run_id), self.validate):
                    with self.assertRaises(type(error)) as caught:
                        call()
                    self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
