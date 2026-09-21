from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from grounded_apply.services import material_build_history
from grounded_apply.services.workflow import canonical, digest
from tests import test_material_history


ERROR = "^Material build record failed integrity checks$"


class MaterialBuildRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = test_material_history.MaterialHistoryTests()
        fixture.setUp()
        try:
            claim_id = fixture.generic_claim("build-record", text="Used Python for a fictional café service")
            cls.records = []
            for transformation, layout in (
                ("approved_text_selection@1", None), ("approved_text_selection@2", None),
                ("approved_text_selection@2", {"schema_version": 1, "presentations": {claim_id: "paragraph"}}),
            ):
                material_id = fixture.build((claim_id,), transformation=transformation, layout=layout)
                material = fixture.service.get(material_id, require_current=False)
                workflow = fixture.repository.get_workflow_run(material["workflow_run_id"])
                cls.records.append((material, workflow))
        finally:
            fixture.doCleanups()

    def records_for(self, index: int = 1) -> tuple[dict, dict]:
        return deepcopy(self.records[index])

    def rebind(self, material: dict, workflow: dict) -> None:
        """Keep checksums consistent when testing typed decoded-value failures."""
        material["manifest"]["structure_sha256"] = digest(material["structure"])
        for field in ("structure", "manifest", "validation"):
            material[field + "_json"] = canonical(material[field])
        material["bundle_sha256"] = digest({field: material[field] for field in ("structure", "manifest", "validation")})
        payload = json.loads(workflow["input_json"])
        payload.update(structure_sha256=digest(material["structure"]),
            question_specs_sha256=digest(material["manifest"]["question_specs"]),
            answers_sha256=digest(material["manifest"]["answers"]))
        workflow["input_json"], workflow["input_hash_sha256"] = canonical(payload), digest(payload)

    def test_both_transformations_and_presentations_return_independent_payload_without_mutation(self) -> None:
        for index in range(len(self.records)):
            material, workflow = self.records_for(index)
            before = deepcopy((material, workflow))
            payload = material_build_history.validate_material_build_record(material, workflow)
            self.assertEqual(payload, json.loads(workflow["input_json"]))
            self.assertEqual("presentations" in payload, index != 0)
            self.assertIs(type(payload["version"]), int)
            payload["selected_claim_ids"].append("fictional-result-only-change")
            self.assertEqual((material, workflow), before)

    def test_harmless_encodings_and_asdict_tuple_collections_are_preserved(self) -> None:
        material, workflow = self.records_for()
        self.assertIs(type(material["structure"]["units"]), tuple)
        self.assertIs(type(material["structure"]["units"][0]["packet_claim_ids"]), tuple)
        for field in ("structure_json", "manifest_json", "validation_json"):
            material[field] = json.dumps(dict(reversed(list(json.loads(material[field]).items()))), indent=2, ensure_ascii=True)
        for field in ("input_json", "completed_steps_json", "generated_artifacts_json", "outstanding_need_info_json", "retry_policy_json"):
            workflow[field] = " \n" + json.dumps(json.loads(workflow[field]), indent=2, ensure_ascii=True) + "\n "
        before = deepcopy((material, workflow))
        self.assertEqual(material_build_history.validate_material_build_record(material, workflow), json.loads(workflow["input_json"]))
        self.assertEqual((material, workflow), before)

    def test_all_material_json_columns_and_nested_workflow_keys_reject_duplicates(self) -> None:
        material, workflow = self.records_for(2)
        for field in ("structure_json", "manifest_json", "validation_json"):
            altered = {**material, field: '{"schema_version":false,' + material[field][1:]}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record(altered, workflow)
        cases = (
            ("input_json", '{"version":false,' + workflow["input_json"][1:]),
            ("input_json", workflow["input_json"].replace('"presentations":{', '"presentations":{"fictional-duplicate":1,"fictional-duplicate":2,')),
            ("retry_policy_json", '{"nested":{"fictional":1,"fictional":2}}'),
        )
        for field, value in cases:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record(material, {**workflow, field: value})

    def test_nonfinite_malformed_and_deep_json_fail_with_fixed_message(self) -> None:
        material, workflow = self.records_for()
        values = ('{"version":NaN}', '{"version":Infinity}', '{"version":-Infinity}',
                  '{"version":1e999}', "[" * 10000 + "]" * 10000, '{"fictional-private-detail":')
        for value in values:
            with self.subTest(length=len(value)), self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record(material, {**workflow, "input_json": value})

    def test_raw_and_decoded_values_reject_boolean_and_number_aliases(self) -> None:
        alterations = (("structure", "schema_version", True), ("manifest", "schema_version", 1.0),
                       ("validation", "schema_version", True), ("validation", "valid", 1),
                       ("validation", "page_count", True), ("validation", "unsupported_factual_units", False))
        for field, key, value in alterations:
            for rehash in (False, True):
                material, workflow = self.records_for()
                material[field][key] = value
                if rehash:
                    self.rebind(material, workflow)
                with self.subTest(field=field, key=key, rehash=rehash), self.assertRaisesRegex(ValueError, ERROR):
                    material_build_history.validate_material_build_record(material, workflow)

    def test_closed_rows_identifiers_and_material_scalar_types_are_required(self) -> None:
        material, workflow = self.records_for()
        for field, value in (("id", "fictional-other-material"), ("job_id", "fictional-other-job"),
                            ("workflow_run_id", "fictional-other-workflow"), ("bundle_sha256", "F" * 64),
                            ("created_at", "2030-01-01T00:00:00"), ("pdf_bytes", bytearray(material["pdf_bytes"])),
                            ("latex_text", b"fictional-latex"), ("extracted_text", 1), ("fictional-extra", None)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record({**material, field: value}, workflow)
        for value in (None, {}, {key: item for key, item in material.items() if key != "validation_json"}):
            with self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record(value, workflow)
        for value in (None, {}, {**workflow, "id": "fictional-other-workflow"}, {**workflow, "fictional-extra": None}):
            with self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record(material, value)

    def test_payload_versions_transformations_and_presentation_shapes_are_closed(self) -> None:
        for index in (0, 1):
            material, workflow = self.records_for(index)
            original = json.loads(workflow["input_json"])
            payloads = [{**original, "version": value} for value in (True, 1.0, "1", 2)]
            payloads += [{**original, "transformation": "fictional@3"}, {**original, "fictional_extra": None},
                         {**original, "selected_claim_ids": []}, {**original, "selected_claim_ids": [True]},
                         {**original, "selected_claim_ids": original["selected_claim_ids"] * 2},
                         {**original, "selected_claim_ids": ["fictional-claim"] * 81}]
            if index == 0:
                payloads.append({**original, "presentations": {}})
            else:
                payloads += [{key: value for key, value in original.items() if key != "presentations"},
                             {**original, "presentations": []},
                             {**original, "presentations": {original["selected_claim_ids"][0]: "fictional-style"}},
                             {**original, "presentations": {"fictional-unselected": "paragraph"}}]
            for number, payload in enumerate(payloads):
                with self.subTest(transformation=index, number=number), self.assertRaisesRegex(ValueError, ERROR):
                    material_build_history.validate_material_build_record(material, {**workflow, "input_json": canonical(payload)})

    def test_changed_material_and_input_digests_cannot_bind_unrelated_values(self) -> None:
        material, workflow = self.records_for()
        for field in ("structure_sha256", "answers_sha256", "question_specs_sha256", "idempotency_sha256"):
            payload = json.loads(workflow["input_json"])
            payload[field] = "e" * 64
            changed = {**workflow, "input_json": canonical(payload), "input_hash_sha256": digest(payload)}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record(material, changed)
        for field, value in (("pdf_bytes", b"fictional-other-pdf"), ("latex_text", "fictional-other-latex"),
                            ("extracted_text", "fictional-other-text"), ("bundle_sha256", "e" * 64)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record({**material, field: value}, workflow)

    def test_completed_workflow_requires_normal_metadata_and_exact_artifact(self) -> None:
        material, workflow = self.records_for()
        for field, value in (("model_name", "fictional-model"), ("prompt_version", "fictional-prompt"),
                            ("failure_code", "fictional-failure"), ("failure_reason", "fictional-private-detail"),
                            ("outstanding_need_info_json", '["fictional-pending"]'), ("retry_policy_json", '{"attempts":1}'),
                            ("completed_steps_json", '["validate"]'), ("generated_artifacts_json", '["fictional-other-material"]'),
                            ("workflow_type", "material_approval"), ("status", "running"), ("current_step", "validate")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, ERROR):
                material_build_history.validate_material_build_record(material, {**workflow, field: value})

    def test_raw_creation_bindings_and_aware_update_ordering_reject_invalid_clocks(self) -> None:
        material, workflow = self.records_for()
        for field in ("created_at", "started_at", "finished_at", "updated_at"):
            invalid = (None, "fictional-private-time", "2030-01-01T00:00:00", "2030-01-01T00:30:00+01:00")
            if field != "updated_at":
                invalid += ("2029-12-31T19:00:00-05:00",)
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, ERROR):
                    material_build_history.validate_material_build_record(material, {**workflow, field: value})

    def test_original_offset_creation_and_equivalent_or_future_updates_remain_valid(self) -> None:
        for updated in ("2030-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"):
            material, workflow = self.records_for()
            offset = "2029-12-31T19:00:00-05:00"
            material["created_at"] = material["manifest"]["created_at"] = offset
            self.rebind(material, workflow)
            for key in ("created_at", "started_at", "finished_at"):
                workflow[key] = offset
            workflow["updated_at"] = updated
            before = deepcopy((material, workflow))
            self.assertEqual(material_build_history.validate_material_build_record(material, workflow), json.loads(workflow["input_json"]))
            self.assertEqual((material, workflow), before)

    def test_programmer_errors_and_interrupts_keep_identity(self) -> None:
        material, workflow = self.records_for()
        for error in (RuntimeError("fictional-programmer-error"), AssertionError("fictional-assertion"), KeyboardInterrupt(), SystemExit()):
            with patch.object(material_build_history, "validate_workflow", side_effect=error), self.assertRaises(type(error)) as caught:
                material_build_history.validate_material_build_record(material, workflow)
            self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
