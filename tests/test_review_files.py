from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from grounded_apply.config import RuntimePaths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.review_files import ReviewExportError, export_search_review
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.review_exports import ReviewMaterial, SearchReviewSnapshot
from grounded_apply.services.workflow import canonical, hash_bytes
from tests.test_materials import SyntheticRenderer, approved_fixture


class ReviewFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gapply-synthetic-review-fixture-")
        cls.addClassCleanup(temporary.cleanup)
        with SQLiteRepository(Path(temporary.name) / "synthetic.db").initialize() as repository:
            job_id, claims = approved_fixture(repository)
            materials = MaterialService(repository, SyntheticRenderer())
            result = materials.build(job_id, claims, idempotency_key="synthetic-review-export")
            material = materials.get(result["material_id"])
        cls.job_id = job_id
        cls.bundle = ReviewMaterial(job_id, canonical({key: value for key, value in material.items() if key != "pdf_bytes"}), material["pdf_bytes"])
        cls.run_id = str(uuid4())
        cls.index = {"schema_version": 1, "run_id": cls.run_id, "status": "completed_with_gaps",
            "coverage_complete": False, "sources": [{"source_id": "fictional-board", "stage": "complete",
                "report": {"status": "partial"}, "captured_count": 1}],
            "items": [{"job_id": job_id, "material_id": material["id"], "bundle_sha256": material["bundle_sha256"], "title": "Fictional Python Engineer",
                "source_url": "https://example.com/jobs/fictional", "source_id": "fictional-board", "location": None,
                "status": "draft", "questionnaire_coverage": "unknown", "blockers": [], "exported": True,
                "export_status": "current_draft"}], "exported_material_count": 1, "omitted_material_count": 0,
            "application_ready": False, "approvals_recorded": False, "external_action_taken": False}

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gapply-synthetic-review-files-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        home = self.root / "runtime"
        home.mkdir(mode=0o700)
        self.paths = RuntimePaths(*(home / name for name in ("config", "data", "cache", "state")), portable_root=home)
        for directory in (self.paths.config_dir, self.paths.data_dir, self.paths.cache_dir, self.paths.state_dir):
            directory.mkdir(mode=0o700)
        self.destination = self.root / "review"
        self.packet = SearchReviewSnapshot(self.run_id, canonical(self.index), (self.bundle,))

    def export(self, packet=None, destination=None, **kwargs):
        return export_search_review(packet or self.packet, destination or self.destination, paths=self.paths, **kwargs)

    def changed_index(self, change, *, materials=None):
        index = json.loads(self.packet.review_json)
        change(index)
        return replace(self.packet, review_json=canonical(index), materials=self.packet.materials if materials is None else materials)

    def inventory(self):
        return {str(path.relative_to(self.destination)): path.read_bytes() for path in self.destination.rglob("*") if path.is_file()}

    def test_export_reuses_individual_format_and_exact_replay_is_read_only(self) -> None:
        result = self.export()
        self.assertEqual((result["material_count"], result["file_count"]), (1, 11))
        self.assertFalse(result["replayed"])
        self.assertFalse(result["application_ready"])
        self.assertFalse(result["approvals_recorded"])
        self.assertFalse(result["external_action_taken"])
        files = self.inventory()
        self.assertEqual(files[f"{self.job_id}/resume.pdf"], self.bundle.pdf_bytes)
        receipt = json.loads(files["export-receipt.json"])
        self.assertEqual(receipt["files"], {name: hash_bytes(value) for name, value in files.items() if name != "export-receipt.json"})
        individual = json.loads(files[f"{self.job_id}/export-receipt.json"])
        self.assertEqual(individual["files"]["resume.pdf"], hash_bytes(self.bundle.pdf_bytes))
        self.assertTrue(self.export()["replayed"])
        self.assertTrue(self.export(dry_run=True)["replayed"])
        self.assertEqual(self.inventory(), files)
        self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in self.destination.rglob("*")))

    def test_preview_creates_nothing_and_checks_same_sizes(self) -> None:
        result = self.export(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(self.destination.exists())
        with patch("grounded_apply.repositories.review_files.MAX_REVIEW_BYTES", 1):
            for dry in (True, False):
                with self.assertRaises(ReviewExportError):
                    self.export(dry_run=dry)
        self.assertFalse(self.destination.exists())

    def test_all_material_and_index_bounds_precede_first_write(self) -> None:
        payload = b"x" * (2 * 1024 * 1024 + 1)
        metadata = json.loads(self.bundle.material_json)
        metadata["manifest"]["pdf_sha256"] = hash_bytes(payload)
        large = replace(self.bundle, material_json=canonical(metadata), pdf_bytes=payload)
        cases = [replace(self.packet, materials=(large,)), replace(self.packet, review_json="x" * (8 * 1024 * 1024 + 1))]
        for packet in cases:
            for dry in (True, False):
                with self.subTest(dry=dry), self.assertRaises(ReviewExportError):
                    self.export(packet, dry_run=dry)
                self.assertFalse(self.destination.exists())
        with patch("grounded_apply.repositories.review_files.MAX_REVIEW_FILES", 10), self.assertRaises(ReviewExportError):
            self.export()
        self.assertFalse(self.destination.exists())

    def test_invalid_closed_packet_associations_precede_destination_creation(self) -> None:
        cases = [replace(self.packet, run_id="../../outside"), replace(self.packet, materials=()),
            replace(self.packet, materials=(self.bundle, self.bundle)),
            self.changed_index(lambda value: value["items"][0].update(job_id="../../outside")),
            self.changed_index(lambda value: value.update(application_ready=True)),
            self.changed_index(lambda value: value.update(exported_material_count=True)),
            self.changed_index(lambda value: value["items"].append(value["items"][0])),
            replace(self.packet, review_json=json.dumps(self.index)),
            replace(self.packet, materials=(replace(self.bundle, job_id=str(uuid4())),))]
        cases.append(self.changed_index(lambda value: value["items"][0].update(bundle_sha256="0" * 64)))
        for packet in cases:
            with self.subTest(packet=type(packet)), self.assertRaises(ReviewExportError):
                self.export(packet)
            self.assertFalse(self.destination.exists())

    def test_untrusted_markdown_html_controls_and_job_link_are_inert(self) -> None:
        dangerous = '[click](https://evil.example/x) ![img](https://evil.example/p.png) <script>alert(1)</script> `code`\n# heading\x00\u202e'
        packet = self.changed_index(lambda value: value["items"][0].update(title=dangerous, location=dangerous,
            source_url='https://example.com/job/")[evil](https://evil.example/x)',
            blockers=[{"question_label": dangerous, "reason": "missing"}]))
        self.export(packet)
        text = (self.destination / "review.md").read_text()
        self.assertNotIn("<script>", text)
        self.assertNotIn("![img]", text)
        self.assertNotIn("[click]", text)
        self.assertNotIn("\n# heading", text)
        self.assertNotIn("\x00", text)
        self.assertNotIn("\u202e", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn('https://example.com/job/%22%29%5Bevil%5D%28https://evil.example/x%29', text)
        self.assertEqual(json.loads((self.destination / "review.json").read_text())["items"][0]["title"], dangerous)

    def test_invalid_external_urls_do_not_become_links(self) -> None:
        for value in ("javascript:alert(1)", "http://example.com/job", "https://user:secret@example.com/job", "https://example.com/\njob",
                      "https://example.com/job?q=unsupported", "https://explore.jobs.netflix.net/careers/job/123?domain=netflix.com"):
            packet = self.changed_index(lambda index: index["items"][0].update(source_url=value))
            target = self.root / str(uuid4())
            self.export(packet, target)
            self.assertNotIn("[Open job page]", (target / "review.md").read_text())

    def test_query_job_links_preserve_route_and_encode_markdown_delimiters(self) -> None:
        value = "https://explore.jobs.netflix.net/careers/job/123?domain=netflix.com&microsite=netflix.com"
        packet = self.changed_index(lambda index: index["items"][0].update(source_url=value))
        self.export(packet)
        text = (self.destination / "review.md").read_text()
        self.assertIn("https://explore.jobs.netflix.net/careers/job/123?domain=netflix.com&amp;microsite=netflix.com", text)
        self.assertNotIn("%3Fdomain", text)

    def test_observed_approval_and_grouped_questions_are_visible_without_new_authority(self) -> None:
        def change(index):
            index["items"][0].update(material_approved=True, requires_approval=False, visual_review_required=False)
            index["grouped_blockers"] = [{"question_label": "Fictional shared question", "reason": "missing",
                "job_ids": [self.job_id]}]
        self.export(self.changed_index(change))
        text = (self.destination / "review.md").read_text()
        self.assertIn("Observed bundle approval: approved. Requires approval: no. Visual review required: no.", text)
        self.assertIn("Fictional shared question", text)
        self.assertIn("affected jobs: 1", text)
        self.assertIn("Exporting does not approve", text)

    def test_partial_and_stale_and_missing_packages_remain_explicit(self) -> None:
        def partial(value):
            value["items"][0].update(status="blocked", export_status="current_partial",
                blockers=[{"question_label": "Fictional sponsorship question", "reason": "human_answer_required"}])
        self.export(self.changed_index(partial))
        text = (self.destination / "review.md").read_text()
        self.assertIn("current\\_partial", text)
        self.assertIn("unknown", text)
        self.assertIn("Fictional sponsorship question", text)
        def omitted(value):
            value["items"][0].update(exported=False, export_status="stale_omitted", status="blocked")
            value.update(exported_material_count=0, omitted_material_count=1)
            value["items"].append({**value["items"][0], "job_id": str(uuid4()), "material_id": None,
                "export_status": "not_prepared", "status": "not_prepared"})
        target = self.root / "omitted"
        result = self.export(self.changed_index(omitted, materials=()), target)
        self.assertEqual(result["file_count"], 3)
        text = (target / "review.md").read_text()
        self.assertIn("current evidence no longer validates", text)
        self.assertIn("No current package", text)
        self.assertNotIn("[Review PDF]", text)

    def test_changed_receipt_or_bundle_or_index_never_overwrites(self) -> None:
        self.export()
        for name in ("review.md", "export-receipt.json", f"{self.job_id}/resume.pdf"):
            path = self.destination / name
            original = path.read_bytes()
            path.write_bytes(b"synthetic changed")
            before = self.inventory()
            with self.subTest(name=name), self.assertRaises(ReviewExportError):
                self.export()
            self.assertEqual(self.inventory(), before)
            path.write_bytes(original)
        changed = self.changed_index(lambda value: value["items"][0].update(title="Changed fictional posting"))
        before = self.inventory()
        with self.assertRaises(ReviewExportError):
            self.export(changed)
        self.assertEqual(self.inventory(), before)

    def test_missing_or_unknown_entries_refuse_without_repair(self) -> None:
        self.export()
        (self.destination / "review.md").unlink()
        with self.assertRaises(ReviewExportError):
            self.export()
        self.assertFalse((self.destination / "review.md").exists())
        target = self.root / "extra"
        self.export(destination=target)
        (target / "unexpected").mkdir()
        with self.assertRaises(ReviewExportError):
            self.export(destination=target)
        nested = self.root / "nested-extra"
        self.export(destination=nested)
        (nested / self.job_id / "unexpected").write_bytes(b"synthetic")
        with self.assertRaises(ReviewExportError):
            self.export(destination=nested)

    def test_private_directories_and_files_are_required_on_replay(self) -> None:
        self.export()
        paths = (self.destination, self.destination / self.job_id, self.destination / "review.json")
        for path in paths:
            original = path.stat().st_mode & 0o777
            path.chmod(0o755 if path.is_dir() else 0o644)
            with self.subTest(path=path.name), self.assertRaises(ReviewExportError):
                self.export()
            path.chmod(original)

    def test_destination_and_nested_symlinks_or_hardlinks_fail_closed(self) -> None:
        self.export()
        alias = self.root / "alias"
        alias.symlink_to(self.destination, target_is_directory=True)
        with self.assertRaises(ReviewExportError):
            self.export(destination=alias)
        material = self.destination / self.job_id
        moved = self.root / "moved-package"
        material.rename(moved)
        material.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ReviewExportError):
            self.export()
        material.unlink()
        moved.rename(material)
        linked = self.root / "linked-file"
        os.link(self.destination / "review.json", linked)
        with self.assertRaises(ReviewExportError):
            self.export()

    def test_every_xdg_runtime_directory_and_portable_root_are_excluded(self) -> None:
        xdg = replace(self.paths, portable_root=None)
        for directory in (xdg.config_dir, xdg.data_dir, xdg.cache_dir, xdg.state_dir):
            for dry in (False, True):
                with self.subTest(directory=directory.name, dry=dry), self.assertRaises(ReviewExportError):
                    export_search_review(self.packet, directory / "review", paths=xdg, dry_run=dry)
                self.assertFalse((directory / "review").exists())
        with self.assertRaises(ReviewExportError):
            self.export(destination=self.paths.portable_root / "review")

    def test_relative_repository_and_public_parent_destinations_are_rejected(self) -> None:
        with self.assertRaises(ReviewExportError):
            self.export(destination=Path("relative-review"))
        repository = self.root / "fictional-repository"
        repository.mkdir(mode=0o700)
        (repository / ".git").mkdir(mode=0o700)
        with self.assertRaises(ReviewExportError):
            self.export(destination=repository / "review")
        public = self.root / "public"
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaises(ReviewExportError):
            self.export(destination=public / "review")

    def test_interrupted_write_has_no_completion_receipt_and_cannot_be_repaired(self) -> None:
        from grounded_apply.repositories.review_files import write_private_file
        def fail_index(path, value):
            if path.name == "review.json":
                raise OSError("synthetic failure")
            return write_private_file(path, value)
        with patch("grounded_apply.repositories.review_files.write_private_file", side_effect=fail_index), self.assertRaises(ReviewExportError):
            self.export()
        self.assertFalse((self.destination / "export-receipt.json").exists())
        before = self.inventory()
        with self.assertRaises(ReviewExportError):
            self.export()
        self.assertEqual(self.inventory(), before)
        self.assertFalse(self.export(destination=self.root / "fresh")["replayed"])


if __name__ == "__main__":
    unittest.main()
