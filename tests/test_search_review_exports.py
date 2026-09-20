from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.batches import BatchService
from grounded_apply.services.batches import _at
from grounded_apply.services.material_models import MaterialValidationError
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.searches import SearchExecutionError, SearchService
from grounded_apply.services.workflow import canonical
from tests.test_batches import RecordingRenderer
from tests.test_discovery import GREENHOUSE, encoded, greenhouse_job
from tests.test_materials import approved_fixture
from tests.test_searches import SearchTransport


class SearchReviewExportTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-synthetic-review-export-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        with SQLiteRepository(self.database).initialize() as repository:
            _, self.claim_ids = approved_fixture(repository)
        self.active_repositories = 0
        self.opens: list[bool] = []
        self.renderer = RecordingRenderer()
        self.transport = SearchTransport(self)
        self.service = SearchService(self.factory, self.transport, self.renderer)

    @contextmanager
    def factory(self, read_only: bool):
        self.opens.append(read_only)
        with SQLiteRepository(self.database, existing_only=True, read_only=read_only).initialize() as repository:
            self.active_repositories += 1
            try:
                yield repository
            finally:
                self.active_repositories -= 1

    def configure(self, **changes):
        return self.service.configure({"schema_version": 1,
            "sources": [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"}],
            "claim_ids": list(self.claim_ids), "max_jobs": 2, **changes},
            idempotency_key="fictional-scope")["search_id"]

    def retire(self):
        with self.factory(False) as repository:
            service = ProfileLifecycleService(repository)
            preview = service.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire")
            service.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="retire",
                confirm=True, preview_token=preview.preview_token)

    def test_snapshot_is_current_immutable_deterministic_and_one_read_transaction(self):
        run = self.service.run(self.configure(), idempotency_key="first")
        before, calls, rendered = self.database.read_bytes(), len(self.transport.calls), len(self.renderer.built)
        self.opens.clear()
        original, checked = MaterialService.get, []
        def get(service, material_id, **kwargs):
            self.assertTrue(service._repository._connection.in_transaction)
            self.assertEqual(self.active_repositories, 1)
            checked.append(material_id)
            return original(service, material_id, **kwargs)
        with patch.object(MaterialService, "get", get):
            packet = self.service.export_snapshot(run["run_id"])
        self.assertEqual(self.opens, [True])
        self.assertEqual(len(set(checked)), 2)
        self.assertEqual(packet, self.service.export_snapshot(run["run_id"]))
        review = json.loads(packet.review_json)
        self.assertEqual(packet.review_json, canonical(review))
        self.assertEqual(review["exported_material_count"], 2)
        self.assertEqual(review["omitted_material_count"], 0)
        self.assertFalse(review["application_ready"])
        self.assertFalse(review["approvals_recorded"])
        self.assertFalse(review["external_action_taken"])
        self.assertEqual({item["export_status"] for item in review["items"]}, {"current_draft"})
        for item, material in zip(review["items"], packet.materials, strict=True):
            metadata = json.loads(material.material_json)
            self.assertEqual((metadata["job_id"], metadata["id"], metadata["bundle_sha256"]),
                (item["job_id"], item["material_id"], item["bundle_sha256"]))
            self.assertIn("location", item)
            self.assertEqual(item["questionnaire_coverage"], "unknown")
            self.assertNotIn("pdf_bytes", metadata)
            self.assertTrue(material.pdf_bytes)
        with self.assertRaises(FrozenInstanceError):
            packet.run_id = "changed"
        with self.assertRaises(FrozenInstanceError):
            packet.materials[0].job_id = "changed"
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual((len(self.transport.calls), len(self.renderer.built)), (calls, rendered))
        self.assertFalse(Path(str(self.database) + "-journal").exists())

    def test_lease_expiry_alone_does_not_change_export_bytes(self):
        scope = self.configure()
        run = self.service.run(scope, idempotency_key="first")
        moment = datetime.now(UTC) + timedelta(minutes=1)
        with self.factory(False) as repository, repository.transaction():
            lease = repository.get_search_lease(scope)
            repository.acquire_search_lease(scope, run_id=run["run_id"], expected_epoch=lease["epoch"],
                owner="synthetic-owner", now=_at(moment), expires_at=_at(moment + timedelta(minutes=1)))
        with patch.object(self.service, "_clock", return_value=moment):
            self.assertEqual(self.service.get(run["run_id"])["status"], "running")
            packet = self.service.export_snapshot(run["run_id"])
        with patch.object(self.service, "_clock", return_value=moment + timedelta(minutes=2)):
            self.assertEqual(self.service.get(run["run_id"])["status"], "completed")
            self.assertEqual(packet, self.service.export_snapshot(run["run_id"]))

    def test_required_sensitive_answers_keep_current_partial_bundles_and_blockers(self):
        scope = self.configure(questions=[{"id": "fictional-sponsorship", "text": "Do you need sponsorship?",
            "claim_ids": [], "required": True}], questionnaire_coverage="provided")
        run = self.service.run(scope, idempotency_key="first")
        packet = self.service.export_snapshot(run["run_id"])
        review = json.loads(packet.review_json)
        self.assertEqual(len(packet.materials), 2)
        self.assertEqual(review["counts"]["blocked"], 2)
        for item, material in zip(review["items"], packet.materials, strict=True):
            self.assertEqual(item["export_status"], "current_partial")
            self.assertTrue(item["currently_valid"])
            self.assertTrue(item["requires_approval"])
            self.assertFalse(item["material_approved"])
            self.assertTrue(item["blockers"])
            answer = json.loads(material.material_json)["manifest"]["answers"][0]
            self.assertEqual(answer["status"], "need_info")
            self.assertTrue(answer["need_info"])

    def test_retired_facts_omit_stale_bytes_and_keep_exact_material_identity(self):
        run = self.service.run(self.configure(), idempotency_key="first")
        original_ids = {item["material_id"] for item in run["items"]}
        self.retire()
        before = self.database.read_bytes()
        packet = self.service.export_snapshot(run["run_id"])
        review = json.loads(packet.review_json)
        self.assertEqual(packet.materials, ())
        self.assertEqual(review["omitted_material_count"], 2)
        self.assertEqual({item["material_id"] for item in review["items"]}, original_ids)
        for item in review["items"]:
            self.assertEqual(item["export_status"], "stale_omitted")
            self.assertEqual(item["status"], "blocked")
            self.assertFalse(item["exported"])
            self.assertFalse(item["currently_valid"])
            self.assertTrue(item["blockers"])
        self.assertEqual(self.database.read_bytes(), before)

    def test_corrupt_stale_history_is_not_disguised_as_an_omitted_bundle(self):
        run = self.service.run(self.configure(), idempotency_key="first")
        self.retire()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER materials_no_update")
            connection.execute("UPDATE material_versions SET pdf_bytes=? WHERE id=?",
                (b"%PDF fictional-corrupt", run["items"][0]["material_id"]))
            connection.commit()
        with self.assertRaises(MaterialValidationError):
            self.service.export_snapshot(run["run_id"])

    def test_stale_omission_cannot_hide_corrupt_prior_approval(self):
        run = self.service.run(self.configure(), idempotency_key="first")
        item = run["items"][0]
        with self.factory(False) as repository:
            MaterialService(repository, self.renderer).approve(item["material_id"],
                bundle_sha256=item["bundle_sha256"], actor_id="synthetic-user",
                idempotency_key="synthetic-approval", confirm=True)
        self.retire()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER approvals_no_update")
            connection.execute("UPDATE material_approvals SET bundle_sha256=? WHERE material_id=?",
                ("0" * 64, item["material_id"]))
            connection.commit()
        with self.assertRaises(MaterialValidationError):
            self.service.export_snapshot(run["run_id"])

    def test_exact_old_run_export_does_not_follow_latest_posting_version(self):
        scope = self.configure()
        first = self.service.run(scope, idempotency_key="first")
        changed = greenhouse_job(1)
        changed["content"] += "<p>Changed fictional job wording.</p>"
        self.transport.responses[GREENHOUSE] = encoded({"jobs": [changed]})
        latest = self.service.run(scope, idempotency_key="latest")
        packet = self.service.export_snapshot(first["run_id"])
        review = json.loads(packet.review_json)
        self.assertEqual(review["run_id"], first["run_id"])
        self.assertEqual(review["batch_id"], first["batch_id"])
        self.assertEqual({item.job_id for item in packet.materials}, {item["job_id"] for item in first["items"]})
        self.assertFalse({item.job_id for item in packet.materials} & {item["job_id"] for item in latest["items"]})

    def test_coverage_gaps_and_pre_child_selection_remain_visible(self):
        sources = [{"id": "fictional-gh", "provider": "greenhouse", "board": "example"},
            {"id": "fictional-manual", "provider": "manual", "careers_url": "https://example.com/careers"}]
        scope = self.configure(sources=sources)
        with patch.object(BatchService, "create", side_effect=RuntimeError("synthetic child interruption")), self.assertRaises(SearchExecutionError) as failure:
            self.service.run(scope, idempotency_key="first")
        packet = self.service.export_snapshot(failure.exception.run_id)
        review = json.loads(packet.review_json)
        self.assertIsNone(review["batch_id"])
        self.assertFalse(review["coverage_complete"])
        self.assertEqual(review["sources"][1]["report"]["status"], "manual_required")
        self.assertEqual(len(review["items"]), 2)
        self.assertEqual({item["status"] for item in review["items"]}, {"not_prepared"})
        self.assertEqual(packet.materials, ())
        self.assertEqual(review["remaining_count"], 2)

    def test_prior_exact_bundle_approval_is_observed_without_new_approval(self):
        run = self.service.run(self.configure(), idempotency_key="first")
        original = self.service.export_snapshot(run["run_id"])
        item = run["items"][0]
        with self.factory(False) as repository:
            MaterialService(repository, self.renderer).approve(item["material_id"],
                bundle_sha256=item["bundle_sha256"], actor_id="synthetic-user",
                idempotency_key="synthetic-approval", confirm=True)
        before = self.database.read_bytes()
        packet = self.service.export_snapshot(run["run_id"])
        self.assertNotEqual(original.review_json, packet.review_json)
        self.assertEqual(original.materials, packet.materials)
        review = json.loads(packet.review_json)
        self.assertTrue(review["items"][0]["material_approved"])
        self.assertFalse(review["items"][0]["requires_approval"])
        self.assertFalse(review["approvals_recorded"])
        self.assertFalse(review["application_ready"])
        self.assertEqual(before, self.database.read_bytes())


if __name__ == "__main__":
    unittest.main()
