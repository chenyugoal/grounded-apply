from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.materials import MaterialBlocked, MaterialHistoryIntegrityError, MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.workflow import canonical, digest
from tests import test_material_approval_history, test_material_history
from tests.test_batches import RecordingRenderer
from tests.test_material_history import MATERIAL_AT


ERROR = "Material historical facts failed integrity checks"


class MaterialBuildHistoryTests(unittest.TestCase):
    build = test_material_history.MaterialHistoryTests.build
    generic_claim = test_material_history.MaterialHistoryTests.generic_claim
    retire = test_material_history.MaterialHistoryTests.retire
    approve = test_material_approval_history.MaterialApprovalHistoryTests.approve

    def setUp(self) -> None:
        test_material_history.MaterialHistoryTests.setUp(self)
        self.renderer = RecordingRenderer()
        self.service = MaterialService(self.repository, self.renderer)

    def workflow(self, material_id: str) -> dict:
        return self.repository.get_workflow_run(self.repository.get_material_version(material_id)["workflow_run_id"])

    def workflow_patch(self, original: dict, replacement: dict):
        get = self.repository.get_workflow_run
        return patch.object(self.repository, "get_workflow_run",
            side_effect=lambda identifier: replacement if identifier == original["id"] else get(identifier))

    def update_workflow(self, material_id: str, fields: dict[str, object]) -> None:
        workflow = self.workflow(material_id)
        self.assertTrue(set(fields) <= set(workflow) - {"id"})
        with self.repository.transaction():
            self.repository._connection.execute("UPDATE workflow_runs SET " + ", ".join(f"{key} = ?" for key in fields)
                + " WHERE id = ?", (*fields.values(), workflow["id"]))

    def update_material(self, material_id: str, fields: dict[str, object]) -> None:
        """Only synthetic storage is changed, with the exact trigger restored."""
        row = self.repository.get_material_version(material_id)
        self.assertTrue(set(fields) <= set(row) - {"id"})
        connection = self.repository._connection
        trigger = connection.execute("SELECT sql FROM sqlite_schema WHERE name = 'materials_no_update'").fetchone()[0]
        with self.repository.transaction():
            connection.execute("DROP TRIGGER materials_no_update")
            connection.execute("UPDATE material_versions SET " + ", ".join(f"{key} = ?" for key in fields)
                + " WHERE id = ?", (*fields.values(), material_id))
            connection.execute(trigger)
        self.assertEqual(connection.execute("SELECT sql FROM sqlite_schema WHERE name = 'materials_no_update'").fetchone()[0], trigger)

    def duplicate(self, raw: str, key: str) -> str:
        value = json.loads(raw)
        return raw.rstrip()[:-1] + "," + canonical(key) + ":" + canonical(value[key]) + "}"

    def assert_rejected(self, material_id: str, *, before_profile: bool = True) -> None:
        before = self.repository._connection.serialize()
        with patch.object(ProfileService, "validated_profile", wraps=ProfileService.validated_profile) as profile:
            with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                self.service.validate_historical_facts(material_id)
            if before_profile:
                profile.assert_not_called()
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def assert_snapshot_rejected(self, material_id: str) -> None:
        image = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(image) as repository:
            service = MaterialService(repository, RecordingRenderer())
            # Existing bundle validation accepts these persisted records.
            self.assertEqual(service.get(material_id, require_current=False)["id"], material_id)
            with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("Invalid record precedes profile traversal")), \
                 self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                service.validate_historical_facts(material_id)
            self.assertEqual(repository._connection.serialize(), image)
        self.assertEqual(self.repository._connection.serialize(), image)

    def test_both_transformations_and_explicit_modern_presentations_keep_payload_version_one(self) -> None:
        heading = self.generic_claim("record-heading", claim_type="employment_description", value="Fictional role",
            text="Fictional Engineer | Example City | Example Lab | 2029",
            evidence_text=r"\resumeSubheading{Fictional Engineer}{Example City}{Example Lab}{2029}")
        for transformation, layout in (("approved_text_selection@1", None), ("approved_text_selection@2", None),
            ("approved_text_selection@2", {"schema_version": 1, "presentations": {heading: "paragraph"}})):
            with self.subTest(transformation=transformation, explicit=layout is not None):
                material_id = self.build((heading,), transformation=transformation, layout=layout)
                payload = json.loads(self.workflow(material_id)["input_json"])
                self.assertIs(type(payload["version"]), int)
                self.assertEqual(payload["version"], 1)
                self.assertEqual("presentations" in payload, transformation.endswith("@2"))
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_persisted_legacy_duplicate_model_and_raw_start_aliases_fail_after_snapshot_validation(self) -> None:
        for defect in ("duplicate", "model", "started_offset"):
            with self.subTest(defect=defect):
                material_id = self.build(transformation="approved_text_selection@1")
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                workflow = self.workflow(material_id)
                fields = ({"input_json": self.duplicate(workflow["input_json"], "version")} if defect == "duplicate"
                    else {"model_name": "fictional-unrecorded-model"} if defect == "model"
                    else {"started_at": "2029-12-31T19:00:00-05:00"})
                if defect == "started_offset":
                    self.assertEqual(datetime.fromisoformat(fields["started_at"]), datetime.fromisoformat(workflow["created_at"]))
                self.update_workflow(material_id, fields)
                self.assert_snapshot_rejected(material_id)

    def test_persisted_modern_updated_clock_cannot_precede_creation_despite_sql_lexical_order(self) -> None:
        material_id = self.build()
        updated = "2030-01-01T00:30:00+01:00"
        created = self.repository.get_material_version(material_id)["created_at"]
        self.assertGreater(updated, created)
        self.assertLess(datetime.fromisoformat(updated), datetime.fromisoformat(created))
        self.update_workflow(material_id, {"updated_at": updated})
        self.assert_snapshot_rejected(material_id)

    def test_duplicate_keys_in_every_raw_material_document_and_nested_objects_fail(self) -> None:
        questions = [{"id": "fictional-optional", "text": "Please sign this experience statement.",
            "claim_ids": [], "required": False}]
        material_id = self.build(questions=questions)
        original = self.repository.get_material_version(material_id)
        variants = [(field, self.duplicate(original[field], "schema_version"))
            for field in ("structure_json", "manifest_json", "validation_json")]
        structure = json.loads(original["structure_json"])
        unit = canonical(structure["units"][0])
        variants.append(("structure_json", canonical(structure).replace(unit, self.duplicate(unit, "text"), 1)))
        manifest = json.loads(original["manifest_json"])
        for array, key in (("question_specs", "id"), ("answers", "required")):
            nested = canonical(manifest[array][0])
            variants.append(("manifest_json", canonical(manifest).replace(nested, self.duplicate(nested, key), 1)))
        for index, (field, raw) in enumerate(variants):
            with self.subTest(location=index):
                self.assertEqual(json.loads(raw), json.loads(original[field]))
                self.assertNotEqual(raw, original[field])
                self.update_material(material_id, {field: raw})
                self.assert_snapshot_rejected(material_id)
                self.update_material(material_id, {field: original[field]})
                self.assertIsNone(self.service.validate_historical_facts(material_id))

    def test_literal_workflow_version_one_rejects_boolean_and_float_aliases(self) -> None:
        for transformation in ("approved_text_selection@1", "approved_text_selection@2"):
            material_id = self.build(transformation=transformation)
            workflow = self.workflow(material_id)
            for version in (True, 1.0):
                with self.subTest(transformation=transformation, version_type=type(version).__name__):
                    payload = json.loads(workflow["input_json"])
                    payload["version"] = version
                    with self.workflow_patch(workflow, {**workflow, "input_json": canonical(payload)}):
                        self.assertEqual(self.service.get(material_id, require_current=False)["id"], material_id)
                        self.assert_rejected(material_id)

    def test_closed_workflow_metadata_and_returned_identity_are_checked_before_profile(self) -> None:
        material_id = self.build()
        workflow = self.workflow(material_id)
        changes = [{**workflow, field: "fictional-unexpected"} for field in
            ("model_name", "prompt_version", "failure_code", "failure_reason")]
        changes += [{**workflow, "outstanding_need_info_json": '[{"reason":"fictional"}]'},
            {**workflow, "retry_policy_json": '{"attempts":1}'}, {**workflow, "retry_policy_json": '{"x":NaN}'},
            {**workflow, "id": "fictional-other-build"}, {**workflow, "extra": None},
            {key: value for key, value in workflow.items() if key != "model_name"}]
        for index, row in enumerate(changes):
            with self.subTest(variant=index), self.workflow_patch(workflow, row):
                self.assert_rejected(material_id)

    def test_returned_material_rows_are_closed_and_bound_to_requested_identity(self) -> None:
        material_id = self.build()
        original = self.repository.get_material_version(material_id)
        variants = [{**original, "id": "fictional-other-material"}, {**original, "extra": None},
            {key: value for key, value in original.items() if key != "latex_text"}]
        for index, row in enumerate(variants):
            with self.subTest(variant=index), patch.object(self.repository, "get_material_version", return_value=row):
                self.assert_rejected(material_id)
        other = self.build()
        other_bundle = self.service.get(other, require_current=False)
        with patch.object(self.service, "get", return_value=other_bundle):
            self.assert_rejected(material_id)

    def test_decoded_documents_cannot_disagree_with_their_raw_record(self) -> None:
        material_id = self.build()
        original = self.service.get(material_id, require_current=False)
        for field in ("structure", "manifest", "validation"):
            with self.subTest(field=field):
                changed = deepcopy(original)
                changed[field]["schema_version"] = True
                with patch.object(self.service, "get", return_value=changed):
                    self.assert_rejected(material_id)

    def test_raw_clock_bindings_and_aware_update_order_allow_equivalent_or_future_updates(self) -> None:
        material_id = self.build()
        workflow = self.workflow(material_id)
        for field, value in (("started_at", None), ("started_at", "2030-01-01T00:00:00"),
            ("started_at", "2030-01-01T01:00:00+01:00"), ("updated_at", "malformed"),
            ("updated_at", "2030-01-01T00:00:00"), ("updated_at", "2030-01-01T00:30:00+01:00")):
            with self.subTest(field=field, value=value), self.workflow_patch(workflow, {**workflow, field: value}):
                self.assert_rejected(material_id)
        for updated in ("2030-01-01T01:00:00+01:00", "2031-01-01T00:00:00+00:00"):
            with self.subTest(valid_update=updated), self.workflow_patch(workflow, {**workflow, "updated_at": updated}):
                self.assertIsNone(self.service.validate_historical_facts(material_id))

    def test_harmless_formatting_and_noncanonical_creation_offset_preserve_read_only_files(self) -> None:
        material_id = self.build()
        material = self.service.get(material_id, require_current=False)
        alias = "2029-12-31T19:00:00-05:00"
        self.assertEqual(datetime.fromisoformat(alias), MATERIAL_AT)
        material["manifest"]["created_at"] = alias
        def formatted(value):
            return json.dumps(value, indent=2, ensure_ascii=True).replace("schema", "\\u0073chema")
        self.update_material(material_id, {"created_at": alias,
            "structure_json": formatted(material["structure"]), "manifest_json": formatted(material["manifest"]),
            "validation_json": formatted(material["validation"]),
            "bundle_sha256": digest({key: material[key] for key in ("structure", "manifest", "validation")})})
        workflow = self.workflow(material_id)
        self.update_workflow(material_id, {"created_at": alias, "started_at": alias, "finished_at": alias,
            "input_json": formatted(json.loads(workflow["input_json"])),
            "completed_steps_json": '[ "validate", "persist" ]',
            "generated_artifacts_json": formatted([material_id])})
        image = self.repository._connection.serialize()
        with tempfile.TemporaryDirectory(prefix="gapply-fictional-build-history-") as temporary:
            database = Path(temporary) / "synthetic.db"
            database.write_bytes(image)
            database.chmod(0o600)
            before = (database.read_bytes(), database.stat().st_mtime_ns, sorted(path.name for path in database.parent.iterdir()))
            with SQLiteRepository(database, read_only=True) as repository:
                service = MaterialService(repository, RecordingRenderer())
                self.assertIsNone(service.validate_historical_facts(material_id))
            self.assertEqual((database.read_bytes(), database.stat().st_mtime_ns,
                sorted(path.name for path in database.parent.iterdir())), before)
        self.assertEqual(self.repository._connection.serialize(), image)

    def test_one_bundle_pdf_profile_and_transaction_without_current_resolution(self) -> None:
        material_id = self.build()
        trace, profile_states = [], []
        original_profile = ProfileService.validated_profile
        def profile_read(profile, *, apply_retirements=True):
            profile_states.append((apply_retirements, self.repository._connection.in_transaction))
            return original_profile(profile, apply_retirements=apply_retirements)
        self.repository._connection.set_trace_callback(trace.append)
        before = self.repository._connection.serialize()
        with patch.object(self.service, "get", wraps=self.service.get) as get, \
             patch.object(self.renderer, "validate", wraps=self.renderer.validate) as pdf, \
             patch.object(self.renderer, "render", side_effect=AssertionError("No new rendering")), \
             patch.object(self.service, "plan", side_effect=AssertionError("No current planning")), \
             patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("No current answers")), \
             patch.object(ProfileService, "validated_profile", autospec=True, side_effect=profile_read):
            self.assertIsNone(self.service.validate_historical_facts(material_id))
        get.assert_called_once_with(material_id, require_current=False)
        self.assertEqual(pdf.call_count, 1)
        self.assertEqual(profile_states, [(False, True)])
        self.assertEqual([sql for sql in trace if sql in {"BEGIN", "ROLLBACK"}], ["BEGIN", "ROLLBACK"])
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_later_retirement_keeps_history_and_current_blocking_precedence(self) -> None:
        material_id = self.build()
        self.retire(self.claim_ids[0])
        self.assertIsNone(self.service.validate_historical_facts(material_id))
        workflow = self.workflow(material_id)
        with self.workflow_patch(workflow, {**workflow, "model_name": "fictional-unrecorded-model"}):
            with self.assertRaises(MaterialBlocked):
                self.service.get(material_id)
            self.assert_rejected(material_id)
            # Record-only absence does not become a creation-facts audit.
            self.assertIsNone(self.service.validate_historical_approval_record(material_id))

    def test_existing_approval_and_use_audits_inherit_strict_build_record_custody(self) -> None:
        material_id = self.build()
        self.approve(material_id)
        workflow = self.workflow(material_id)
        for method, kwargs, message in (("validate_historical_approval_facts", {}, "Material historical approval facts failed integrity checks"),
            ("validate_historical_approval_eligibility", {}, "Material historical approval eligibility failed integrity checks"),
            ("validate_historical_use", {"used_at": (MATERIAL_AT + timedelta(days=3)).isoformat()}, "Material historical use failed integrity checks")):
            with self.subTest(method=method), self.workflow_patch(workflow, {**workflow, "model_name": "fictional-unrecorded-model"}), \
                 self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{message}$"):
                getattr(self.service, method)(material_id, **kwargs)

    def test_borrowed_caller_work_survives_success_and_corrupt_build_failure(self) -> None:
        material_id = self.build()
        before = self.repository._connection.serialize()
        abort = RuntimeError("fictional caller rollback")
        with self.assertRaises(RuntimeError) as raised:
            with self.repository.transaction():
                marker = self.repository.add_workflow_run(workflow_type="fictional-caller", status="queued")
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                self.update_workflow(material_id, {"model_name": "fictional-unrecorded-model"})
                with self.assertRaisesRegex(MaterialHistoryIntegrityError, f"^{ERROR}$"):
                    self.service.validate_historical_facts(material_id)
                self.assertTrue(self.repository._connection.in_transaction)
                self.assertIsNotNone(self.repository.get_workflow_run(marker["id"]))
                raise abort
        self.assertIs(raised.exception, abort)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_build_validator_failures_are_fixed_and_interrupts_preserve_identity(self) -> None:
        material_id = self.build()
        target = "grounded_apply.services.material_build_history.validate_material_build_record"
        for failure in (ValueError("fictional private record"), sqlite3.OperationalError("fictional private SQL"),
                        RecursionError("fictional decoder depth")):
            with self.subTest(failure=type(failure).__name__), patch(target, side_effect=failure):
                self.assert_rejected(material_id)
        sentinel = KeyboardInterrupt("fictional build audit interruption")
        before = self.repository._connection.serialize()
        with patch(target, side_effect=sentinel), self.assertRaises(KeyboardInterrupt) as raised:
            self.service.validate_historical_facts(material_id)
        self.assertIs(raised.exception, sentinel)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
