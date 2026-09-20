from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.repositories.backup_files import LocalBackupStorage
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.batches import (
    BatchCapacityError, BatchIntegrityError, BatchLeaseActiveError, BatchLeaseLostError,
    BatchService, CHECKPOINT_RESERVE_BYTES, _answer_blockers, _blockers, validate_batch_manifest,
)
from grounded_apply.services.jobs import JobService
from grounded_apply.services.material_models import MaterialValidationError, RenderedResume, ResumeStructure
from grounded_apply.services.materials import MaterialDependencyError, MaterialService
from grounded_apply.services.profile import CreateProfileReviewDecision, ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.resume_extraction import extract_resume
from grounded_apply.services.workflow import digest
from tests.test_jobs import JOB_TEXT
from tests.test_materials import SyntheticRenderer, approved_fixture
from tests.test_resume_extraction import RESUME


class RecordingRenderer:
    def __init__(self) -> None:
        self.built: list[str] = []
        self.failures: dict[str, str] = {}
        self.on_render = None
        self.padding = 0

    def _result(self, structure: ResumeStructure) -> RenderedResume:
        value = SyntheticRenderer().render(structure)
        return replace(value, pdf=value.pdf + b" " * self.padding)

    def render(self, structure: ResumeStructure) -> RenderedResume:
        self.built.append(structure.job_id)
        if self.on_render is not None:
            self.on_render(structure)
        if structure.job_id in self.failures:
            raise MaterialValidationError(self.failures[structure.job_id])
        return self._result(structure)

    def validate(self, structure: ResumeStructure, rendered: RenderedResume) -> None:
        if rendered != self._result(structure):
            raise MaterialValidationError("Synthetic material changed")


class BatchTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-batches-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / "synthetic.db"
        self.repository = SQLiteRepository(self.database).initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.renderer = RecordingRenderer()
        self.materials = MaterialService(self.repository, self.renderer)
        self.service = BatchService(self.repository, self.materials)

    def manifest(self, count: int = 1) -> dict[str, object]:
        jobs = [{"job_id": self.job_id}]
        for index in range(1, count):
            job = JobService(self.repository).add(f"https://example.com/jobs/fictional-{index}", JOB_TEXT,
                idempotency_key=f"synthetic-job-{index}")
            jobs.append({"job_id": job["job_id"]})
        return {"schema_version": 1, "claim_ids": list(self.claim_ids), "jobs": jobs}

    def ten_jobs(self) -> dict[str, object]:
        spec = self.manifest(10)
        spec["jobs"][1]["claim_ids"] = ["fictional-missing-claim"]
        spec["jobs"][5]["questions"] = [{"id": "fictional-eligibility", "text": "Do you need sponsorship?",
            "claim_ids": [], "required": True}]
        spec["jobs"][5]["questionnaire_coverage"] = "provided"
        return spec

    def create(self, spec: object | None = None, key: str = "synthetic-batch") -> str:
        return self.service.create(self.manifest() if spec is None else spec, idempotency_key=key)["batch_id"]

    def test_manifest_is_closed_typed_bounded_and_resolves_overrides(self) -> None:
        spec = self.manifest()
        spec["jobs"][0]["questionnaire_coverage"] = "provided"
        normalized = validate_batch_manifest(spec)
        self.assertEqual(normalized["jobs"][0]["claim_ids"], spec["claim_ids"])
        self.assertEqual(normalized["jobs"][0]["layout"], {"schema_version": 1, "presentations": {}})
        self.assertEqual(normalized, validate_batch_manifest(normalized))
        normalized["jobs"][0]["claim_ids"].clear()
        self.assertTrue(spec["claim_ids"])
        invalid = (None, {}, {**spec, "schema_version": True}, {**spec, "prose": "invented"},
            {**spec, "claim_ids": []}, {**spec, "claim_ids": [True]}, {**spec, "jobs": []},
            {**spec, "jobs": spec["jobs"] * 51}, {**spec, "jobs": spec["jobs"] * 2},
            {**spec, "questionnaire_coverage": "complete"}, {**spec, "layout": {"schema_version": 1, "presentations": {"unselected": "heading"}}},
            {**spec, "jobs": [{"job_id": self.job_id, "text": "Invented candidate fact"}]})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_batch_manifest(value)

    def test_plan_is_read_only_and_does_not_call_renderer(self) -> None:
        spec = self.ten_jobs()
        before = self.database.read_bytes()
        result = self.service.create(spec, idempotency_key="preview", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertIsNone(result["batch_id"])
        self.assertEqual(result["counts"], {"total": 10, "queued": 8, "building": 0, "draft": 0, "blocked": 2})
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.renderer.built, [])
        self.assertEqual(self.repository.list_preparation_batches(), [])

    def test_ten_jobs_prepare_eight_drafts_and_two_isolated_blockers(self) -> None:
        batch_id = self.create(self.ten_jobs())
        result = self.service.run(batch_id)
        self.assertEqual(result["status"], "waiting_for_input")
        self.assertEqual(result["counts"], {"total": 10, "queued": 0, "building": 0, "draft": 8, "blocked": 2})
        self.assertEqual(result["remaining_count"], 0)
        self.assertEqual(len(self.repository.list_material_ids()), 9)
        self.assertEqual(len(self.renderer.built), 9)
        # Missing claim and missing usable career evidence are separate facts;
        # the required sensitive answer is a third distinct actionable reason.
        self.assertEqual(len(result["grouped_blockers"]), 3)
        partial = result["items"][5]
        self.assertEqual(partial["status"], "blocked")
        self.assertIsNotNone(partial["material_id"])
        self.assertTrue(partial["currently_valid"])
        self.assertEqual(partial["questionnaire_coverage"], "provided")
        self.assertEqual(result["items"][0]["questionnaire_coverage"], "unknown")
        self.assertFalse(result["external_action_taken"])
        self.assertFalse(result["approvals_recorded"])
        self.assertFalse(result["application_ready"])
        self.assertEqual(self.repository.list_applications(), [])
        self.assertTrue(all(self.repository.get_material_approval(identifier) is None for identifier in self.repository.list_material_ids()))

    def test_create_and_cross_batch_material_replays_do_not_duplicate_outputs(self) -> None:
        spec = self.manifest(2)
        batch_id = self.create(spec)
        first = self.service.run(batch_id)
        self.assertTrue(self.service.create(spec, idempotency_key="synthetic-batch")["replayed"])
        second_id = self.create(spec, key="another-kickoff")
        second = self.service.run(second_id)
        self.assertEqual([item["material_id"] for item in first["items"]], [item["material_id"] for item in second["items"]])
        self.assertEqual(len(self.renderer.built), 2)
        changed = {**spec, "claim_ids": list(reversed(self.claim_ids))}
        with self.assertRaises(RepositoryError):
            self.service.create(changed, idempotency_key="synthetic-batch")
        self.assertEqual(len(self.repository.list_preparation_batches()), 2)

    def test_item_and_time_budgets_checkpoint_remaining_work(self) -> None:
        batch_id = self.create(self.manifest(3))
        first = self.service.run(batch_id, max_items=1)
        self.assertEqual(first["stop_reason"], "item_budget")
        self.assertEqual(first["status"], "budget_exhausted")
        self.assertEqual(first["remaining_count"], 2)
        clock = iter((0.0, 2.0))
        timed = BatchService(self.repository, self.materials, monotonic=lambda: next(clock))
        paused = timed.run(batch_id, max_seconds=1)
        self.assertEqual(paused["stop_reason"], "time_budget")
        self.assertEqual(paused["remaining_count"], 2)
        resumed = self.service.run(batch_id)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(len(self.repository.list_material_ids()), 3)
        self.assertEqual(first["items"][0]["material_id"], resumed["items"][0]["material_id"])

    def test_prior_exact_bundle_approval_survives_reuse_without_new_review_request(self) -> None:
        spec = self.manifest()
        first = self.service.run(self.create(spec))
        item = first["items"][0]
        self.materials.approve(item["material_id"], bundle_sha256=item["bundle_sha256"],
            actor_id="synthetic-user", idempotency_key="synthetic-approved", confirm=True)
        second = self.service.run(self.create(spec, key="another-kickoff"))
        reused = second["items"][0]
        self.assertEqual(reused["material_id"], item["material_id"])
        self.assertTrue(reused["material_approved"])
        self.assertFalse(reused["requires_approval"])
        self.assertFalse(reused["visual_review_required"])
        self.assertFalse(second["approvals_recorded"])
        self.assertEqual(reused["source_url"], "https://example.com/jobs/engineer")
        self.assertIsNone(reused["title"])

    def test_crash_after_child_commit_before_parent_checkpoint_reconciles_exact_child(self) -> None:
        batch_id = self.create()
        real_checkpoint = self.service._checkpoint
        def interrupted(batch, item, state, owner, epoch):
            if state["stage"] == "draft":
                raise RuntimeError("synthetic interrupted parent checkpoint")
            return real_checkpoint(batch, item, state, owner, epoch)
        with patch.object(self.service, "_checkpoint", side_effect=interrupted), self.assertRaises(RuntimeError):
            self.service.run(batch_id)
        material_ids = self.repository.list_material_ids()
        self.assertEqual(len(material_ids), 1)
        self.assertEqual(self.service.get(batch_id)["items"][0]["status"], "building")
        resumed = self.service.run(batch_id)
        self.assertEqual(resumed["items"][0]["material_id"], material_ids[0])
        self.assertEqual(self.repository.list_material_ids(), material_ids)
        self.assertEqual(len(self.renderer.built), 1)

    def test_crash_before_child_build_resumes_and_shared_errors_are_not_need_info(self) -> None:
        batch_id = self.create()
        with patch.object(self.materials, "build", side_effect=RepositoryError("synthetic integrity failure")), self.assertRaises(RepositoryError):
            self.service.run(batch_id)
        failed = self.service.get(batch_id)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["items"][0]["blockers"], [])
        self.assertEqual(self.repository.list_material_ids(), ())
        self.assertEqual(self.service.run(batch_id)["status"], "completed")

    def test_retired_evidence_blocks_current_reuse_and_read_only_show_keeps_history(self) -> None:
        batch_id = self.create()
        first = self.service.run(batch_id)
        identifier = first["items"][0]["material_id"]
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire")
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire", confirm=True, preview_token=preview.preview_token)
        before = self.database.read_bytes()
        stale = self.service.get(batch_id)
        self.assertEqual(stale["items"][0]["status"], "blocked")
        self.assertFalse(stale["items"][0]["currently_valid"])
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.service.run(batch_id)["status"], "waiting_for_input")
        self.assertEqual(self.repository.list_material_ids(), (identifier,))
        self.assertEqual(len(self.renderer.built), 1)

    def test_changed_preparation_fingerprint_cannot_silently_get_new_child_key(self) -> None:
        batch_id = self.create()
        with patch.object(self.materials, "build", side_effect=RuntimeError("synthetic crash")), self.assertRaises(RuntimeError):
            self.service.run(batch_id)
        original = self.service._preflight
        def changed(spec):
            structure, answers, fingerprint = original(spec)
            return structure, answers, "0" * 64
        with patch.object(self.service, "_preflight", side_effect=changed):
            result = self.service.run(batch_id)
        self.assertEqual(result["items"][0]["blockers"][0]["reason"], "preparation_inputs_changed")
        self.assertEqual(self.repository.list_material_ids(), ())

    def test_new_same_value_approved_evidence_between_preflight_and_build_blocks_without_child(self) -> None:
        batch_id = self.create()
        original_build = self.materials.build
        def approve_duplicate_identity_then_build(*args, **kwargs):
            profile = ProfileService(self.repository)
            request = extract_resume(RESUME).selected_request((0, 1), RESUME, "synthetic-duplicate-identity")
            imported = profile.create_import_proposal(request)
            imported_ids = {claim.id for claim in imported.claims}
            for item in profile.list_review_items():
                if item.claim.id in imported_ids:
                    profile.decide_review_item(CreateProfileReviewDecision(claim_id=item.claim.id,
                        review_token=item.review_token, decision=ApprovalStatus.APPROVED,
                        actor_id="synthetic-user", idempotency_key="duplicate-" + item.claim.id))
            return original_build(*args, **kwargs)
        with patch.object(self.materials, "build", side_effect=approve_duplicate_identity_then_build):
            result = self.service.run(batch_id)
        self.assertEqual(result["status"], "waiting_for_input")
        self.assertEqual(result["items"][0]["blockers"][0]["reason"], "preparation_inputs_changed")
        self.assertEqual(self.renderer.built, [])
        self.assertEqual(self.repository.list_material_ids(), ())
        self.assertEqual(self.service.get(batch_id)["status"], "waiting_for_input")
        self.assertEqual(self.service.run(batch_id)["items"][0]["blockers"][0]["reason"], "preparation_inputs_changed")
        fresh = self.service.run(self.create(key="synthetic-fresh-evidence"))
        self.assertEqual(fresh["status"], "completed")
        self.assertEqual(len(self.repository.list_material_ids()), 1)

    def test_expected_material_plan_digest_requires_exact_hash_shape(self) -> None:
        for expected in (True, 123, "0" * 63, "A" * 64):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.materials.build(self.job_id, self.claim_ids, idempotency_key="synthetic-invalid-plan",
                    expected_plan_sha256=expected)
        self.assertEqual(self.renderer.built, [])
        self.assertEqual(self.repository.list_material_ids(), ())

    def test_rendering_has_no_write_transaction_and_active_overlap_is_refused(self) -> None:
        batch_id = self.create()
        def during_render(structure):
            self.assertFalse(self.repository._connection.in_transaction)
            with SQLiteRepository(self.database, existing_only=True) as other:
                with other.transaction():
                    self.assertEqual(other.get_job_snapshot(structure.job_id)["id"], structure.job_id)
                second = BatchService(other, MaterialService(other, RecordingRenderer()))
                with self.assertRaises(BatchLeaseActiveError):
                    second.run(batch_id)
        self.renderer.on_render = during_render
        result = self.service.run(batch_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.renderer.built), 1)

    def test_expired_lease_can_be_reclaimed_and_old_owner_cannot_checkpoint(self) -> None:
        batch_id = self.create()
        now = datetime.now(UTC)
        owner = "synthetic-expired-owner"
        with self.repository.transaction():
            epoch = self.repository.acquire_preparation_lease(batch_id, expected_epoch=0, owner=owner,
                now=now.isoformat(timespec="microseconds"), expires_at=(now - timedelta(seconds=1)).isoformat(timespec="microseconds"))
        result = self.service.run(batch_id)
        record = self.repository.list_preparation_items(batch_id)[0]
        state, _ = self.service._item_state(record["id"])
        with self.assertRaises(BatchLeaseLostError):
            self.service._checkpoint(batch_id, record["id"], state, owner, epoch)
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(self.repository.get_preparation_lease(batch_id)["owner"])

    def test_expired_renderer_cannot_commit_after_another_owner_finishes(self) -> None:
        moment = [datetime.now(UTC)]
        service = BatchService(self.repository, self.materials, clock=lambda: moment[0])
        batch_id = service.create(self.manifest(), idempotency_key="synthetic-fenced")['batch_id']
        def during_render(structure):
            moment[0] += timedelta(seconds=1000)
            with SQLiteRepository(self.database, existing_only=True) as other:
                second = BatchService(other, MaterialService(other, RecordingRenderer()), clock=lambda: moment[0])
                self.assertEqual(second.run(batch_id, max_seconds=1)["status"], "completed")
        self.renderer.on_render = during_render
        with self.assertRaises(BatchLeaseLostError):
            service.run(batch_id, max_seconds=1)
        self.assertEqual(service.get(batch_id)["status"], "completed")
        self.assertEqual(len(self.repository.list_material_ids()), 1)

    def test_one_render_failure_blocks_only_its_item_and_retains_fixed_remediation(self) -> None:
        spec = self.manifest(2)
        self.renderer.failures[self.job_id] = "Resume layout overflows; shorten the selected content"
        result = self.service.run(self.create(spec))
        self.assertEqual(result["counts"]["draft"], 1)
        self.assertEqual(result["counts"]["blocked"], 1)
        self.assertEqual(result["items"][0]["blockers"][0]["reason"], "layout_overflow")

    def test_missing_renderer_is_a_shared_failure_instead_of_fifty_job_blockers(self) -> None:
        spec = self.manifest(2)
        self.renderer.failures[self.job_id] = "PDF generation requires a local pdflatex installation"
        batch_id = self.create(spec)
        with self.assertRaises(MaterialDependencyError):
            self.service.run(batch_id)
        result = self.service.get(batch_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["counts"]["blocked"], 0)
        self.assertEqual(len(self.renderer.built), 1)

    def test_persisted_material_corruption_is_not_downgraded_to_item_blocker(self) -> None:
        batch_id = self.create()
        result = self.service.run(batch_id)
        material = self.repository.get_material_version(result["items"][0]["material_id"])
        with self.repository.transaction():
            self.repository.update_workflow_run(material["workflow_run_id"], generated_artifacts=["wrong-material"])
        with self.assertRaises(MaterialValidationError):
            self.service.run(batch_id)
        self.assertIsNone(self.repository.get_preparation_lease(batch_id)["owner"])
        self.assertEqual(len(self.repository.list_material_ids()), 1)

    def test_batch_creation_capacity_failure_rolls_back_request_items_and_audit(self) -> None:
        workflows = self.repository.list_workflow_runs()
        with patch("grounded_apply.services.batches.MAX_SNAPSHOT_BYTES", CHECKPOINT_RESERVE_BYTES), self.assertRaises(BatchCapacityError):
            self.create()
        self.assertEqual(self.repository.list_preparation_batches(), [])
        self.assertEqual(self.repository.list_workflow_runs(), workflows)

    def test_child_capacity_failure_rolls_back_material_and_keeps_resumable_checkpoint(self) -> None:
        batch_id = self.create()
        self.renderer.padding = 128 * 1024
        allowance = self.repository.database_size_bytes() + CHECKPOINT_RESERVE_BYTES
        with patch("grounded_apply.services.batches.MAX_SNAPSHOT_BYTES", allowance):
            result = self.service.run(batch_id)
        self.assertEqual(result["stop_reason"], "capacity_reached")
        self.assertEqual(result["items"][0]["status"], "building")
        self.assertEqual(self.repository.list_material_ids(), ())
        self.assertEqual(self.service.run(batch_id)["status"], "completed")

    def test_checkpoint_capacity_failure_after_child_commit_does_not_lose_material(self) -> None:
        batch_id = self.create()
        original = self.service._checkpoint
        def capacity(batch, item, state, owner, epoch):
            if state["stage"] == "draft":
                raise BatchCapacityError("synthetic parent capacity")
            return original(batch, item, state, owner, epoch)
        with patch.object(self.service, "_checkpoint", side_effect=capacity):
            stopped = self.service.run(batch_id)
        self.assertEqual(stopped["stop_reason"], "capacity_reached")
        identifiers = self.repository.list_material_ids()
        self.assertEqual(len(identifiers), 1)
        self.assertEqual(self.service.run(batch_id)["items"][0]["material_id"], identifiers[0])
        self.assertEqual(len(self.renderer.built), 1)

    def test_identical_missing_fact_blockers_group_without_copying_claim_values(self) -> None:
        spec = self.manifest(2)
        spec["claim_ids"] = ["fictional-missing-claim"]
        result = self.service.run(self.create(spec))
        self.assertEqual(result["counts"]["blocked"], 2)
        self.assertEqual(len(result["grouped_blockers"]), 2)
        self.assertTrue(all(len(group["job_ids"]) == 2 for group in result["grouped_blockers"]))
        self.assertNotIn("conflicting_claims", json.dumps(result["grouped_blockers"]))

    def test_twenty_one_questions_with_ten_missing_claims_keep_partial_and_continue(self) -> None:
        spec = self.manifest(2)
        spec["jobs"][0]["questions"] = [{"id": f"fictional-question-{index}",
            "text": f"Describe your experience for fictional project {index}.",
            "claim_ids": [f"fictional-missing-{claim}" for claim in range(10)], "required": True}
            for index in range(21)]
        planned = self.service.plan(spec)
        self.assertEqual(planned["counts"]["blocked"], 1)
        batch_id = self.create(spec)
        result = self.service.run(batch_id)
        self.assertEqual(result["counts"]["blocked"], 1)
        self.assertEqual(result["counts"]["draft"], 1)
        self.assertEqual(len(result["items"][0]["blockers"]), 21)
        self.assertIsNotNone(result["items"][0]["material_id"])
        self.assertEqual(len(self.repository.list_material_ids()), 2)
        self.assertEqual(self.service.get(batch_id)["status"], "waiting_for_input")
        self.service.run(batch_id)
        self.assertEqual(len(self.repository.list_material_ids()), 2)

    def test_large_legitimate_blockers_summarize_per_question_and_remain_bounded(self) -> None:
        answers = [{"question_id": f"fictional-question-{question}", "question": f"Describe project {question} experience.",
            "required": True, "need_info": [{"kind": "need_info", "reason": "missing", "intent": "selected_claim",
                "question": "Review the requested evidence.", "related_claim_ids": [f"fictional-missing-{question}-{claim}"]}
                for claim in range(10)]} for question in range(50)]
        result = _answer_blockers(answers)
        self.assertEqual(len(result), 50)
        self.assertTrue(all(item["reason"] == "blocker_details_exceed_limit" for item in result))
        self.assertEqual({item["question_id"] for item in result}, {item["question_id"] for item in answers})
        self.assertEqual(_blockers(result), result)
        contradiction = {"kind": "contradiction", "related_claim_ids": [f"fictional-claim-{index}" for index in range(161)]}
        large = _blockers([contradiction])
        self.assertEqual(large[0]["reason"], "blocker_details_exceed_limit")
        self.assertEqual(_blockers(large), large)
        with self.assertRaises(BatchIntegrityError):
            _blockers([{"kind": "contradiction", "related_claim_ids": "malformed"}])

    def test_question_blockers_keep_distinct_context_and_group_identical_shared_questions(self) -> None:
        spec = self.manifest(2)
        spec["questions"] = [{"id": "fictional-sponsor", "text": "Do you require sponsorship?", "claim_ids": [], "required": True},
            {"id": "fictional-relocate", "text": "Are you willing to relocate?", "claim_ids": [], "required": True}]
        result = self.service.run(self.create(spec))
        groups = result["grouped_blockers"]
        self.assertEqual(len(groups), 2)
        self.assertEqual({group["question_id"] for group in groups}, {"fictional-sponsor", "fictional-relocate"})
        self.assertTrue(all(len(group["job_ids"]) == 2 for group in groups))
        for item in result["items"]:
            material = self.materials.get(item["material_id"])
            self.assertTrue(all(answer["answer"] is None and not answer["factual_units"] for answer in material["manifest"]["answers"]))
            self.assertIsNone(self.repository.get_material_approval(item["material_id"]))
        issue = {"kind": "need_info", "reason": "human_answer_required"}
        long = [{"question_id": "same-id", "question": "x" * 256 + suffix, "required": True, "need_info": [issue]}
            for suffix in ("a", "b")]
        labels = _answer_blockers(long)
        self.assertEqual(labels[0]["question_label"], labels[1]["question_label"])
        self.assertNotEqual(labels[0]["question_sha256"], labels[1]["question_sha256"])
        self.assertEqual(len(labels), 2)
        with self.assertRaises(BatchIntegrityError):
            _blockers([{**issue, "question_id": "missing-other-context"}])

    def test_rehashed_historical_question_context_must_match_immutable_spec(self) -> None:
        spec = self.manifest()
        spec["questions"] = [{"id": "fictional-sponsor", "text": "Do you require sponsorship?", "claim_ids": [], "required": True}]
        batch_id = self.create(spec)
        self.service.run(batch_id)
        self.service.run(batch_id)
        item = self.repository.list_preparation_items(batch_id)[0]
        events = self.repository.list_preparation_events(item["id"])
        first_blocked = next(index for index, event in enumerate(events) if json.loads(event["state_json"])["blockers"])
        self.assertLess(first_blocked, len(events) - 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER preparation_events_no_update")
            connection.commit()
            for field, value in (("question_id", "fictional-unknown"), ("question_label", "A changed label"),
                                 ("question_sha256", "0" * 64)):
                previous_hash = None
                with connection:
                    for position, event in enumerate(events):
                        state = json.loads(event["state_json"])
                        if position == first_blocked:
                            state["blockers"][0][field] = value
                        event_hash = digest({"item_id": item["id"], "position": position, "at": event["at"],
                            "state": state, "previous_sha256": previous_hash})
                        connection.execute("UPDATE preparation_batch_events SET state_json = ?, previous_sha256 = ?, event_sha256 = ? WHERE id = ?",
                            (json.dumps(state), previous_hash, event_hash, event["id"]))
                        previous_hash = event_hash
                with self.subTest(field=field):
                    with self.assertRaises(BatchIntegrityError):
                        self.service.get(batch_id)
                    with self.assertRaises(BatchIntegrityError):
                        self.service.run(batch_id)

    def test_immutable_request_and_event_chain_reject_sql_mutation(self) -> None:
        batch_id = self.create()
        with closing(sqlite3.connect(self.database)) as connection:
            for statement in ("UPDATE preparation_batches SET manifest_sha256 = 'changed'", "DELETE FROM preparation_batch_items",
                "UPDATE preparation_batch_events SET state_json = '{}'", "DELETE FROM preparation_batch_leases"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
        self.assertEqual(self.service.get(batch_id)["counts"]["queued"], 1)

    def test_corrupt_checkpoint_digest_fails_closed_before_render(self) -> None:
        batch_id = self.create()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP TRIGGER preparation_events_no_update")
            connection.execute("UPDATE preparation_batch_events SET event_sha256 = ?", ("0" * 64,))
        with self.assertRaises(BatchIntegrityError):
            self.service.run(batch_id)
        self.assertEqual(self.renderer.built, [])

    def test_batch_snapshot_backup_and_restore_preserve_resume_and_artifacts(self) -> None:
        batch_id = self.create(self.manifest(2))
        first = self.service.run(batch_id, max_items=1)
        with SQLiteRepository(self.database, read_only=True) as readonly:
            image = readonly.snapshot_bytes(max_bytes=16 * 1024 * 1024)
        validate_profile_snapshot(image)
        target = self.root / "restored"
        LocalBackupStorage().restore_profile(target, image, "a" * 64, confirm=True)
        with SQLiteRepository(target / "data" / "grounded_apply.db", existing_only=True) as restored:
            service = BatchService(restored, MaterialService(restored, RecordingRenderer()))
            result = service.run(batch_id)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["items"][0]["material_id"], first["items"][0]["material_id"])
            self.assertEqual(len(restored.list_material_ids()), 2)

    def test_readonly_get_and_list_validate_without_writing_or_rendering(self) -> None:
        batch_id = self.create()
        self.service.run(batch_id)
        before = self.database.read_bytes()
        count = len(self.renderer.built)
        with SQLiteRepository(self.database, read_only=True) as readonly:
            service = BatchService(readonly, MaterialService(readonly, self.renderer))
            self.assertEqual(service.get(batch_id)["status"], "completed")
            self.assertEqual(len(service.list()), 1)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(len(self.renderer.built), count)

    def test_invalid_budgets_never_acquire_lease(self) -> None:
        batch_id = self.create()
        for kwargs in ({"max_items": 0}, {"max_items": True}, {"max_items": 51}, {"max_seconds": 0}, {"max_seconds": 3601}):
            with self.assertRaises(ValueError):
                self.service.run(batch_id, **kwargs)
        self.assertEqual(self.repository.get_preparation_lease(batch_id)["epoch"], 0)

    def test_exhausted_interrupted_attempt_becomes_an_explicit_terminal_blocker(self) -> None:
        batch_id = self.create()
        item_id = self.repository.list_preparation_items(batch_id)[0]["id"]
        state, _ = self.service._item_state(item_id)
        blocker = {"kind": "need_info", "reason": "synthetic_retry", "intent": "batch_preparation",
            "question": "Review the synthetic fixture.", "related_claim_ids": []}
        with self.repository.transaction():
            for attempt in range(1, 101):
                state = {**state, "stage": "building", "attempts": attempt, "blockers": []}
                self.service._append_event(item_id, state)
                if attempt < 100:
                    state = {**state, "stage": "blocked", "blockers": [blocker]}
                    self.service._append_event(item_id, state)
        result = self.service.run(batch_id)
        self.assertEqual(result["status"], "waiting_for_input")
        self.assertEqual(result["items"][0]["blockers"][0]["reason"], "attempt_limit")
        self.assertEqual(result["items"][0]["attempts"], 100)
        self.assertEqual(self.renderer.built, [])
