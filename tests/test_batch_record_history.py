from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from grounded_apply.services import batch_history
from grounded_apply.services.workflow import canonical, digest


AT = "2026-09-20T12:00:00.000000+00:00"
ERROR = "^Batch record failed integrity checks$"


class BatchRecordHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {"version": 1, "manifest_sha256": "a" * 64,
                        "batch_schema_version": 1, "idempotency_sha256": "b" * 64}
        self.batch = {"id": "fictional-batch", "manifest_json": '{"label":"Fictional café"}',
                      "manifest_sha256": "a" * 64, "created_at": AT, "workflow_run_id": "fictional-workflow"}
        self.item = {"id": "fictional-item", "batch_id": self.batch["id"], "position": 0,
                     "job_id": "fictional-job", "spec_json": "{}", "spec_sha256": "c" * 64}
        self.event = {"id": "fictional-event", "item_id": self.item["id"], "position": 0,
                      "at": AT, "state_json": "{}", "previous_sha256": None, "event_sha256": "d" * 64}
        self.lease = {"batch_id": self.batch["id"], "owner": None, "expires_at": None,
                      "epoch": 0, "stop_reason": None}
        self.workflow = {"id": self.batch["workflow_run_id"], "workflow_type": "batch_create",
                         "status": "succeeded", "idempotency_key": self.payload["idempotency_sha256"],
                         "input_hash_sha256": digest(self.payload), "input_json": canonical(self.payload),
                         "current_step": "complete", "completed_steps_json": '["validate","persist"]',
                         "generated_artifacts_json": '["fictional-batch"]', "outstanding_need_info_json": "[]",
                         "model_name": None, "prompt_version": None, "retry_policy_json": "{}",
                         "failure_code": None, "failure_reason": None, "started_at": AT,
                         "finished_at": AT, "created_at": AT, "updated_at": AT}

    def validate_workflow(self, row: object) -> None:
        batch_history.validate_batch_workflow(row, self.payload, batch_id=self.batch["id"],
            created_at=AT, workflow_run_id=self.workflow["id"])

    def test_json_rejects_recursive_duplicates_nonfinite_values_and_depth_with_fixed_error(self) -> None:
        malformed = (None, b"{}", '{"x":1,"x":1}', '{"nested":[{"x":1,"x":2}]}',
                     '{"number":NaN}', '[Infinity]', '[-Infinity]', '{"nested":[1e999]}',
                     '["fictional-private-detail",]', "[" * 10000 + "]" * 10000)
        for value in malformed:
            with self.subTest(value_type=type(value)), self.assertRaisesRegex(ValueError, ERROR):
                batch_history.decode_batch_json(value)
        self.assertEqual(batch_history.decode_batch_json(' { "b":1.25, "a":"caf\\u00e9" } '),
                         {"a": "café", "b": 1.25})

    def test_closed_rows_decode_without_mutating_or_rewriting_input(self) -> None:
        for row, parser, decoded in (
            (self.batch, lambda value: batch_history.parse_batch_record(value, self.batch["id"]), "manifest"),
            (self.item, batch_history.parse_batch_item, "spec"),
            (self.event, batch_history.parse_batch_event, "state"),
        ):
            before = deepcopy(row)
            result = parser(row)
            self.assertEqual(result[decoded], json.loads(row[decoded + "_json"]))
            self.assertNotIn(decoded + "_json", result)
            self.assertEqual(row, before)
            for changed in (None, {**row, "unexpected": "fictional"},
                            {key: value for key, value in row.items() if key != "id"},
                            {**row, "id": "invalid/id"}, {**row, decoded + "_json": "[]"}):
                with self.subTest(decoded=decoded), self.assertRaisesRegex(ValueError, ERROR):
                    parser(changed)

    def test_metadata_positions_hashes_and_canonical_batch_clocks_are_exact(self) -> None:
        for parser, row, position_key in ((batch_history.parse_batch_item, self.item, "position"),
                                         (batch_history.parse_batch_event, self.event, "position")):
            for value in (True, 0.0, -1, "0", None):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, ERROR):
                    parser({**row, position_key: value})
        for key, value in (("event_sha256", "D" * 64), ("previous_sha256", "fictional-private-hash"),
                           ("item_id", True), ("at", "2026-09-20T12:00:00+00:00"),
                           ("at", "2026-09-20T14:00:00.000000+02:00"), ("at", AT[:-6])):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, ERROR):
                batch_history.parse_batch_event({**self.event, key: value})

    def test_lease_shape_allows_active_expiry_without_testing_current_time(self) -> None:
        for expiry in ("2000-01-01T00:00:00.000000+00:00", "2100-01-01T00:00:00.000000+00:00"):
            row = {**self.lease, "owner": "fictional-owner", "expires_at": expiry, "epoch": 2}
            self.assertEqual(batch_history.parse_batch_lease(row, self.batch["id"]), row)
        for changed in (None, {**self.lease, "unknown": 1}, {**self.lease, "epoch": True},
                        {**self.lease, "epoch": 1.0}, {**self.lease, "epoch": -1},
                        {**self.lease, "owner": "fictional-owner"}, {**self.lease, "expires_at": AT},
                        {**self.lease, "stop_reason": []}, {**self.lease, "stop_reason": "fictional-unknown"},
                        {**self.lease, "batch_id": "different-fictional-batch"}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, ERROR):
                batch_history.parse_batch_lease(changed, self.batch["id"])

    def test_workflow_rejects_numeric_aliases_and_unknown_payload_fields_with_original_hash(self) -> None:
        for key in ("version", "batch_schema_version"):
            for value in (True, 1.0, "1", 2):
                changed = {**self.payload, key: value}
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate_workflow({**self.workflow, "input_json": canonical(changed)})
        for payload in ({**self.payload, "extra": "fictional"},
                        {**self.payload, "manifest_sha256": "e" * 64}):
            with self.assertRaisesRegex(ValueError, ERROR):
                self.validate_workflow({**self.workflow, "input_json": canonical(payload),
                                       "input_hash_sha256": digest(payload)})

    def test_completed_workflow_rejects_unsupported_metadata_and_ambiguous_json(self) -> None:
        for field, value in (("model_name", "fictional-model"), ("prompt_version", "fictional-prompt"),
                            ("failure_code", "fictional-failure"), ("failure_reason", "fictional-private-detail"),
                            ("outstanding_need_info_json", '["fictional-pending"]'),
                            ("retry_policy_json", '{"nested":{"key":1,"key":1}}'),
                            ("completed_steps_json", '["validate"]'),
                            ("generated_artifacts_json", '["different-fictional-batch"]'),
                            ("input_json", '{"version":1,' + self.workflow["input_json"][1:]),
                            ("id", "different-fictional-workflow"), ("extra", "fictional-extra")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                self.validate_workflow({**self.workflow, field: value})

    def test_workflow_time_bindings_allow_later_aware_updates_and_preserve_encodings(self) -> None:
        for field in ("created_at", "started_at", "finished_at", "updated_at"):
            invalid = (None, AT[:-6], "fictional-private-time", "2026-09-20T11:59:59.000000+00:00")
            if field != "updated_at":
                invalid += ("2026-09-20T14:00:00.000000+02:00",)
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, ERROR):
                    self.validate_workflow({**self.workflow, field: value})
        for updated in ("2026-09-20T14:00:00+02:00", "2100-01-01T00:00:00+00:00"):
            row = {**self.workflow, "input_json": json.dumps(self.payload, indent=2),
                   "generated_artifacts_json": ' ["fictional-\\u0062atch"] ',
                   "completed_steps_json": ' [ "validate", "persist" ] ', "updated_at": updated}
            before = deepcopy(row)
            self.assertIsNone(self.validate_workflow(row))
            self.assertEqual(row, before)

    def test_unexpected_runtime_errors_and_interrupts_keep_identity(self) -> None:
        for error in (RuntimeError("fictional-programmer-error"), AssertionError("fictional-assertion"),
                      KeyboardInterrupt(), SystemExit()):
            with patch.object(batch_history, "validate_workflow", side_effect=error), \
                 self.assertRaises(type(error)) as caught:
                self.validate_workflow(self.workflow)
            self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
