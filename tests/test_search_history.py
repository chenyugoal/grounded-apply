from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.searches import SearchExecutionError, SearchService
from tests.test_batches import RecordingRenderer
from tests.test_discovery import GREENHOUSE, encoded, greenhouse_job
from tests.test_materials import approved_fixture
from tests.test_searches import SearchTransport


class SearchHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-search-history-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.transport = SearchTransport(self)
        self.renderer = RecordingRenderer()
        self.service = SearchService(self.factory, self.transport, self.renderer)

    @contextmanager
    def factory(self, read_only: bool):
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def configure(self, **changes):
        return self.service.configure({"schema_version": 1,
            "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_jobs": 1, **changes}, idempotency_key="fictional-scope")["search_id"]

    def feed(self, *jobs):
        self.transport.responses[GREENHOUSE] = encoded({"jobs": list(jobs)})

    @contextmanager
    def forbid_current_resolution(self, forbidden_job_ids):
        original = MaterialService.plan
        def guarded(service, job_id, *args, **kwargs):
            self.assertNotIn(job_id, forbidden_job_ids, "unrelated historical evidence must not be resolved as current")
            return original(service, job_id, *args, **kwargs)
        with patch.object(MaterialService, "plan", guarded):
            yield

    def test_changed_and_unrelated_versions_keep_historical_audits_without_current_resolution(self):
        self.feed(greenhouse_job(1))
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        first_job = first["selection"][0]["job_id"]
        changed = greenhouse_job(1)
        changed["content"] += "<p>Changed fictional source version.</p>"
        self.feed(changed)
        with self.forbid_current_resolution({first_job}):
            second = self.service.run(scope, idempotency_key="changed")
        second_job = second["selection"][0]["job_id"]
        self.assertNotEqual(first_job, second_job)
        self.feed(greenhouse_job(2))
        with self.forbid_current_resolution({first_job, second_job}):
            third = self.service.run(scope, idempotency_key="unrelated")
        self.assertEqual(third["counts"]["draft"], 1)
        self.assertEqual(len(self.renderer.built), 3)

    def test_matching_current_draft_is_still_checked_before_skip(self):
        self.feed(greenhouse_job(1))
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        original, checked = MaterialService.plan, []
        def observed(service, job_id, *args, **kwargs):
            checked.append(job_id)
            return original(service, job_id, *args, **kwargs)
        with patch.object(MaterialService, "plan", observed):
            repeated = self.service.run(scope, idempotency_key="repeat")
        self.assertIn(first["selection"][0]["job_id"], checked)
        self.assertEqual(repeated["skipped"]["unchanged_draft"], 1)
        self.assertEqual(repeated["counts"]["total"], 0)
        self.assertEqual(len(self.renderer.built), 1)

    def test_exclusions_filters_and_prior_drafts_remain_before_candidate_quota(self):
        excluded, intern, unknown, prior, fresh = (greenhouse_job(i) for i in range(1, 6))
        intern["title"] = "Fictional Intern"
        unknown["location"] = {"name": None}
        self.feed(excluded, intern, unknown, prior)
        scope = self.configure(schema_version=2,
            excluded_identities=[{"kind": "discovery", "provider": "greenhouse", "board": "example", "external_id": "1"}],
            preparation_filters={"title_excludes": ["Intern"], "missing_location": "exclude"})
        first = self.service.run(scope, idempotency_key="first")
        self.assertTrue(first["selection"][0]["source_url"].endswith("/4"))
        self.feed(excluded, intern, unknown, prior, fresh)
        second = self.service.run(scope, idempotency_key="second")
        self.assertTrue(second["selection"][0]["source_url"].endswith("/5"))
        for reason in ("explicitly_excluded", "title_excluded", "location_unknown_excluded", "unchanged_draft"):
            self.assertEqual(second["skipped"][reason], 1)
        with self.factory(True) as repository:
            self.assertEqual(len(repository.list_job_snapshots()), 3)

    def test_historical_application_skip_does_not_resolve_unneeded_current_draft(self):
        self.feed(greenhouse_job(1))
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        job_id, material_id = first["selection"][0]["job_id"], first["items"][0]["material_id"]
        with self.factory(False) as repository:
            materials = MaterialService(repository, self.renderer)
            materials.approve(material_id, bundle_sha256=first["items"][0]["bundle_sha256"],
                actor_id="synthetic-user", idempotency_key="approval", confirm=True)
            applications = ApplicationService(repository, materials)
            application = applications.add(job_id, actor_id="synthetic-user", idempotency_key="application")
            for state in ("shortlisted", "preparing", "ready_for_review", "applied", "withdrawn"):
                options = {"actor_id": "synthetic-user", "idempotency_key": "transition-" + state}
                if state in {"ready_for_review", "applied"}:
                    options["material_id"] = material_id
                if state == "applied":
                    options["confirm_submitted"] = True
                preview = applications.transition(application["application_id"], state, **options)
                applications.transition(application["application_id"], state, **options, confirm=True,
                    preview_token=preview["preview_token"])
        self.feed(greenhouse_job(1), greenhouse_job(2))
        with self.forbid_current_resolution({job_id}):
            second = self.service.run(scope, idempotency_key="after-submission")
        self.assertEqual(second["skipped"]["already_applied"], 1)
        self.assertEqual(second["counts"]["draft"], 1)
        self.assertTrue(second["selection"][0]["source_url"].endswith("/2"))

    def test_unrelated_corrupt_historical_pdf_still_stops_before_source_capture(self):
        self.feed(greenhouse_job(1))
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER materials_no_update")
            connection.execute("UPDATE material_versions SET pdf_bytes=? WHERE id=?",
                (b"synthetic invalid PDF", first["items"][0]["material_id"]))
            connection.commit()
        self.feed(greenhouse_job(2))
        with self.assertRaises(SearchExecutionError):
            self.service.run(scope, idempotency_key="corrupt-history")
        with self.factory(True) as repository:
            self.assertEqual(len(repository.list_job_snapshots()), 2)
            self.assertEqual(len(repository.list_material_ids()), 1)
        self.assertEqual(len(self.renderer.built), 1)


if __name__ == "__main__":
    unittest.main()
