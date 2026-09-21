from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from grounded_apply.repositories import RepositoryError
from grounded_apply.services import search_scope_history
from grounded_apply.services.searches import validate_search_manifest
from grounded_apply.services.workflow import canonical, digest


AT = "2026-09-20T12:00:00.000000+00:00"
ERROR = "^Saved search failed integrity checks$"


class SearchScopeRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = validate_search_manifest({"schema_version": 1,
            "sources": [{"id": "fictional-source", "provider": "greenhouse", "board": "example"}],
            "claim_ids": ["fictional-claim"], "title_contains": ["café researcher"]})
        self.payload = {"version": 1, "manifest_sha256": digest(self.manifest), "idempotency_sha256": "b" * 64}
        self.search_id = str(uuid5(NAMESPACE_URL, "grounded-apply.search@1/" + self.payload["idempotency_sha256"]))
        self.record = {"id": self.search_id, "manifest_json": canonical(self.manifest),
            "manifest_sha256": digest(self.manifest), "created_at": AT, "workflow_run_id": "fictional-configure"}
        self.workflow = {"id": self.record["workflow_run_id"], "workflow_type": "search_configure",
            "status": "succeeded", "idempotency_key": self.payload["idempotency_sha256"],
            "input_hash_sha256": digest(self.payload), "input_json": canonical(self.payload),
            "current_step": "complete", "completed_steps_json": '["validate","persist"]',
            "generated_artifacts_json": canonical([self.search_id]), "outstanding_need_info_json": "[]",
            "model_name": None, "prompt_version": None, "retry_policy_json": "{}",
            "failure_code": None, "failure_reason": None, "started_at": AT,
            "finished_at": AT, "created_at": AT, "updated_at": AT}

    def validate(self, *, record=None, workflow=None, search_id=None, normalize=validate_search_manifest):
        return search_scope_history.validate_search_scope_record(
            self.record if record is None else record, self.workflow if workflow is None else workflow,
            self.search_id if search_id is None else search_id, normalize_manifest=normalize)

    def rebind_manifest(self, manifest: dict) -> None:
        self.record.update(manifest_json=canonical(manifest), manifest_sha256=digest(manifest))
        self.payload["manifest_sha256"] = digest(manifest)
        self.workflow.update(input_json=canonical(self.payload), input_hash_sha256=digest(self.payload))

    def test_registered_v1_v2_and_harmless_encoding_preserve_records(self) -> None:
        for version in (1, 2):
            manifest = validate_search_manifest({**self.manifest, "schema_version": version})
            self.rebind_manifest(manifest)
            self.record["manifest_json"] = json.dumps(manifest, indent=2, ensure_ascii=True)
            self.workflow["input_json"] = json.dumps(self.payload, indent=3)
            self.workflow["completed_steps_json"] = ' [ "validate", "persist" ] '
            self.workflow["generated_artifacts_json"] = canonical([self.search_id]).replace("-", "\\u002d")
            before = deepcopy((self.record, self.workflow))
            result = self.validate()
            self.assertEqual(result, manifest)
            self.assertEqual((self.record, self.workflow), before)
            result["title_contains"].append("fictional-return-value-change")
            self.assertEqual((self.record, self.workflow), before)

    def test_closed_origin_and_workflow_shapes_and_identity_bindings(self) -> None:
        for original, argument in ((self.record, "record"), (self.workflow, "workflow")):
            for changed in ({**original, "extra": "fictional"},
                            {key: value for key, value in original.items() if key != "id"},
                            {**original, "id": "wrong-fictional-id"}, {**original, "id": True}):
                with self.subTest(argument=argument), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(**{argument: changed})
        for record, workflow in ((None, self.workflow), (self.record, None), ([], self.workflow)):
            with self.assertRaisesRegex(ValueError, ERROR):
                search_scope_history.validate_search_scope_record(record, workflow, self.search_id,
                    normalize_manifest=validate_search_manifest)
        for value in ("wrong-fictional-id", "invalid/id", True):
            with self.subTest(search_id=value), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(search_id=value)
        with self.assertRaisesRegex(ValueError, ERROR):
            self.validate(record={**self.record, "workflow_run_id": "other-fictional-workflow"})

    def test_manifest_must_already_equal_registered_normalized_values(self) -> None:
        for changed in ({**self.manifest, "schema_version": True}, {**self.manifest, "max_jobs": 10.0},
                        {key: value for key, value in self.manifest.items() if key != "layout"},
                        {**self.manifest, "unexpected": "fictional"}):
            self.rebind_manifest(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, ERROR):
                self.validate()
        self.rebind_manifest({**self.manifest, "schema_version": True})
        with self.assertRaisesRegex(ValueError, ERROR):
            self.validate(normalize=lambda value: {**value, "schema_version": 1})

    def test_input_payload_rejects_aliases_extra_fields_and_rehashed_wrong_bindings(self) -> None:
        values = [{**self.payload, "version": value} for value in (True, 1.0, "1", 2)]
        values += [{**self.payload, "extra": "fictional"}, {**self.payload, "manifest_sha256": "c" * 64},
                   {**self.payload, "idempotency_sha256": "d" * 64}]
        for value in values:
            for rehash in (False, True):
                workflow = {**self.workflow, "input_json": canonical(value)}
                if rehash:
                    workflow["input_hash_sha256"] = digest(value)
                with self.subTest(value=value, rehash=rehash), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(workflow=workflow)

    def test_digests_and_deterministic_origin_identity_cannot_be_substituted(self) -> None:
        for field in ("idempotency_key", "input_hash_sha256"):
            for value in ("B" * 64, "b" * 63, 1, "c" * 64):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(workflow={**self.workflow, field: value})
        for value in ("A" * 64, None, "c" * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(record={**self.record, "manifest_sha256": value})
        changed_id = "fictional-rebound-search"
        with self.assertRaisesRegex(ValueError, ERROR):
            self.validate(search_id=changed_id, record={**self.record, "id": changed_id},
                workflow={**self.workflow, "generated_artifacts_json": canonical([changed_id])})

    def test_recursive_duplicate_nonfinite_wrong_type_and_deep_json_are_fixed_errors(self) -> None:
        malformed = (None, b"{}", '{"x":1,"x":1}', '{"nested":[{"x":1,"x":2}]}',
                     '{"number":NaN}', '[Infinity]', '[-Infinity]', '{"nested":[1e999]}',
                     '["fictional-private-detail",]', "[" * 10000 + "]" * 10000)
        for field in ("manifest_json", "input_json", "completed_steps_json", "generated_artifacts_json",
                      "outstanding_need_info_json", "retry_policy_json"):
            for value in malformed:
                argument, original = ("record", self.record) if field == "manifest_json" else ("workflow", self.workflow)
                with self.subTest(field=field, value_type=type(value)), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(**{argument: {**original, field: value}})
        for field, raw in (("manifest_json", self.record["manifest_json"]), ("input_json", self.workflow["input_json"])):
            duplicate = ('{"schema_version":1,' if field == "manifest_json" else '{"version":1,') + raw[1:]
            argument, original = ("record", self.record) if field == "manifest_json" else ("workflow", self.workflow)
            with self.assertRaisesRegex(ValueError, ERROR):
                self.validate(**{argument: {**original, field: duplicate}})

    def test_completed_default_workflow_metadata_is_closed(self) -> None:
        for field, value in (("workflow_type", "search_run"), ("status", "running"), ("current_step", "persist"),
                            ("model_name", "fictional-model"), ("prompt_version", "fictional-prompt"),
                            ("failure_code", "fictional-failure"), ("failure_reason", "fictional-private-detail"),
                            ("outstanding_need_info_json", '["fictional-pending"]'), ("retry_policy_json", '{"attempts":1}'),
                            ("completed_steps_json", '["persist","validate"]'),
                            ("generated_artifacts_json", '["wrong-fictional-search"]')):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(workflow={**self.workflow, field: value})

    def test_creation_clock_is_canonical_and_workflow_bindings_remain_raw_exact(self) -> None:
        bad = (None, AT[:-6], "fictional-private-time", "2026-09-20T12:00:00+00:00",
               "2026-09-20T14:00:00.000000+02:00")
        for value in bad:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(record={**self.record, "created_at": value}, workflow={**self.workflow,
                    **{field: value for field in ("created_at", "started_at", "finished_at", "updated_at")}})
        for field in ("created_at", "started_at", "finished_at"):
            for value in (None, "2026-09-20T14:00:00.000000+02:00", "2026-09-20T12:00:01.000000+00:00"):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate(workflow={**self.workflow, field: value})

    def test_updated_time_allows_equal_offsets_and_future_but_not_earlier_or_naive(self) -> None:
        for value in ("2026-09-20T14:00:00+02:00", "2100-01-01T00:00:00+00:00"):
            self.assertEqual(self.validate(workflow={**self.workflow, "updated_at": value}), self.manifest)
        for value in (None, AT[:-6], "2026-09-20T14:00:00+03:00", "fictional-private-time"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, ERROR):
                self.validate(workflow={**self.workflow, "updated_at": value})

    def test_expected_errors_are_fixed_and_unexpected_failures_keep_identity(self) -> None:
        for error in (RepositoryError("fictional-private-detail"), ValueError("fictional-private-detail"),
                      TypeError("fictional-private-detail"), RecursionError("fictional-private-detail")):
            with patch.object(search_scope_history, "validate_workflow", side_effect=error), \
                 self.assertRaisesRegex(ValueError, ERROR):
                self.validate()
        for error in (RuntimeError("fictional-programmer-error"), AssertionError("fictional-assertion"),
                      KeyboardInterrupt(), SystemExit()):
            with patch.object(search_scope_history, "validate_workflow", side_effect=error), \
                 self.assertRaises(type(error)) as caught:
                self.validate()
            self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
