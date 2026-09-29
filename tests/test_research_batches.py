from __future__ import annotations

import unittest
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus
from grounded_apply.services.profile import CreateProfileReviewDecision, ProfileService
from grounded_apply.services.resume_extraction import extract_resume
from tests import test_batches as batches


class ResearchBatchTests(unittest.TestCase):
    setUp = batches.BatchTests.setUp
    manifest = batches.BatchTests.manifest
    create = batches.BatchTests.create

    def add_research(self) -> str:
        source = "Research\nContributed fictional probe analysis; did not lead the study.\n"
        extracted = extract_resume(source, version=3)
        request = extracted.selected_request((0,), source, "fictional-research-addition", retain_all_facts=True)
        profile = ProfileService(self.repository)
        claim = profile.create_import_proposal(request).claims[0]
        item = next(item for item in profile.list_review_items() if item.claim.id == claim.id)
        profile.decide_review_item(CreateProfileReviewDecision(claim_id=claim.id,
            review_token=item.review_token, decision=ApprovalStatus.APPROVED,
            actor_id="synthetic-reviewer", idempotency_key="fictional-research-approval"))
        return claim.id

    def test_unselected_research_does_not_change_plan_or_approved_bundle_reuse(self) -> None:
        original_plan = self.materials.plan(self.job_id, self.claim_ids)
        first = self.service.run(self.create())
        item = first["items"][0]
        self.materials.approve(item["material_id"], bundle_sha256=item["bundle_sha256"],
            actor_id="synthetic-reviewer", idempotency_key="fictional-prior-approval", confirm=True)
        self.add_research()
        self.assertEqual(self.materials.plan(self.job_id, self.claim_ids), original_plan)
        self.assertEqual(original_plan.transformation, "approved_text_selection@2")
        second = self.service.run(self.create(key="fictional-after-research"))
        reused = second["items"][0]
        self.assertEqual(reused["material_id"], item["material_id"])
        self.assertTrue(reused["material_approved"])
        self.assertFalse(reused["requires_approval"])
        self.assertEqual(len(self.renderer.built), 1)

    def test_interrupted_nonresearch_batch_resumes_with_original_child_key(self) -> None:
        identifier = self.create()
        with patch.object(self.materials, "build", side_effect=RuntimeError("fictional interruption")) as build:
            with self.assertRaises(RuntimeError):
                self.service.run(identifier)
            original_key = build.call_args.kwargs["idempotency_key"]
            original_plan_hash = build.call_args.kwargs["expected_plan_sha256"]
        self.add_research()
        with patch.object(self.materials, "build", wraps=self.materials.build) as build:
            resumed = self.service.run(identifier)
            self.assertEqual(build.call_args.kwargs["idempotency_key"], original_key)
            self.assertEqual(build.call_args.kwargs["expected_plan_sha256"], original_plan_hash)
        self.assertEqual(resumed["status"], "completed")
        material = self.materials.get(resumed["items"][0]["material_id"])
        self.assertEqual(material["structure"]["transformation"], "approved_text_selection@2")

    def test_selected_research_uses_the_same_version_in_preflight_build_and_history(self) -> None:
        research = self.add_research()
        selected = (*self.claim_ids, research)
        specification = self.manifest()
        specification["claim_ids"] = list(selected)
        before = self.database.read_bytes()
        plan = self.materials.build(self.job_id, selected, idempotency_key="fictional-plan", dry_run=True)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(plan["structure"]["transformation"], "approved_text_selection@3")
        result = self.service.run(self.create(specification))
        self.assertEqual(result["status"], "completed")
        material = self.materials.get(result["items"][0]["material_id"])
        self.assertEqual(material["structure"], plan["structure"])
        self.assertEqual(next(unit for unit in material["structure"]["units"]
                              if unit["claim_id"] == research)["claim_type"], "research_description")
        before = self.database.read_bytes()
        self.service.validate_historical_inventory()
        self.assertEqual(self.database.read_bytes(), before)
        replay = self.service.run(self.create(specification, key="fictional-research-reuse"))
        self.assertEqual(replay["items"][0]["material_id"], material["id"])
        self.assertEqual(len(self.renderer.built), 1)

    def test_research_used_only_in_answers_does_not_upgrade_the_resume(self) -> None:
        research = self.add_research()
        questions = [{"id": "fictional-research-question", "text": "Describe your research contribution.",
                      "claim_ids": [research], "required": True}]
        result = self.materials.build(self.job_id, self.claim_ids,
            questions=questions, idempotency_key="fictional-answer-only-research")
        material = self.materials.get(result["material_id"])
        self.assertEqual(material["structure"]["transformation"], "approved_text_selection@2")
        self.assertNotIn(research, {unit["claim_id"] for unit in material["structure"]["units"]})
        answer = material["manifest"]["answers"][0]
        self.assertEqual(answer["status"], "draft")
        self.assertTrue(any(research in unit["claim_ids"] for unit in answer["factual_units"]))
        self.materials.validate_historical_inventory()


if __name__ == "__main__":
    unittest.main()
