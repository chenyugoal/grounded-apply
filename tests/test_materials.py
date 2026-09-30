from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.repositories.latex_renderer import LatexResumeRenderer, latex_source
from grounded_apply.repositories.material_files import export_material
from grounded_apply.services.jobs import JobService
from grounded_apply.services.material_models import FactualUnit, MaterialValidationError, RenderedResume, ResumeStructure
from grounded_apply.services.materials import MaterialBlocked, MaterialService
from grounded_apply.services.profile import CreateProfileReviewDecision, ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.resume_extraction import extract_resume
from tests.test_jobs import JOB_TEXT
from tests.test_resume_extraction import RESUME
from tests.test_profile_service import reviewable_import_request
from grounded_apply.services.profile import TextSourceSpan
from grounded_apply.services.workflow import hash_bytes


def layout_fixture(repetitions: int = 1) -> ResumeStructure:
    def unit(key: str, kind: str, text: str, style: str = "paragraph") -> FactualUnit:
        return FactualUnit(key, kind, text, (key,), ("synthetic-evidence",), (), "synthetic-hash", style)
    units = [unit("name", "candidate_name", "Avery Quill"),
        unit("email", "contact_email", "avery.quill@example.com")]
    for index in range(repetitions):
        units.extend((unit(f"role-{index}", "employment_description",
            f"Research Engineer {index + 1} | Example City, ZZ | Fictional Systems Laboratory | May 2022–August 2024", "heading"),
            unit(f"work-{index}", "employment_description",
                "Model evaluation: Built a fictional document retrieval and evaluation pipeline using Python and SQL; "
                "compared baseline models with structured error analysis across a synthetic collection of technical reports, "
                "documented failure cases, and contributed reproducible experiments for collaborators. "
                "Reduced synthetic setup time from 45 minutes to 15 minutes; did not lead the project.", "bullet")))
    units.extend((unit("skills", "skill_use", "Languages: Python, SQL, Rust"),
        unit("degree", "education", "Example University | Example City, ZZ | PhD in Computer Science (expected) | September 2027", "heading")))
    return ResumeStructure("synthetic-job", tuple(units))


class SyntheticRenderer:
    """Deterministic test adapter; real PDF behavior has a separate required gate."""
    def render(self, structure: ResumeStructure) -> RenderedResume:
        text = "\n".join(u.text for u in structure.units)
        return RenderedResume(b"synthetic-pdf:" + text.encode(), latex_source(structure), text, 1, "synthetic-renderer@1")

    def validate(self, structure: ResumeStructure, rendered: RenderedResume) -> None:
        if self.render(structure) != rendered:
            raise MaterialValidationError("Synthetic material changed")


def approved_fixture(repository: SQLiteRepository) -> tuple[str, tuple[str, ...]]:
    profile = ProfileService(repository)
    extraction = extract_resume(RESUME)
    request = extraction.selected_request((0, 1, 2, 3, 4, 6, 7), RESUME, "synthetic-onboarding")
    result = profile.create_import_proposal(request)
    for item in profile.list_review_items():
        profile.decide_review_item(CreateProfileReviewDecision(claim_id=item.claim.id, review_token=item.review_token,
            decision=ApprovalStatus.APPROVED, actor_id="synthetic-user", idempotency_key=item.claim.id))
    job = JobService(repository).add("https://example.com/jobs/engineer", JOB_TEXT, idempotency_key="synthetic-job")
    return job["job_id"], tuple(c.id for c in result.claims if c.claim_type not in {"candidate_name", "contact_email"})


class MaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = SQLiteRepository(Path(directory.name) / "synthetic.db").initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.service = MaterialService(self.repository, SyntheticRenderer())

    def test_build_contains_only_packet_text_and_requires_explicit_approval(self) -> None:
        result = self.service.build(self.job_id, self.claim_ids, idempotency_key="synthetic-material")
        self.assertFalse(result["ready"])
        stored = self.service.get(result["material_id"])
        for unit in stored["structure"]["units"]:
            self.assertIn(unit["claim_id"], unit["packet_claim_ids"])
            self.assertTrue(unit["evidence_ids"])
            self.assertEqual(unit["text"], self.repository.get_claim(unit["claim_id"])["canonical_text"])
        self.assertIn("did not lead", stored["extracted_text"])
        self.assertFalse(self.service.is_approved(result["material_id"]))
        self.assertEqual(self.service.list(self.job_id)[0]["status"], "draft")
        approval = dict(bundle_sha256=result["bundle_sha256"], actor_id="synthetic-user", idempotency_key="synthetic-ready")
        self.assertTrue(self.service.approve(result["material_id"], **approval)["dry_run"])
        self.assertFalse(self.service.is_approved(result["material_id"]))
        self.assertTrue(self.service.approve(result["material_id"], **approval, confirm=True)["ready"])
        self.assertTrue(self.service.approve(result["material_id"], **approval, confirm=True)["replayed"])
        replay = self.service.build(self.job_id, self.claim_ids, idempotency_key="synthetic-material")
        self.assertEqual(replay["material_id"], result["material_id"])

    def test_retired_fact_invalidates_material_readiness_and_future_reuse(self) -> None:
        result = self.service.build(self.job_id, self.claim_ids, idempotency_key="synthetic-material")
        lifecycle = ProfileLifecycleService(self.repository)
        preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retirement")
        lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retirement", confirm=True, preview_token=preview.preview_token)
        with self.assertRaises(MaterialBlocked):
            self.service.approve(result["material_id"], bundle_sha256=result["bundle_sha256"], actor_id="synthetic-user", idempotency_key="ready", confirm=True)
        self.assertIsNone(self.repository.get_material_approval(result["material_id"]))
        self.assertEqual(self.service.list(self.job_id)[0]["status"], "needs_review")

    def test_unknown_fact_produces_need_info_and_no_material(self) -> None:
        with self.assertRaises(MaterialBlocked) as error:
            self.service.build(self.job_id, ("missing-claim",), idempotency_key="synthetic-material")
        self.assertTrue(error.exception.outcomes)
        self.assertEqual(self.repository._connection.execute("SELECT count(*) FROM material_versions").fetchone()[0], 0)

    def test_layout_is_closed_bound_to_approval_and_idempotency(self) -> None:
        layout = {"schema_version": 1, "presentations": {self.claim_ids[0]: "heading"}}
        result = self.service.build(self.job_id, self.claim_ids, layout=layout, idempotency_key="styled")
        self.service.approve(result["material_id"], bundle_sha256=result["bundle_sha256"],
            actor_id="synthetic-user", idempotency_key="styled-ready", confirm=True)
        self.assertTrue(self.service.build(self.job_id, self.claim_ids, layout=layout, idempotency_key="styled")["ready"])
        with self.assertRaises(RepositoryError):
            self.service.build(self.job_id, self.claim_ids, idempotency_key="styled")
        changed = self.service.build(self.job_id, self.claim_ids, idempotency_key="unstyled")
        self.assertNotEqual(changed["bundle_sha256"], result["bundle_sha256"])
        self.assertFalse(changed["ready"])
        invalid = ({"schema_version": True, "presentations": {}}, {"schema_version": 2, "presentations": {}},
            {"schema_version": 1, "presentations": {"unselected": "heading"}},
            {"schema_version": 1, "presentations": {self.claim_ids[0]: "invented text"}},
            {"schema_version": 1, "presentations": {}, "text": "new fact"})
        for value in invalid:
            with self.subTest(layout=value), self.assertRaises(MaterialValidationError):
                self.service.build(self.job_id, self.claim_ids, layout=value, idempotency_key="invalid")
        name_id = next(u.claim_id for u in self.service.plan(self.job_id, self.claim_ids).units if u.claim_type == "candidate_name")
        with self.assertRaises(MaterialValidationError):
            self.service.plan(self.job_id, (*self.claim_ids, name_id),
                layout={"schema_version": 1, "presentations": {name_id: "heading"}})

    def test_legacy_build_approval_replay_and_export_survive_new_default(self) -> None:
        with patch("grounded_apply.services.materials.CURRENT_TRANSFORMATION", "approved_text_selection@1"):
            result = self.service.build(self.job_id, self.claim_ids, idempotency_key="legacy")
            self.service.approve(result["material_id"], bundle_sha256=result["bundle_sha256"],
                actor_id="synthetic-user", idempotency_key="legacy-ready", confirm=True)
            before = self.service.get(result["material_id"])
        self.assertEqual(before, self.service.get(result["material_id"]))
        self.assertTrue(self.service.build(self.job_id, self.claim_ids, idempotency_key="legacy")["ready"])
        with tempfile.TemporaryDirectory() as directory:
            export_material(self.service.get(result["material_id"]), Path(directory).resolve() / "old", portable_root=None)
        with self.assertRaises(MaterialValidationError):
            self.service.build(self.job_id, self.claim_ids, idempotency_key="legacy",
                layout={"schema_version": 1, "presentations": {self.claim_ids[0]: "heading"}})
        modern = self.service.build(self.job_id, self.claim_ids, idempotency_key="modern")
        self.assertEqual(self.service.get(modern["material_id"])["structure"]["transformation"], "approved_text_selection@2")

    def test_approved_tex_source_formatting_is_data_and_keeps_exact_canonical_text(self) -> None:
        selected = []
        profile = ProfileService(self.repository)
        examples = (("heading", r"\resumeSubheading{Engineer}{Example City}{Fictional Lab}{2024}",
                     "Engineer | Example City | Fictional Lab | 2024"),
                    ("bullet", r"\resumeItem{Contributed a Python prototype; did not lead the project.}",
                     "Contributed a Python prototype; did not lead the project."))
        for style, evidence, text in examples:
            request = reviewable_import_request(idempotency_key="tex-" + style,
                claim_type="employment_description", value=text, canonical_text=text)
            source = request.source_text.replace("Synthetic supporting evidence.", evidence)
            start = source.index(evidence)
            proposal = replace(request.proposals[0], span=TextSourceSpan(start=start, end=start + len(evidence), text=evidence))
            result = profile.create_import_proposal(replace(request, source_text=source,
                expected_source_sha256=hash_bytes(source.encode()), proposals=(proposal,)))
            item = profile.list_review_items()[0]
            profile.decide_review_item(CreateProfileReviewDecision(claim_id=item.claim.id, review_token=item.review_token,
                decision=ApprovalStatus.APPROVED, actor_id="synthetic-user", idempotency_key="approve-" + style))
            selected.append(result.claims[0].id)
        structure = self.service.plan(self.job_id, tuple(selected))
        self.assertEqual([(u.presentation, u.text) for u in structure.units[:2]], [(s, t) for s, _, t in examples])
        self.assertEqual([u.presentation for u in self.service.plan(self.job_id, tuple(selected),
            transformation="approved_text_selection@1").units[:2]], ["paragraph", "paragraph"])

    def test_explicit_selection_cannot_hide_conflicting_names(self) -> None:
        profile = ProfileService(self.repository)
        source = RESUME.replace("Name: Avery Quill", "Name: Bailey Fable")
        result = profile.create_import_proposal(extract_resume(source).selected_request((0,), source, "synthetic-conflict"))
        item = profile.list_review_items()[0]
        profile.decide_review_item(CreateProfileReviewDecision(claim_id=item.claim.id,
            review_token=item.review_token, decision=ApprovalStatus.APPROVED,
            actor_id="synthetic-reviewer", idempotency_key="synthetic-conflict-approval"))
        for selected in (self.claim_ids, (result.claims[0].id, *self.claim_ids)):
            with self.assertRaises(MaterialBlocked):
                self.service.build(self.job_id, selected, idempotency_key="synthetic-conflicting-material")

    def test_export_refuses_modified_copies_without_repair(self) -> None:
        built = self.service.build(self.job_id, self.claim_ids, idempotency_key="synthetic-material")
        material = self.service.get(built["material_id"])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / "export"
            export_material(material, target, portable_root=None)
            self.assertTrue(export_material(material, target, portable_root=None)["replayed"])
            (target / "resume.txt").write_text("SYNTHETIC MODIFIED CONTENT")
            with self.assertRaises(MaterialValidationError):
                export_material(material, target, portable_root=None)
            self.assertEqual((target / "resume.txt").read_text(), "SYNTHETIC MODIFIED CONTENT")

    def test_changed_facts_while_rendering_block_persistence(self) -> None:
        renderer = self.service._renderer
        real_render = renderer.render
        def changed(structure: ResumeStructure) -> RenderedResume:
            rendered = real_render(structure)
            lifecycle = ProfileLifecycleService(self.repository)
            preview = lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retirement")
            lifecycle.retire(self.claim_ids[0], actor_id="synthetic-user", idempotency_key="synthetic-retirement", confirm=True, preview_token=preview.preview_token)
            return rendered
        # Validation in this test adapter calls render, so patch the service
        # boundary once while retaining an independent validator.
        class ChangedRenderer:
            def render(self, structure):
                return changed(structure)
            def validate(self, structure, rendered):
                if real_render(structure) != rendered:
                    raise MaterialValidationError("Changed")
        with self.assertRaises(MaterialBlocked):
            MaterialService(self.repository, ChangedRenderer()).build(self.job_id, self.claim_ids, idempotency_key="synthetic-material")


@unittest.skipUnless(importlib.util.find_spec("pypdf") is not None and shutil.which("pdflatex"), "optional materials provider; run scripts/check_materials.py")
class RealPdfTests(unittest.TestCase):
    def test_long_bullets_four_field_headers_and_page_breaks_preserve_text(self) -> None:
        import io
        from pypdf import PdfReader
        renderer = LatexResumeRenderer()
        for count, expected_pages in ((1, 1), (7, 2)):
            with self.subTest(roles=count):
                structure = layout_fixture(count)
                rendered = renderer.render(structure)
                self.assertEqual(rendered.page_count, expected_pages)
                self.assertIn("did not lead", rendered.extracted_text)
                self.assertNotIn(" | Fictional", rendered.extracted_text)
                pages = [page.extract_text() for page in PdfReader(io.BytesIO(rendered.pdf)).pages]
                for page in pages:
                    for index in range(count):
                        if f"Research Engineer {index + 1}" in page:
                            tail = page[page.index(f"Research Engineer {index + 1}"):]
                            self.assertIn("Model evaluation:", tail)
                self.assertIn("PhD in Computer Science (expected)", pages[-1])
                self.assertIn("September 2027", pages[-1])
                tampered = replace(structure, units=tuple(replace(u, presentation="paragraph") if u.presentation == "heading" else u for u in structure.units))
                with self.assertRaises(MaterialValidationError):
                    renderer.validate(tampered, rendered)

    def test_unrenderable_content_is_rejected_without_truncation(self) -> None:
        structure = layout_fixture()
        long_word = replace(structure.units[3], text="X" * 400)
        with self.assertRaises(MaterialValidationError):
            LatexResumeRenderer().render(replace(structure, units=(*structure.units[:3], long_word)))

    def test_real_legacy_pdf_retains_validation_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "synthetic.db") as repository:
                job, claims = approved_fixture(repository)
                service = MaterialService(repository, LatexResumeRenderer())
                with patch("grounded_apply.services.materials.CURRENT_TRANSFORMATION", "approved_text_selection@1"):
                    result = service.build(job, claims, idempotency_key="legacy-real")
                    service.approve(result["material_id"], bundle_sha256=result["bundle_sha256"],
                        actor_id="synthetic-user", idempotency_key="legacy-approved", confirm=True)
                    before = service.get(result["material_id"])
                self.assertEqual(service.get(result["material_id"]), before)
                self.assertEqual(before["manifest"]["renderer"], "grounded-apply.latex-resume@1")
                self.assertTrue(service.build(job, claims, idempotency_key="legacy-real")["ready"])

    def test_pdf_with_added_factual_text_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "synthetic.db") as repository:
                job, claims = approved_fixture(repository)
                renderer = LatexResumeRenderer()
                original = MaterialService(repository, renderer).plan(job, claims)
                fabricated = replace(original.units[0], text="Led a team of 99 engineers with a 500 percent improvement.")
                altered = replace(original, units=(*original.units, fabricated))
                rendered = renderer.render(altered)
                with self.assertRaises(MaterialValidationError):
                    renderer.validate(original, replace(rendered, latex=latex_source(original)))

    def test_real_pdf_preserves_every_factual_unit_and_can_be_revalidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "synthetic.db") as repository:
                job, claims = approved_fixture(repository)
                renderer = LatexResumeRenderer()
                service = MaterialService(repository, renderer)
                result = service.build(job, claims, idempotency_key="synthetic-pdf")
                record = service.get(result["material_id"])
                self.assertTrue(record["pdf_bytes"].startswith(b"%PDF-"))
                self.assertIn("Avery Quill", record["extracted_text"])
                self.assertIn("did not lead", record["extracted_text"])
                text = record["extracted_text"]
                self.assertLess(text.index("Example Robotics"), text.index("January 2022"))
                self.assertLess(text.index("January 2022"), text.index("Built a Python"))
                self.assertNotIn("•Example Robotics", text)
                self.assertEqual(record["validation"]["unsupported_factual_units"], 0)

    def test_tex_commands_are_literal_and_never_read_a_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "private.txt"
            secret.write_text("SYNTHETIC-SECRET-MUST-NOT-BE-READ")
            with SQLiteRepository(Path(directory) / "synthetic.db") as repository:
                job, claims = approved_fixture(repository)
                renderer = LatexResumeRenderer()
                structure = MaterialService(repository, renderer).plan(job, claims)
                unit = structure.units[0]
                attack = replace(unit, text=r"Example \input{" + str(secret) + "}")
                structure = replace(structure, units=(attack, *structure.units[1:]))
                try:
                    pdf = renderer.render(structure)
                except MaterialValidationError:
                    return  # Unsupported text/layout fails closed before use.
                self.assertNotIn(secret.read_text(), pdf.extracted_text)


if __name__ == "__main__":
    unittest.main()
