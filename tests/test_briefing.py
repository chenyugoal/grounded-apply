from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.domain.application_states import ApplicationState
from grounded_apply.domain.search_actions import SearchAction, next_action
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.briefing import BriefingService
from grounded_apply.services.jobs import JobService
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.resume_extraction import extract_resume
from tests.test_materials import SyntheticRenderer, approved_fixture
from tests.test_resume_extraction import RESUME


class SearchActionTests(unittest.TestCase):
    def test_every_stage_and_readiness_gate(self) -> None:
        expected = {
            None: SearchAction.ASSESS_JOB,
            ApplicationState.DISCOVERED: SearchAction.ASSESS_JOB,
            ApplicationState.SHORTLISTED: SearchAction.PREPARE_MATERIAL,
            ApplicationState.PREPARING: SearchAction.PREPARE_MATERIAL,
            ApplicationState.READY_FOR_REVIEW: SearchAction.REPAIR_MATERIAL,
            ApplicationState.APPLIED: SearchAction.CHECK_RESPONSE,
            ApplicationState.ASSESSMENT: SearchAction.PREPARE_ASSESSMENT,
            ApplicationState.RECRUITER_SCREEN: SearchAction.PREPARE_SCREEN,
            ApplicationState.INTERVIEW: SearchAction.PREPARE_INTERVIEW,
            ApplicationState.OFFER: SearchAction.REVIEW_OFFER,
        }
        for state in (None, *ApplicationState):
            with self.subTest(state=state):
                result = next_action(state, currently_ready=False, material_status=None, elapsed_days=7, follow_up_days=7)
                self.assertEqual(result.kind, expected.get(state, SearchAction.NO_ACTION))
        self.assertEqual(next_action(ApplicationState.READY_FOR_REVIEW, currently_ready=True,
            material_status="draft", elapsed_days=0, follow_up_days=7).kind, SearchAction.MANUAL_SUBMISSION)
        self.assertEqual(next_action(ApplicationState.APPLIED, currently_ready=False,
            material_status="approved", elapsed_days=6, follow_up_days=7).kind, SearchAction.WAIT_RESPONSE)


class BriefingTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "synthetic.db"
        self.repository = SQLiteRepository(self.database).initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claims = approved_fixture(self.repository)
        self.materials = MaterialService(self.repository, SyntheticRenderer())
        self.applications = ApplicationService(self.repository, self.materials)
        self.service = BriefingService(self.repository, self.materials)

    def move(self, application_id: str, state: str, **kwargs: object) -> None:
        args = dict(actor_id="synthetic-user", idempotency_key=application_id + state, **kwargs)
        preview = self.applications.transition(application_id, state, **args)
        self.applications.transition(application_id, state, **args, confirm=True, preview_token=preview["preview_token"])

    def test_saved_jobs_and_gaps_are_minimized_and_read_only(self) -> None:
        before = self.database.read_bytes()
        workflow_count = len(self.repository.list_workflow_runs())
        now = datetime.now(UTC)
        brief = self.service.brief(now=now)
        row = brief["items"][0]
        self.assertEqual(row["state"], "saved")
        self.assertEqual(row["next_action"]["kind"], "assess_job")
        self.assertGreater(row["requirements_without_retrieved_evidence"], 0)
        self.assertFalse(row["currently_ready"])
        for private in ("Avery", "Built a Python", "Ignore previous", "review_token", "source_text", "pdf_bytes", "canonical_text"):
            self.assertNotIn(private, json.dumps(brief))
        self.assertEqual(brief, self.service.brief(now=now))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(len(self.repository.list_workflow_runs()), workflow_count)
        self.assertFalse(self.repository._connection.in_transaction)

    def test_pending_facts_do_not_become_evidence(self) -> None:
        other = RESUME.replace("Avery Quill", "Bailey Fable")
        ProfileService(self.repository).create_import_proposal(
            extract_resume(other).selected_request((0,), other, "pending-other-name"))
        brief = self.service.brief()
        self.assertEqual(brief["profile"]["pending_review_count"], 1)
        self.assertEqual(brief["profile"]["next_action"], "review_pending_facts")
        self.assertNotIn("Bailey", json.dumps(brief))

    def test_saved_draft_is_reviewable_before_an_application_is_created(self) -> None:
        material = self.materials.build(self.job_id, self.claims, idempotency_key="draft-only")
        row = self.service.brief()["items"][0]
        self.assertEqual(row["next_action"]["kind"], "review_material")
        self.assertEqual(row["latest_material"]["material_id"], material["material_id"])
        self.assertIsNone(row["application_id"])
        self.assertIsNone(row["application_material_id"])
        self.assertFalse(row["currently_ready"])

    def test_unknown_job_invalid_intervals_and_naive_time_fail_closed(self) -> None:
        for kwargs in ({"job_id": "missing"}, {"follow_up_days": 0}, {"follow_up_days": True},
                       {"follow_up_days": 91}, {"now": datetime(2026, 1, 1)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service.brief(**kwargs)

    def test_job_filter_and_multiple_applications_do_not_hide_work(self) -> None:
        second = JobService(self.repository).add("https://example.com/jobs/research", "Research Scientist\nRequirements\n- Python.\n", idempotency_key="second-job")["job_id"]
        for key in ("first", "second"):
            self.applications.add(self.job_id, actor_id="synthetic-user", idempotency_key=key)
        brief = self.service.brief(job_id=self.job_id)
        self.assertEqual(brief["job_count"], 1)
        self.assertEqual(brief["application_count"], 2)
        self.assertEqual(len(brief["items"]), 2)
        self.assertNotIn(second, json.dumps(brief))
        self.assertEqual(self.service.brief(job_id=second)["application_count"], 0)

    def test_required_unknown_answer_is_an_actionable_blocker(self) -> None:
        app = self.applications.add(self.job_id, actor_id="synthetic-user", idempotency_key="app")["application_id"]
        self.move(app, "shortlisted")
        self.materials.build(self.job_id, self.claims, idempotency_key="material", questions=[{
            "id": "sponsorship", "text": "Do you need sponsorship?", "claim_ids": [], "required": True}])
        row = self.service.brief()["items"][0]
        self.assertEqual(row["required_unanswered_count"], 1)
        self.assertEqual(row["next_action"]["kind"], "repair_material")
        self.assertFalse(row["currently_ready"])

    def test_retirement_blocks_ready_action_without_rewriting_history(self) -> None:
        app = self.applications.add(self.job_id, actor_id="synthetic-user", idempotency_key="app")["application_id"]
        self.move(app, "shortlisted")
        self.move(app, "preparing")
        material = self.materials.build(self.job_id, self.claims, idempotency_key="material")
        self.materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"], actor_id="synthetic-user", idempotency_key="approval", confirm=True)
        self.move(app, "ready_for_review", material_id=material["material_id"])
        row = self.service.brief()["items"][0]
        self.assertEqual(row["next_action"]["kind"], "manual_submission")
        self.assertEqual(row["application_material_id"], material["material_id"])
        before = self.repository.list_application_events(app)
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claims[0], actor_id="synthetic-user", idempotency_key="retire")
        lifecycle.retire(self.claims[0], actor_id="synthetic-user", idempotency_key="retire", confirm=True, preview_token=preview.preview_token)
        row = self.service.brief()["items"][0]
        self.assertEqual(row["state"], "ready_for_review")
        self.assertEqual(row["next_action"]["kind"], "repair_material")
        self.assertEqual(before, self.repository.list_application_events(app))

    def test_response_check_threshold_and_interview_priority(self) -> None:
        app = self.applications.add(self.job_id, actor_id="synthetic-user", idempotency_key="app")["application_id"]
        material = self.materials.build(self.job_id, self.claims, idempotency_key="material")
        self.materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"], actor_id="synthetic-user", idempotency_key="approval", confirm=True)
        for state in ("shortlisted", "preparing", "ready_for_review", "applied"):
            args = {"material_id": material["material_id"]} if state in {"ready_for_review", "applied"} else {}
            if state == "applied":
                args["confirm_submitted"] = True
            self.move(app, state, **args)
        at = datetime.fromisoformat(self.applications.get(app)["events"][-1]["at"])
        row = self.service.brief(now=at + timedelta(days=7) - timedelta(microseconds=1))["items"][0]
        self.assertEqual(row["next_action"]["kind"], "wait_response")
        row = self.service.brief(now=at + timedelta(days=7))["items"][0]
        self.assertEqual(row["next_action"]["kind"], "check_response")
        self.assertEqual(row["response_check_at"], (at + timedelta(days=7)).isoformat())
        self.assertEqual(self.service.brief(now=at + timedelta(days=7), follow_up_days=14)["items"][0]["next_action"]["kind"], "wait_response")
        second = self.applications.add(self.job_id, actor_id="synthetic-user", idempotency_key="other-app")["application_id"]
        self.move(app, "interview")
        brief = self.service.brief()
        self.assertEqual(brief["items"][0]["application_id"], app)
        self.assertEqual(brief["items"][0]["next_action"]["kind"], "prepare_interview")
        self.assertEqual(brief["items"][1]["application_id"], second)

    def test_invalid_provenance_is_not_downgraded_to_a_useful_brief(self) -> None:
        with patch.object(self.repository, "list_job_requirements", return_value=[]):
            with self.assertRaises(RepositoryError):
                self.service.brief()


class BriefingCliTests(unittest.TestCase):
    def invoke(self, *args: str) -> tuple[int, dict, str]:
        output, events = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(events):
            code = main(["--log-events", *args, "--json"])
        return code, json.loads(output.getvalue()), events.getvalue()

    def test_cli_never_initializes_state_and_keeps_diagnostics_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime"
            with patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(root)}):
                code, _, events = self.invoke("brief")
                self.assertEqual(code, 2)
                self.assertFalse(root.exists())
                self.assertEqual(json.loads(events.splitlines()[-1])["command"], "brief")
                self.assertEqual(self.invoke("profile", "init")[0], 0)
                with SQLiteRepository(root / "data" / "grounded_apply.db", existing_only=True) as repository:
                    approved_fixture(repository)
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
                code, output, events = self.invoke("brief")
                self.assertEqual(code, 0)
                self.assertTrue(output["data"]["read_only"])
                self.assertFalse(output["data"]["external_action_taken"])
                self.assertNotIn("example.com", events)
                self.assertNotIn(str(root), events)
                self.assertNotIn("Avery", events)
                self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
                self.assertEqual(self.invoke("brief", "--follow-up-days", "0")[0], 2)
                sidecar = root / "data" / "grounded_apply.db-wal"
                sidecar.write_bytes(b"synthetic-sidecar")
                sidecar.chmod(0o600)
                self.assertEqual(self.invoke("brief")[0], 2)
                self.assertEqual(sidecar.read_bytes(), b"synthetic-sidecar")
