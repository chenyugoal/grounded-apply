from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.batches import BatchIntegrityError, BatchSearchHistoryItem, BatchService
from grounded_apply.services.jobs import JobService
from grounded_apply.services.material_models import MaterialValidationError
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.questionnaires import QuestionnaireService
from grounded_apply.services.resume_extraction import extract_resume
from grounded_apply.services.workflow import canonical, digest
from tests.test_batches import RecordingRenderer
from tests.test_jobs import JOB_TEXT
from tests.test_materials import approved_fixture
from tests.test_resume_extraction import RESUME


class BatchSearchHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gapply-synthetic-history-")
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "synthetic.db"
        self.repository = SQLiteRepository(self.database).initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.renderer = RecordingRenderer()
        self.materials = MaterialService(self.repository, self.renderer)
        self.service = BatchService(self.repository, self.materials)

    def create(self, count: int = 2, *, partial: bool = False, run: bool = True) -> tuple[str, list[str]]:
        ids = [self.job_id]
        for index in range(1, count):
            ids.append(JobService(self.repository).add(f"https://example.com/history/{index}", JOB_TEXT,
                idempotency_key=f"synthetic-history-{index}")["job_id"])
        spec = {"schema_version": 1, "claim_ids": list(self.claim_ids), "jobs": [{"job_id": identifier} for identifier in ids]}
        if partial:
            spec["jobs"][0]["questions"] = [{"id": "fictional-sponsorship", "text": "Do you need sponsorship?",
                "claim_ids": [], "required": True}]
        batch_id = self.service.create(spec, idempotency_key="synthetic-history-batch")["batch_id"]
        if run:
            self.service.run(batch_id)
        return batch_id, ids

    def material_id(self, batch_id: str, index: int = 0) -> str:
        item = self.repository.list_preparation_items(batch_id)[index]
        return json.loads(self.repository.list_preparation_events(item["id"])[-1]["state_json"])["material_id"]

    def rewrite_events(self, batch_id: str, change, index: int = 0) -> None:
        """Intentionally forge isolated fixture storage to test custody checks."""
        item = self.repository.list_preparation_items(batch_id)[index]
        events = self.repository.list_preparation_events(item["id"])
        with self.repository.transaction():
            self.repository._connection.execute("DROP TRIGGER preparation_events_no_update")
            previous = None
            for event in events:
                state = json.loads(event["state_json"])
                change(state)
                value = digest({"item_id": item["id"], "position": event["position"], "at": event["at"],
                    "state": state, "previous_sha256": previous})
                self.repository._connection.execute(
                    "UPDATE preparation_batch_events SET state_json = ?, previous_sha256 = ?, event_sha256 = ? WHERE id = ?",
                    (canonical(state), previous, value, event["id"]))
                previous = value

    def corrupt_approval(self, material_id: str) -> None:
        material = self.repository.get_material_version(material_id)
        with self.repository.transaction():
            self.repository.insert_material_approval(material_id=material_id, bundle_sha256=material["bundle_sha256"],
                actor_id="synthetic-user", approved_at=material["created_at"], workflow_run_id=material["workflow_run_id"])

    def test_unmatched_history_audits_every_pdf_and_profile_without_current_resolution(self) -> None:
        batch_id, ids = self.create(3)
        before, built = self.database.read_bytes(), tuple(self.renderer.built)
        real_profile = ProfileService.validated_profile
        with patch.object(ProfileService, "validated_profile", autospec=True, side_effect=real_profile) as profile, \
             patch.object(self.materials, "plan", side_effect=AssertionError("Unexpected current plan")), \
             patch.object(QuestionnaireService, "prepare", side_effect=AssertionError("Unexpected current answers")), \
             patch.object(self.renderer, "validate", wraps=self.renderer.validate) as validate:
            result = self.service.search_history(batch_id, current_job_ids=frozenset())
        self.assertEqual(result, tuple(BatchSearchHistoryItem(identifier, 1, False) for identifier in ids))
        self.assertEqual(profile.call_count, 1)
        self.assertEqual(validate.call_count, 3)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(tuple(self.renderer.built), built)
        with self.assertRaises(FrozenInstanceError):
            result[0].reusable = True
        self.assertFalse(hasattr(result[0], "ready"))

    def test_only_matching_draft_resolves_current_facts_and_normal_review_remains_full(self) -> None:
        batch_id, ids = self.create(3)
        real_questions = QuestionnaireService.prepare
        with patch.object(self.materials, "plan", wraps=self.materials.plan) as plan, \
             patch.object(QuestionnaireService, "prepare", autospec=True, side_effect=real_questions) as questions:
            result = self.service.search_history(batch_id, current_job_ids=frozenset({ids[1]}))
            self.assertEqual([item.reusable for item in result], [False, True, False])
            self.assertEqual([call.args[0] for call in plan.call_args_list], [ids[1]])
            self.assertEqual([call.args[1] for call in questions.call_args_list], [ids[1]])
            plan.reset_mock()
            questions.reset_mock()
            self.assertEqual(self.service.get(batch_id)["counts"]["draft"], 3)
            self.assertEqual([call.args[0] for call in plan.call_args_list], ids)
            self.assertEqual([call.args[1] for call in questions.call_args_list], ids)

    def test_retirement_is_rechecked_without_cross_call_authority_cache(self) -> None:
        batch_id, ids = self.create(1)
        self.assertTrue(self.service.search_history(batch_id, current_job_ids=frozenset(ids))[0].reusable)
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire")
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire",
            confirm=True, preview_token=preview.preview_token)
        self.assertFalse(self.service.search_history(batch_id, current_job_ids=frozenset(ids))[0].reusable)
        self.assertEqual(self.service.get(batch_id)["counts"]["blocked"], 1)

    def test_unrelated_import_corruption_fails_even_without_candidate_matches(self) -> None:
        batch_id, _ = self.create(1)
        source = RESUME.replace("Avery Quill", "Morgan Fable")
        result = ProfileService(self.repository).create_import_proposal(
            extract_resume(source).selected_request((0,), source, "unrelated-synthetic-import"))
        association = self.repository.get_profile_import_review_item(result.claims[0].id)
        with self.repository.transaction():
            self.repository.update_workflow_run(association["import_workflow_run_id"], generated_artifacts=["wrong-import"])
        with self.assertRaises(RepositoryError):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_unmatched_pdf_corruption_is_not_hidden(self) -> None:
        batch_id, _ = self.create(1)
        with self.repository.transaction():
            self.repository._connection.execute("DROP TRIGGER materials_no_update")
            self.repository._connection.execute("UPDATE material_versions SET pdf_bytes = ?", (b"synthetic-corrupt-pdf",))
        with self.assertRaises(MaterialValidationError):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_unmatched_material_workflow_corruption_is_not_hidden(self) -> None:
        batch_id, _ = self.create(1)
        material = self.repository.get_material_version(self.material_id(batch_id))
        with self.repository.transaction():
            self.repository.update_workflow_run(material["workflow_run_id"], generated_artifacts=["wrong-material"])
        with self.assertRaises(MaterialValidationError):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_valid_other_job_material_cannot_be_rebound_with_rehashed_events(self) -> None:
        batch_id, _ = self.create(2)
        other = self.repository.list_preparation_items(batch_id)[1]
        other_state = json.loads(self.repository.list_preparation_events(other["id"])[-1]["state_json"])
        def swap(state):
            for field in ("fingerprint", "child_key", "material_id", "bundle_sha256"):
                if state[field] is not None:
                    state[field] = other_state[field]
        self.rewrite_events(batch_id, swap)
        with self.assertRaises(BatchIntegrityError):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_unmatched_approval_is_audited_even_after_claim_retirement(self) -> None:
        batch_id, _ = self.create(1)
        material_id = self.material_id(batch_id)
        self.corrupt_approval(material_id)
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire")
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire",
            confirm=True, preview_token=preview.preview_token)
        with self.assertRaises(RepositoryError):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_partial_package_cannot_hide_corrupt_approval_and_is_never_reusable(self) -> None:
        batch_id, ids = self.create(1, partial=True)
        material_id = self.material_id(batch_id)
        self.assertFalse(self.materials.is_approved(material_id, require_current=False))
        self.assertFalse(self.service.search_history(batch_id, current_job_ids=frozenset(ids))[0].reusable)
        self.corrupt_approval(material_id)
        with self.assertRaises(RepositoryError):
            self.materials.is_approved(material_id, require_current=False)
        with self.assertRaises(RepositoryError):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_rehashed_partial_package_draft_checkpoint_fails_even_when_unmatched(self) -> None:
        batch_id, _ = self.create(1, partial=True)
        def conceal(state):
            if state["stage"] == "blocked":
                state.update(stage="draft", blockers=[])
        self.rewrite_events(batch_id, conceal)
        with self.assertRaisesRegex(BatchIntegrityError, "required unanswered"):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_rehashed_question_context_is_bound_to_spec_even_when_unmatched(self) -> None:
        batch_id, _ = self.create(1, partial=True)
        def wrong_context(state):
            for blocker in state["blockers"]:
                blocker["question_label"] = "A different synthetic question"
        self.rewrite_events(batch_id, wrong_context)
        with self.assertRaises(BatchIntegrityError):
            self.service.search_history(batch_id, current_job_ids=frozenset())

    def test_pending_batch_retains_attempts_without_requiring_profile_or_rendering(self) -> None:
        batch_id, ids = self.create(2, run=False)
        with patch.object(ProfileService, "validated_profile", side_effect=AssertionError("No material exists")):
            self.assertEqual(self.service.search_history(batch_id, current_job_ids=frozenset(ids)),
                tuple(BatchSearchHistoryItem(identifier, 0, False) for identifier in ids))
        self.assertEqual(self.renderer.built, [])

    def test_invalid_inputs_fail_before_repository_access(self) -> None:
        invalid = (None, [], set(), frozenset({True}), frozenset({"invalid/id"}), frozenset(str(i) for i in range(1001)))
        with patch.object(self.repository, "read_transaction", side_effect=AssertionError("Unexpected DB access")):
            for value in invalid:
                with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                    self.service.search_history("synthetic-batch", current_job_ids=value)
            with self.assertRaises(ValueError):
                self.service.search_history("invalid/id", current_job_ids=frozenset())


if __name__ == "__main__":
    unittest.main()
