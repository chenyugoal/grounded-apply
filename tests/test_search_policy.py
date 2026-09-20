from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.jobs import JobService
from grounded_apply.services.material_models import MaterialValidationError
from grounded_apply.services.materials import MaterialBlocked, MaterialService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.search_policy import (
    PostingIdentity, SearchEligibilityPolicy, SearchPolicyLimitError,
    posting_identity, validate_excluded_identities,
)
from tests.test_discovery_capture import discovered
from tests.test_jobs import JOB_TEXT
from tests.test_materials import SyntheticRenderer, approved_fixture


class SearchPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "synthetic.db"
        self.repository = SQLiteRepository(self.path).initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.jobs = JobService(self.repository)
        self.materials = MaterialService(self.repository, SyntheticRenderer())
        self.applications = ApplicationService(self.repository, self.materials)
        self.policy = SearchEligibilityPolicy(self.repository, self.materials)

    def move(self, application_id: str, target: str, **fields: object) -> None:
        options = {"actor_id": "synthetic-user", "idempotency_key": application_id + "." + target, **fields}
        preview = self.applications.transition(application_id, target, **options)
        self.applications.transition(application_id, target, **options,
                                     confirm=True, preview_token=preview["preview_token"])

    def application(self, job_id: str, *, submitted: bool = True) -> tuple[str, str]:
        application = self.applications.add(job_id, actor_id="synthetic-user",
                                            idempotency_key="synthetic-app-" + job_id)
        identifier = application["application_id"]
        self.move(identifier, "shortlisted")
        self.move(identifier, "preparing")
        material = self.materials.build(job_id, self.claim_ids, idempotency_key="synthetic-material-" + job_id)
        self.materials.approve(material["material_id"], bundle_sha256=material["bundle_sha256"],
            actor_id="synthetic-user", idempotency_key="synthetic-approve-" + job_id, confirm=True)
        self.move(identifier, "ready_for_review", material_id=material["material_id"])
        if submitted:
            self.move(identifier, "applied", material_id=material["material_id"], confirm_submitted=True)
        return identifier, material["material_id"]

    def test_changed_source_version_retains_the_applied_posting_identity(self) -> None:
        original = self.jobs.capture_discovered(discovered())["job_id"]
        self.application(original)
        changed = self.jobs.capture_discovered(discovered(changed=True))["job_id"]
        self.assertNotEqual(original, changed)
        decision = self.policy.assess(changed)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "already_applied")
        self.assertEqual(decision.identity.to_dict(), {
            "kind": "discovery", "provider": "greenhouse", "board": "fictional-labs", "external_id": "123"})
        self.assertEqual(decision.identity, posting_identity(self.jobs.get(original)))
        self.assertEqual(self.policy.assess_discovered(discovered(changed=True)), decision)

    def test_any_historical_applied_event_excludes_later_rejected_or_withdrawn(self) -> None:
        for state in ("rejected", "withdrawn"):
            with self.subTest(state=state):
                job_id = self.jobs.add("https://example.com/jobs/" + state, JOB_TEXT,
                                       idempotency_key="synthetic-" + state)["job_id"]
                application_id, _ = self.application(job_id)
                self.move(application_id, state)
                self.assertEqual(self.applications.get(application_id)["state"], state)
                self.assertEqual(self.policy.assess(job_id).reason, "already_applied")

    def test_unsubmitted_applications_and_archived_jobs_are_not_inferred_as_excluded(self) -> None:
        self.application(self.job_id, submitted=False)
        self.assertTrue(self.policy.assess(self.job_id).eligible)
        other = self.jobs.add("https://example.com/jobs/archived", JOB_TEXT,
                              idempotency_key="synthetic-archived")["job_id"]
        application = self.applications.add(other, actor_id="synthetic-user", idempotency_key="synthetic-archive-app")
        self.move(application["application_id"], "archived")
        self.assertTrue(self.policy.assess(other).eligible)

    def test_exact_manual_url_matches_discovery_but_unknown_aliases_do_not(self) -> None:
        item = discovered()
        manual = self.jobs.add(item.source_url, JOB_TEXT, idempotency_key="synthetic-manual-canonical")["job_id"]
        self.application(manual)
        self.assertEqual(self.policy.assess_discovered(item).reason, "already_applied")
        for index, url in enumerate((
            item.source_url + "/", item.source_url.replace("boards.greenhouse.io", "job-boards.greenhouse.io"),
            item.source_url.replace("123", "%31%32%33"), "https://example.com/fictional-same-role",
        )):
            alias = self.jobs.add(url, JOB_TEXT, idempotency_key=f"synthetic-alias-{index}")["job_id"]
            self.assertTrue(self.policy.assess(alias).eligible)

    def test_discovery_submission_also_excludes_a_manual_exact_url_version(self) -> None:
        item = discovered()
        captured = self.jobs.capture_discovered(item)["job_id"]
        self.application(captured)
        manual = self.jobs.add(item.source_url, JOB_TEXT, idempotency_key="synthetic-manual-version")["job_id"]
        decision = self.policy.assess(manual)
        self.assertEqual(decision.identity.kind, "url")
        self.assertEqual(decision.reason, "already_applied")

    def test_explicit_identity_and_exact_url_exclusions_apply_before_capture(self) -> None:
        item = discovered()
        variants = (
            [{"kind": "discovery", "provider": "greenhouse", "board": "fictional-labs", "external_id": "123"}],
            [{"kind": "url", "source_url": item.source_url}],
        )
        before = self.repository.list_job_snapshots()
        for entries in variants:
            policy = SearchEligibilityPolicy(self.repository, self.materials,
                excluded_identities=validate_excluded_identities(entries))
            self.assertEqual(policy.assess_discovered(item).reason, "explicitly_excluded")
            self.assertEqual(policy.snapshot().assess_discovered(item).reason, "explicitly_excluded")
        self.assertEqual(self.repository.list_job_snapshots(), before)

    def test_exclusions_are_closed_bounded_and_never_change_url_policy(self) -> None:
        valid = {"kind": "url", "source_url": "https://example.com/jobs/fictional"}
        self.assertEqual(validate_excluded_identities([valid])[0].to_dict(), valid)
        for value in (None, (), [valid, valid], [valid] * 501,
                      [{**valid, "instruction": "submit"}], [{"kind": "url"}],
                      [{"kind": "url", "source_url": "http://example.com/job"}],
                      [{"kind": "url", "source_url": valid["source_url"] + "?token=private"}],
                      [{"kind": "url", "source_url": "https://secret@example.com/job"}],
                      [{"kind": "url", "source_url": valid["source_url"] + "#fragment"}],
                      [{"kind": "discovery", "provider": "manual", "board": "fictional", "external_id": "1"}],
                      [{"kind": "discovery", "provider": "netflix", "board": "other", "external_id": "1"}],
                      [{"kind": "discovery", "provider": "greenhouse", "board": "../private", "external_id": "1"}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_excluded_identities(value)
        with self.assertRaises(ValueError):
            PostingIdentity("url", provider="greenhouse", source_url=valid["source_url"])

    def test_retired_evidence_does_not_erase_validated_past_submission(self) -> None:
        _, material_id = self.application(self.job_id)
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire")
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retire",
                         confirm=True, preview_token=preview.preview_token)
        with self.assertRaises(MaterialBlocked):
            self.materials.get(material_id)
        self.assertEqual(self.policy.assess(self.job_id).reason, "already_applied")

    def test_corrupt_unrelated_history_fails_closed_even_with_explicit_exclusion(self) -> None:
        application = self.applications.add(self.job_id, actor_id="synthetic-user", idempotency_key="synthetic-corrupt-app")
        records = self.repository.list_application_events(application["application_id"])
        malformed = [{**records[0], "state": "applied"}]
        policy = SearchEligibilityPolicy(self.repository, self.materials,
            excluded_identities=(posting_identity(self.jobs.get(self.job_id)),))
        with patch.object(self.repository, "list_application_events", return_value=malformed):
            with self.assertRaises(RepositoryError):
                policy.assess(self.job_id)
            with self.assertRaises(RepositoryError):
                policy.assess_discovered(discovered())

    def test_historical_material_integrity_is_validated_not_just_event_state(self) -> None:
        self.application(self.job_id)
        with patch.object(self.materials._renderer, "validate", side_effect=MaterialValidationError("Synthetic corrupt PDF")):
            with self.assertRaises(RepositoryError):
                self.policy.assess(self.job_id)

    def test_public_assessment_refreshes_history_while_explicit_snapshot_is_fixed(self) -> None:
        job = self.jobs.get(self.job_id)
        snapshot = self.policy.snapshot()
        self.assertTrue(snapshot.assess(job).eligible)
        self.application(self.job_id)
        self.assertTrue(snapshot.assess(job).eligible)
        self.assertFalse(self.policy.assess(self.job_id).eligible)
        self.assertFalse(self.policy.snapshot().assess(job).eligible)

    def test_history_bounds_fail_instead_of_claiming_partial_history_is_eligible(self) -> None:
        counts = self.repository.support_counts()
        for field, count in (("applications", 1001), ("application_events", 10001)):
            with patch.object(self.repository, "support_counts", return_value={**counts, field: count}), \
                 patch.object(self.repository, "list_applications") as read:
                with self.assertRaises(SearchPolicyLimitError):
                    self.policy.assess(self.job_id)
                read.assert_not_called()

    def test_read_only_assessment_preserves_database_bytes_and_returns_no_candidate_content(self) -> None:
        self.application(self.job_id)
        self.repository.close()
        before = self.path.read_bytes()
        with SQLiteRepository(self.path, read_only=True).initialize() as repository:
            service = SearchEligibilityPolicy(repository, MaterialService(repository, SyntheticRenderer()))
            decision = service.assess(self.job_id)
            self.assertEqual(set(asdict(decision)), {"identity", "source_url", "eligible", "reason"})
            self.assertNotIn("Avery", repr(decision))
            self.assertNotIn("claim", repr(decision))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(Path(str(self.path) + "-wal").exists())
        self.assertFalse(Path(str(self.path) + "-shm").exists())
        self.assertFalse(Path(str(self.path) + "-journal").exists())

    def test_forged_source_identity_or_version_is_rejected_before_assessment(self) -> None:
        item = discovered()
        for changed in (replace(item, content_sha256="0" * 64),
                        replace(item, source_url="https://example.com/different")):
            with self.assertRaises(ValueError):
                self.policy.assess_discovered(changed)
        saved_id = self.jobs.capture_discovered(item)["job_id"]
        saved = self.jobs.get(saved_id)
        with self.assertRaises(ValueError):
            posting_identity(replace(saved, discovery={**saved.discovery, "external_id": "999"}))


if __name__ == "__main__":
    unittest.main()
