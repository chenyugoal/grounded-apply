from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.latex_renderer import LatexResumeRenderer, latex_source, normalized
from grounded_apply.repositories.material_files import export_material
from grounded_apply.services.material_build_history import validate_material_build_record
from grounded_apply.services.material_models import MaterialValidationError, heading_fields, presented_text
from grounded_apply.services.materials import MaterialHistoryIntegrityError, MaterialService, _structure
from grounded_apply.services.profile import (
    CreateImportProposal, CreateProfileReviewDecision, ProfileService, ProposedImportClaim, TextSourceSpan,
)
from grounded_apply.services.workflow import canonical, digest
from tests import test_material_history
from tests.test_materials import SyntheticRenderer, approved_fixture, layout_fixture


RESEARCH_FACTS = (
    ("research_description", "Fictional Study | Example City, ZZ | Fictional Research Lab | May 2024-August 2026"),
    ("research_description", "Contributed Python evaluation tools; did not lead the study."),
    ("publication", "Quill, A. Fictional Widget Study; submitted September 2026, not accepted."),
    ("education", "PhD in Computer Science, Example University, expected May 2027."),
)


class ResearchMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-research-material-test-")
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        self.repository = SQLiteRepository(self.workspace / "fictional.db").initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.profile = ProfileService(self.repository)
        self.renderer = SyntheticRenderer()
        self.service = MaterialService(self.repository, self.renderer)

    def import_research(self) -> tuple[str, ...]:
        # Structured import records the supplied classification and exact wording;
        # this fixture never infers a role, employer, degree, or publication status.
        lines = [text if index != 1 else "- " + text for index, (_, text) in enumerate(RESEARCH_FACTS)]
        source = "\n".join(lines) + "\n"
        proposals = []
        position = 0
        for (kind, text), line in zip(RESEARCH_FACTS, lines, strict=True):
            proposals.append(ProposedImportClaim(claim_type=kind, value=text, canonical_text=text,
                span=TextSourceSpan(start=position, end=position + len(line), text=line)))
            position += len(line) + 1
        imported = self.profile.create_import_proposal(CreateImportProposal(
            idempotency_key="fictional-research-intake", source_text=source,
            expected_source_sha256=sha256(source.encode()).hexdigest(), proposals=tuple(proposals),
            content_policy_version=3))
        selected = tuple(claim.id for claim in imported.claims)
        for item in self.profile.list_review_items():
            if item.claim.id in selected:
                self.profile.decide_review_item(CreateProfileReviewDecision(
                    claim_id=item.claim.id, review_token=item.review_token,
                    decision=ApprovalStatus.APPROVED, actor_id="fictional-reviewer",
                    idempotency_key="approve-" + item.claim.id))
        return selected

    @staticmethod
    def layout(selected: tuple[str, ...]) -> dict:
        return {"schema_version": 1, "presentations": {selected[0]: "heading"}}

    def approve_and_audit(self, material_id: str) -> None:
        material = self.service.get(material_id)
        workflow = self.repository.get_workflow_run(material["workflow_run_id"])
        payload = validate_material_build_record(material, workflow)
        self.assertEqual(payload["transformation"], "approved_text_selection@3")
        self.assertIn("presentations", payload)
        self.service.validate_historical_facts(material_id)
        self.assertFalse(self.service.is_approved(material_id))
        self.assertFalse(self.service.approve(material_id, bundle_sha256=material["bundle_sha256"],
            actor_id="fictional-reviewer", idempotency_key="fictional-material-approval")["ready"])
        self.assertTrue(self.service.approve(material_id, bundle_sha256=material["bundle_sha256"],
            actor_id="fictional-reviewer", idempotency_key="fictional-material-approval", confirm=True)["ready"])
        approval = self.repository.get_material_approval(material_id)
        assert approval is not None
        before = self.repository._connection.serialize()
        self.service.validate_historical_approval_record(material_id)
        self.service.validate_historical_approval_facts(material_id)
        self.service.validate_historical_approval_eligibility(material_id)
        self.service.validate_historical_use(material_id, used_at=approval["approved_at"])
        self.service.validate_historical_inventory()
        self.assertTrue(self.service.is_approved(material_id))
        self.assertTrue(self.service.is_approved(material_id, require_current=False))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_unselected_and_answer_only_research_keep_existing_version_and_structure(self) -> None:
        before = self.service.plan(self.job_id, self.claim_ids)
        selected = self.import_research()
        self.assertEqual(self.service.plan(self.job_id, self.claim_ids), before)
        questions = [{"id": "research", "text": "Describe your research contribution.",
                      "claim_ids": [selected[1]], "required": True}]
        preview = self.service.build(self.job_id, self.claim_ids, questions=questions,
            idempotency_key="fictional-answer-only", dry_run=True)
        self.assertEqual(preview["structure"], asdict(before))
        self.assertEqual(preview["structure"]["transformation"], "approved_text_selection@2")
        self.assertEqual(preview["answers"][0]["status"], "draft")
        result = self.service.build(self.job_id, self.claim_ids, questions=questions,
            idempotency_key="fictional-answer-only")
        self.assertEqual(self.service.get(result["material_id"])["structure"], asdict(before))

    def test_research_plan_preview_and_build_share_version_and_exact_mappings(self) -> None:
        selected = self.import_research()
        layout = self.layout(selected)
        structure = self.service.plan(self.job_id, selected, layout=layout)
        before = self.repository._connection.serialize()
        preview = self.service.build(self.job_id, selected, layout=layout,
            idempotency_key="fictional-research-material", dry_run=True)
        self.assertEqual(preview["structure"], asdict(structure))
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertEqual(structure.transformation, "approved_text_selection@3")
        expected = digest({"structure": preview["structure"], "answers": preview["answers"]})
        result = self.service.build(self.job_id, selected, layout=layout,
            idempotency_key="fictional-research-material", expected_plan_sha256=expected)
        material = self.service.get(result["material_id"])
        self.assertEqual(material["structure"], asdict(structure))
        self.assertFalse(result["ready"])
        for unit in structure.units:
            claim = self.profile.get_claim(unit.claim_id)
            self.assertEqual((unit.text, unit.claim_type), (claim.canonical_text, claim.claim_type))
            self.assertIn(unit.claim_id, unit.packet_claim_ids)
            self.assertEqual(set(unit.evidence_ids), set(claim.evidence_ids))
        self.assertEqual(heading_fields(structure.units[0], structure.transformation),
            tuple(RESEARCH_FACTS[0][1].split(" | ")))
        self.assertEqual(presented_text(structure, structure.units[0]), RESEARCH_FACTS[0][1].replace(" | ", " "))
        self.assertEqual(structure.units[1].presentation, "bullet")
        self.approve_and_audit(result["material_id"])
        stored = self.service.get(result["material_id"])
        # A stored version always wins over the new-build fallback.
        with patch("grounded_apply.services.materials.CURRENT_TRANSFORMATION", "approved_text_selection@1"):
            replay = self.service.build(self.job_id, selected, layout=layout, idempotency_key="fictional-research-material")
            self.assertTrue(replay["replayed"])
            self.assertTrue(replay["ready"])
            self.assertEqual(self.service.get(result["material_id"]), stored)
        target = self.workspace / "export"
        self.assertFalse(export_material(stored, target, portable_root=None)["replayed"])
        self.assertTrue(export_material(stored, target, portable_root=None)["replayed"])
        self.assertEqual((target / "resume.pdf").read_bytes(), stored["pdf_bytes"])

    def test_explicit_old_versions_reject_research_and_publication_cannot_be_heading(self) -> None:
        selected = self.import_research()
        structure = self.service.plan(self.job_id, selected)
        before = self.repository._connection.serialize()
        for version in ("approved_text_selection@1", "approved_text_selection@2"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(MaterialValidationError, "Research facts require"):
                    self.service.plan(self.job_id, selected, transformation=version)
                with self.assertRaisesRegex(MaterialValidationError, "unsupported factual section"):
                    latex_source(replace(structure, transformation=version))
        with self.assertRaisesRegex(MaterialValidationError, "incompatible"):
            self.service.plan(self.job_id, selected,
                layout={"schema_version": 1, "presentations": {selected[2]: "heading"}})
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_legacy_latex_bytes_and_saved_material_survive_new_research_inventory(self) -> None:
        hashes = {1: "505d443ad9793c1555429f62752981aff65ad2d191d065ad8fa768ef03c54285",
                  2: "5f8774f3609469274cec4d65e62f42c8c98abe61503728c24c4b18a20f4de795"}
        for version, expected in hashes.items():
            structure = layout_fixture()
            if version == 1:
                structure = replace(structure, units=tuple(replace(unit, presentation="paragraph") for unit in structure.units))
            structure = replace(structure, transformation=f"approved_text_selection@{version}")
            self.assertEqual(sha256(latex_source(structure).encode()).hexdigest(), expected)
        result = self.service.build(self.job_id, self.claim_ids, idempotency_key="fictional-existing")
        before = self.service.get(result["material_id"])
        self.import_research()
        self.assertEqual(self.service.get(result["material_id"]), before)
        self.assertTrue(self.service.build(self.job_id, self.claim_ids, idempotency_key="fictional-existing")["replayed"])
        self.service.validate_historical_facts(result["material_id"])

    def test_research_build_payload_preserves_closed_presentation_and_version_contract(self) -> None:
        selected = self.import_research()
        result = self.service.build(self.job_id, selected, layout=self.layout(selected), idempotency_key="fictional-payload")
        material = self.service.get(result["material_id"])
        workflow = self.repository.get_workflow_run(material["workflow_run_id"])
        assert workflow is not None
        original = json.loads(workflow["input_json"])
        self.assertEqual(original["version"], 1)
        invalid = [{key: value for key, value in original.items() if key != "presentations"},
                   {**original, "transformation": "approved_text_selection@2"},
                   {**original, "presentations": {selected[0]: "research"}},
                   {**original, "presentations": {"fictional-unselected": "heading"}},
                   {**original, "version": True}, {**original, "section": "Research"}]
        for payload in invalid:
            with self.subTest(payload=payload):
                changed = {**workflow, "input_json": canonical(payload), "input_hash_sha256": digest(payload)}
                with self.assertRaisesRegex(ValueError, "Material build record failed"):
                    validate_material_build_record(deepcopy(material), changed)

    def test_rehashed_employment_type_substitution_fails_current_and_historical_facts(self) -> None:
        selected = self.import_research()
        result = self.service.build(self.job_id, selected, idempotency_key="fictional-tampered-type")
        # Rehash an isolated fictional fixture so record/PDF checks pass first.
        test_material_history.MaterialHistoryTests.rewrite_material(self, result["material_id"],
            edit_structure=lambda structure: structure["units"][0].update(claim_type="employment_description"))
        before = self.repository._connection.serialize()
        with self.assertRaises(MaterialValidationError):
            self.service.get(result["material_id"])
        with self.assertRaises(MaterialHistoryIntegrityError):
            self.service.validate_historical_facts(result["material_id"])
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_null_stored_version_is_not_automatic_selection_on_replay(self) -> None:
        selected = self.import_research()
        result = self.service.build(self.job_id, selected, idempotency_key="fictional-invalid-replay")
        material = self.service.get(result["material_id"])
        workflow = self.repository.get_workflow_run(material["workflow_run_id"])
        assert workflow is not None
        payload = json.loads(workflow["input_json"])
        payload["transformation"] = None
        with self.repository.transaction():
            self.repository._connection.execute(
                "UPDATE workflow_runs SET input_json = ?, input_hash_sha256 = ? WHERE id = ?",
                (canonical(payload), digest(payload), workflow["id"]))
        before = self.repository._connection.serialize()
        with patch.object(self.service, "plan", side_effect=AssertionError("Corrupt replay must precede planning")), \
                self.assertRaisesRegex(MaterialValidationError, "replay audit is invalid"):
            self.service.build(self.job_id, selected, idempotency_key="fictional-invalid-replay")
        self.assertEqual(self.repository._connection.serialize(), before)

    @unittest.skipUnless(shutil.which("pdflatex") and importlib.util.find_spec("pypdf"), "Real PDF dependencies are required")
    def test_real_pdf_preserves_research_publications_qualifiers_and_full_lifecycle(self) -> None:
        selected = self.import_research()
        self.renderer = LatexResumeRenderer()
        self.service = MaterialService(self.repository, self.renderer)
        result = self.service.build(self.job_id, selected, layout=self.layout(selected), idempotency_key="fictional-real-research")
        material = self.service.get(result["material_id"])
        self.assertEqual(material["manifest"]["renderer"], "grounded-apply.latex-resume@3")
        self.assertIn(r"\bfseries Research", material["latex_text"])
        self.assertIn(r"\bfseries Publications", material["latex_text"])
        self.assertNotIn(r"\bfseries Experience", material["latex_text"])
        for _, text in RESEARCH_FACTS:
            self.assertIn(normalized(text.replace(" | ", " ")), normalized(material["extracted_text"]))
        self.approve_and_audit(result["material_id"])
        target = self.workspace / "real-export"
        export_material(self.service.get(result["material_id"]), target, portable_root=None)
        self.assertEqual((target / "resume.pdf").read_bytes(), material["pdf_bytes"])
        self.assertTrue(self.service.build(self.job_id, selected, layout=self.layout(selected),
            idempotency_key="fictional-real-research")["replayed"])
        structure = _structure(material["structure"])
        wrong = replace(structure, units=tuple(replace(unit, claim_type="employment_description")
            if unit.claim_type == "research_description" else unit for unit in structure.units))
        rendered = self.renderer.render(wrong)
        with self.assertRaisesRegex(MaterialValidationError, "outside the approved"):
            self.renderer.validate(structure, replace(rendered, latex=latex_source(structure)))


if __name__ == "__main__":
    unittest.main()
