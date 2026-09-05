from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.materials import MaterialBlocked, MaterialService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.questionnaires import QuestionnaireService
from tests.test_materials import SyntheticRenderer, approved_fixture


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = SQLiteRepository(Path(directory.name) / "synthetic.db").initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.materials = MaterialService(self.repository, SyntheticRenderer())
        self.service = ApplicationService(self.repository, self.materials)

    def move(self, application_id: str, target: str, **fields):
        options = {"actor_id": "synthetic-user", "idempotency_key": "synthetic-" + target, **fields}
        preview = self.service.transition(application_id, target, **options)
        self.assertTrue(preview["dry_run"])
        return self.service.transition(application_id, target, **options, confirm=True, preview_token=preview["preview_token"])

    def test_manual_submission_requires_approved_exact_material_and_preserves_history(self) -> None:
        application = self.service.add(self.job_id, actor_id="synthetic-user", idempotency_key="synthetic-application")
        app_id = application["application_id"]
        with self.assertRaises(ValueError):
            self.service.transition(app_id, "applied", actor_id="synthetic-user", idempotency_key="invalid")
        self.move(app_id, "shortlisted")
        self.move(app_id, "preparing")
        material = self.materials.build(self.job_id, self.claim_ids, idempotency_key="synthetic-material")
        with self.assertRaises(ValueError):
            self.move(app_id, "ready_for_review", material_id=material["material_id"])
        self.materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"], actor_id="synthetic-user", idempotency_key="approve", confirm=True)
        self.move(app_id, "ready_for_review", material_id=material["material_id"])
        with self.assertRaises(ValueError):
            self.move(app_id, "applied", material_id=material["material_id"])
        result = self.move(app_id, "applied", material_id=material["material_id"], confirm_submitted=True)
        self.assertFalse(result["external_action_taken"])
        before = self.repository.get_submission_snapshot(app_id)
        snapshot = json.loads(before["snapshot_json"])
        self.assertEqual(snapshot["pdf_sha256"], self.materials.get(material["material_id"])["manifest"]["pdf_sha256"])
        self.assertTrue(snapshot["human_confirmed_submission"])
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire")
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire", confirm=True, preview_token=preview.preview_token)
        self.assertEqual(self.service.get(app_id)["state"], "applied")
        self.assertEqual(before, self.repository.get_submission_snapshot(app_id))
        with self.assertRaises(MaterialBlocked):
            self.materials.get(material["material_id"])
        self.move(app_id, "interview")
        with contextlib.closing(sqlite3.connect(self.repository.database)) as connection, connection:
            for sql in ("DELETE FROM application_events", "UPDATE submission_snapshots SET snapshot_json = '{}'", "DELETE FROM material_versions"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)

    def test_stale_transition_preview_and_exact_replay(self) -> None:
        app_id = self.service.add(self.job_id, actor_id="synthetic-user", idempotency_key="synthetic-application")["application_id"]
        args = dict(actor_id="synthetic-user", idempotency_key="shortlist")
        preview = self.service.transition(app_id, "shortlisted", **args)
        stale = self.service.transition(app_id, "archived", actor_id="synthetic-user", idempotency_key="archive")
        self.service.transition(app_id, "shortlisted", **args, confirm=True, preview_token=preview["preview_token"])
        self.assertTrue(self.service.transition(app_id, "shortlisted", **args, confirm=True, preview_token=preview["preview_token"])["replayed"])
        with self.assertRaises(ValueError):
            self.service.transition(app_id, "archived", actor_id="synthetic-user", idempotency_key="archive", confirm=True, preview_token=stale["preview_token"])
        self.assertEqual(self.service.get(app_id)["state"], "shortlisted")

    def test_retirement_invalidates_current_readiness_and_submission_preview(self) -> None:
        app_id = self.service.add(self.job_id, actor_id="synthetic-user", idempotency_key="synthetic-application")["application_id"]
        self.move(app_id, "shortlisted")
        self.move(app_id, "preparing")
        material = self.materials.build(self.job_id, self.claim_ids, idempotency_key="synthetic-material")
        self.materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"], actor_id="synthetic-user", idempotency_key="approve", confirm=True)
        self.move(app_id, "ready_for_review", material_id=material["material_id"])
        self.assertTrue(self.service.get(app_id)["currently_ready"])
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire")
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire", confirm=True, preview_token=preview.preview_token)
        self.assertFalse(self.service.get(app_id)["currently_ready"])
        with self.assertRaises(MaterialBlocked):
            self.service.transition(app_id, "applied", material_id=material["material_id"], confirm_submitted=True,
                actor_id="synthetic-user", idempotency_key="submitted")
        self.assertIsNone(self.repository.get_submission_snapshot(app_id))

    def test_sensitive_and_unknown_question_answers_are_never_guessed(self) -> None:
        service = QuestionnaireService(self.repository)
        questions = [{"id": "q" + str(i), "text": text, "claim_ids": [self.claim_ids[0]], "required": True}
            for i, text in enumerate(("Are you authorized to work without sponsorship?", "Type your name as an electronic signature.",
                "Do you agree to certify these statements?", "What is your favorite color?", "Describe your experience building Python services."))]
        answers = service.prepare(self.job_id, questions)
        self.assertTrue(all(a["answer"] is None for a in answers[:4]))
        self.assertEqual(answers[-1]["answer"], self.repository.get_claim(self.claim_ids[0])["canonical_text"])
        material = self.materials.build(self.job_id, self.claim_ids, questions=questions, idempotency_key="synthetic-questions")
        with self.assertRaises(MaterialBlocked):
            self.materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"], actor_id="synthetic-user", idempotency_key="ready", confirm=True)
        self.assertIsNone(self.repository.get_material_approval(material["material_id"]))
