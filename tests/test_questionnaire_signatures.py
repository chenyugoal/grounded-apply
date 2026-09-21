from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grounded_apply.config import resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.materials import MaterialBlocked, MaterialHistoryIntegrityError, MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService
from tests import test_cli, test_material_history
from tests.test_materials import SyntheticRenderer, approved_fixture


HUMAN_ANSWER = {
    "kind": "need_info", "reason": "human_answer_required",
    "question": "Answer this question yourself. No sensitive answer is inferred or stored.",
}


class QuestionnaireSignatureTests(unittest.TestCase):
    # Reuse the isolated forgery fixture without inheriting its test cases.
    rewrite_material = test_material_history.MaterialHistoryTests.rewrite_material

    def setUp(self) -> None:
        self.repository = SQLiteRepository(":memory:").initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.renderer = SyntheticRenderer()
        self.service = MaterialService(self.repository, self.renderer)
        self.questionnaires = QuestionnaireService(self.repository)

    def question(self, text: str, *, identifier: str = "fictional-question", required: bool = True) -> dict:
        return {"id": identifier, "text": text, "claim_ids": list(self.claim_ids[:2]), "required": required}

    def assert_human_answer(self, answer: dict) -> None:
        self.assertEqual(answer["status"], "need_info")
        self.assertIsNone(answer["answer"])
        self.assertEqual(answer["factual_units"], [])
        self.assertEqual(answer["need_info"], [HUMAN_ANSWER])
        self.assertIs(answer["human_review_required"], True)
        self.assertIs(answer["external_action_taken"], False)

    def test_explicit_sign_and_esign_requests_with_career_words_always_require_a_human(self) -> None:
        prompts = (
            "Please sign this statement about your experience.",
            "Sign below to confirm your project experience.",
            "Please SIGN your certification statement.",
            "Please e-sign this statement about your project experience.",
            "Please esign this statement about your project experience.",
            "Please e-sign language proficiency statements for this project.",
            "Please sign language proficiency statements for this project.",
            "You must sign language proficiency statements for this project.",
            "Please electronically sign this statement about your experience.",
            "Please sign off on your experience.",
            "Please sign on the dotted line about your experience.",
            "Please sign this statement about your single sign-on experience.",
            "Describe your single sign-on experience, then sign below.",
            "Describe your sign language skills, then sign this statement.",
        )
        questions = [self.question(text, identifier=f"fictional-sign-{index}", required=index % 2 == 0)
                     for index, text in enumerate(prompts)]
        before = self.repository._connection.serialize()
        answers = self.questionnaires.prepare(self.job_id, questions)
        for question, answer in zip(questions, answers, strict=True):
            with self.subTest(prompt=question["text"]):
                self.assertEqual(answer["question_id"], question["id"])
                self.assertEqual(answer["question"], question["text"])
                self.assertEqual(answer["required"], question["required"])
                self.assert_human_answer(answer)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertEqual(self.repository.list_material_ids(), ())
        self.assertFalse(self.repository._connection.in_transaction)

    def test_career_certification_sign_on_and_sign_language_questions_keep_exact_facts(self) -> None:
        prompts = (
            "Describe your certification experience.",
            "Describe your single sign-on project experience.",
            "Describe your single sign on project experience.",
            "Describe your SINGLE-SIGN-ON project experience.",
            "Describe your sign-on integrations experience.",
            "Describe your sign language skills.",
            "Please describe your sign language experience.",
            "Sign language skills: describe your experience.",
            "Sign language project experience?",
            "Describe your SIGN-LANGUAGE skills.",
            "Describe your design experience.",
            "Describe your signage project experience.",
        )
        questions = [self.question(text, identifier=f"fictional-career-{index}") for index, text in enumerate(prompts)]
        expected = [self.repository.get_claim(claim_id)["canonical_text"] for claim_id in self.claim_ids[:2]]
        before = self.repository._connection.serialize()
        for answer in self.questionnaires.prepare(self.job_id, questions):
            with self.subTest(prompt=answer["question"]):
                self.assertEqual(answer["status"], "draft")
                self.assertEqual(answer["answer"], "\n".join(expected))
                self.assertEqual([unit["text"] for unit in answer["factual_units"]], expected)
                self.assertEqual([unit["claim_ids"] for unit in answer["factual_units"]], [[claim_id] for claim_id in self.claim_ids[:2]])
                self.assertTrue(all(unit["evidence_ids"] for unit in answer["factual_units"]))
                self.assertEqual(answer["need_info"], [])
                self.assertIs(answer["human_review_required"], True)
                self.assertIs(answer["external_action_taken"], False)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertEqual(self.repository.list_material_ids(), ())

    def test_signature_gate_does_not_resolve_selected_facts(self) -> None:
        question = self.question("Please sign this statement about your experience.")
        before = self.repository._connection.serialize()
        with patch.object(ProfileService, "packets_for_claims", side_effect=AssertionError("A signature cannot resolve a factual answer")):
            answer, = self.questionnaires.prepare(self.job_id, [question])
        self.assert_human_answer(answer)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_required_signature_blocks_material_approval_without_recording_approval(self) -> None:
        result = self.service.build(self.job_id, self.claim_ids,
            questions=[self.question("Please sign this statement about your experience.")],
            idempotency_key="fictional-signature-material")
        material_id = result["material_id"]
        self.assertFalse(result["ready"])
        self.assert_human_answer(self.service.get(material_id)["manifest"]["answers"][0])
        before = self.repository._connection.serialize()
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), self.assertRaises(MaterialBlocked) as raised:
                self.service.approve(material_id, bundle_sha256=result["bundle_sha256"],
                    actor_id="fictional-reviewer", idempotency_key="fictional-signature-approval", confirm=confirmed)
            self.assertEqual(raised.exception.outcomes, [HUMAN_ANSWER])
            self.assertIsNone(self.repository.get_material_approval(material_id))
            self.assertFalse(self.service.is_approved(material_id))
            self.assertEqual(self.repository._connection.serialize(), before)

    def test_saved_unanswered_signature_has_valid_history_without_granting_readiness(self) -> None:
        result = self.service.build(self.job_id, self.claim_ids,
            questions=[self.question("Please e-sign this statement about your project experience.")],
            idempotency_key="fictional-signature-history")
        material_id = result["material_id"]
        self.assert_human_answer(self.service.get(material_id, require_current=False)["manifest"]["answers"][0])
        snapshot = self.repository._connection.serialize()
        with SQLiteRepository.from_snapshot(snapshot) as repository:
            historical = MaterialService(repository, SyntheticRenderer())
            self.assertIsNone(historical.validate_historical_facts(material_id))
            self.assertFalse(historical.is_approved(material_id, require_current=False))
            self.assertIsNone(repository.get_material_approval(material_id))
        self.assertEqual(self.repository._connection.serialize(), snapshot)

    def test_rehashed_old_style_signature_draft_fails_historical_factual_validation(self) -> None:
        result = self.service.build(self.job_id, self.claim_ids,
            questions=[self.question("Describe your project experience.")],
            idempotency_key="fictional-old-signature-draft")
        material_id = result["material_id"]
        self.assertIsNone(self.service.validate_historical_facts(material_id))

        def forge(manifest: dict) -> None:
            text = "Please sign this statement about your experience."
            manifest["question_specs"][0]["text"] = text
            manifest["answers"][0]["question"] = text
            self.assertEqual(manifest["answers"][0]["status"], "draft")

        self.rewrite_material(material_id, edit_manifest=forge)
        before = self.repository._connection.serialize()
        with self.assertRaises(MaterialHistoryIntegrityError):
            self.service.validate_historical_facts(material_id)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertIsNone(self.repository.get_material_approval(material_id))


class QuestionnaireSignatureCliTests(unittest.TestCase):
    invoke = test_cli.CliTests.invoke

    def test_answers_cli_returns_unanswered_signature_and_keeps_private_runtime_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "fictional-home")
            code, output, errors = self.invoke("profile", "init", "--json", home=home)
            self.assertEqual(code, 0, output + errors)
            paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": home})
            with SQLiteRepository(paths.database).initialize() as repository:
                job_id, claim_ids = approved_fixture(repository)
            question = {"id": "fictional-cli-signature", "text": "Please sign this statement about your experience.",
                        "claim_ids": list(claim_ids[:2]), "required": True}
            before = paths.database.read_bytes()
            metadata = paths.database.stat()
            inventory = sorted(str(path.relative_to(home)) for path in Path(home).rglob("*"))
            code, output, errors = self.invoke("answers", "--job-id", job_id, "--questions-file", "-", "--json",
                                               home=home, stdin_text=json.dumps({"questions": [question]}))
            self.assertEqual(code, 0, output + errors)
            self.assertEqual(errors, "")
            envelope = json.loads(output)
            self.assertTrue(envelope["ok"])
            self.assertFalse(envelope["data"]["stored"])
            self.assertFalse(envelope["data"]["external_action_taken"])
            answer, = envelope["data"]["answers"]
            self.assertEqual(answer["status"], "need_info")
            self.assertIsNone(answer["answer"])
            self.assertEqual(answer["factual_units"], [])
            self.assertEqual(answer["need_info"], [HUMAN_ANSWER])
            self.assertIs(answer["human_review_required"], True)
            self.assertIs(answer["external_action_taken"], False)
            self.assertEqual(paths.database.read_bytes(), before)
            self.assertEqual(paths.database.stat().st_mtime_ns, metadata.st_mtime_ns)
            self.assertEqual(sorted(str(path.relative_to(home)) for path in Path(home).rglob("*")), inventory)


if __name__ == "__main__":
    unittest.main()
