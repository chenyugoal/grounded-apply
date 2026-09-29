"""Statement provenance remains mandatory for current and historical materials."""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from grounded_apply.domain import ApprovalStatus, SourceType
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.repositories.latex_renderer import LatexResumeRenderer, normalized
from grounded_apply.services.materials import (
    MaterialBlocked, MaterialHistoryIntegrityError, MaterialService,
)
from grounded_apply.services.profile import (
    CreateImportProposal, CreateProfileReviewDecision, ProfileService,
    ProposedImportClaim, TextSourceSpan,
)
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from tests.test_materials import SyntheticRenderer, approved_fixture


STATEMENTS = (
    ("employment_description", "Fictional Engineer | Example City | Fictional Lab | May 2024-August 2026"),
    ("employment_description", "Contributed Python evaluation tools for a fictional project; did not lead the project."),
    ("skill_use", "Used SQL for a fictional prototype; no production ownership."),
)


class StatementMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reset_repository()

    def reset_repository(self) -> None:
        if hasattr(self, "repository"):
            self.repository.close()
        directory = tempfile.TemporaryDirectory(prefix="gapply-statement-materials-")
        self.addCleanup(directory.cleanup)
        self.repository = SQLiteRepository(Path(directory.name) / "fictional.db").initialize()
        self.addCleanup(self.repository.close)
        self.job_id, _ = approved_fixture(self.repository)
        self.profile = ProfileService(self.repository)
        self.service = MaterialService(self.repository, SyntheticRenderer())
        source = "\n".join(text for _, text in STATEMENTS) + "\n"
        proposals = []
        position = 0
        for kind, text in STATEMENTS:
            proposals.append(ProposedImportClaim(
                claim_type=kind, value=text, canonical_text=text,
                span=TextSourceSpan(start=position, end=position + len(text), text=text),
            ))
            position += len(text) + 1
        imported = self.profile.create_import_proposal(CreateImportProposal(
            idempotency_key="fictional-material-statements", source_text=source,
            expected_source_sha256=sha256(source.encode()).hexdigest(),
            proposals=tuple(proposals), content_policy_version=3,
            source_type=SourceType.USER_STATEMENT,
        ))
        self.statement_ids = tuple(claim.id for claim in imported.claims)
        for item in self.profile.list_review_items():
            if item.claim.id in self.statement_ids:
                self.profile.decide_review_item(CreateProfileReviewDecision(
                    claim_id=item.claim.id, review_token=item.review_token,
                    decision=ApprovalStatus.APPROVED, actor_id="fictional-reviewer",
                    idempotency_key="fictional-approve-" + item.claim.id,
                ))

    def build(self) -> dict[str, Any]:
        return self.service.build(
            self.job_id, self.statement_ids[:2], idempotency_key="fictional-statement-material",
            layout={"schema_version": 1, "presentations": {self.statement_ids[0]: "heading"}},
            questions=[{"id": "fictional-skill", "text": "Describe your project skills.",
                        "claim_ids": [self.statement_ids[2]], "required": True}],
        )

    def approve(self, result: dict[str, Any]) -> dict[str, object]:
        options = dict(bundle_sha256=result["bundle_sha256"], actor_id="fictional-reviewer",
                       idempotency_key="fictional-statement-material-approval")
        before = Path(self.repository.database).read_bytes()
        self.assertTrue(self.service.approve(result["material_id"], **options)["dry_run"])
        self.assertEqual(before, Path(self.repository.database).read_bytes())
        self.assertFalse(self.service.is_approved(result["material_id"]))
        self.assertTrue(self.service.approve(result["material_id"], **options, confirm=True)["ready"])
        approval = self.repository.get_material_approval(result["material_id"])
        assert approval is not None
        return approval

    def assert_historical_authority(self, material_id: str, approved_at: str) -> None:
        before = Path(self.repository.database).read_bytes()
        self.service.validate_historical_facts(material_id)
        self.service.validate_historical_approval_record(material_id)
        self.service.validate_historical_approval_facts(material_id)
        self.service.validate_historical_approval_eligibility(material_id)
        self.service.validate_historical_use(material_id, used_at=approved_at)
        self.service.validate_historical_inventory()
        self.assertEqual(before, Path(self.repository.database).read_bytes())
        self.assertFalse(self.repository._connection.in_transaction)

    @unittest.skipUnless(shutil.which("pdflatex") and importlib.util.find_spec("pypdf"), "Real PDF dependencies are required")
    def test_real_pdf_statement_build_approval_and_historical_audits(self) -> None:
        self.service = MaterialService(self.repository, LatexResumeRenderer())
        result = self.build()
        material = self.service.get(result["material_id"])
        self.assertFalse(result["ready"])
        self.assertTrue(material["pdf_bytes"].startswith(b"%PDF-"))
        # Changing origin does not introduce a new presentation transformation.
        self.assertEqual(material["structure"]["transformation"], "approved_text_selection@2")
        for _, text in STATEMENTS[:2]:
            self.assertIn(normalized(text.replace(" | ", " ")), normalized(material["extracted_text"]))
        answer = material["manifest"]["answers"][0]
        self.assertEqual(answer["status"], "draft")
        self.assertEqual(answer["factual_units"][0]["claim_ids"], [self.statement_ids[2]])
        self.assertNotIn(self.statement_ids[2], [unit["claim_id"] for unit in material["structure"]["units"]])
        for identifier in self.statement_ids:
            claim = self.repository.get_claim(identifier)
            evidence = self.repository.list_evidence(claim_id=identifier)
            self.assertEqual(claim["source_type"], "user_statement")
            self.assertEqual([item["source_type"] for item in evidence], ["user_statement"])
        approval = self.approve(result)
        self.assertTrue(self.service.is_approved(result["material_id"]))
        self.assert_historical_authority(result["material_id"], str(approval["approved_at"]))
        self.assertTrue(self.build()["replayed"])
        self.assertEqual(material, self.service.get(result["material_id"]))

    def test_later_statement_retirement_preserves_history_but_blocks_current_use(self) -> None:
        for selected in (True, False):
            with self.subTest(selected=selected):
                if not selected:
                    self.reset_repository()
                result = self.build()
                material_id = result["material_id"]
                approval = self.approve(result)
                original = self.repository.get_material_version(material_id)
                target = self.statement_ids[1 if selected else 2]
                original_claim = self.repository.get_claim(target)
                retired_at = datetime.now(UTC)
                self.assertGreater(retired_at, datetime.fromisoformat(str(approval["approved_at"])))
                lifecycle = ProfileLifecycleService(self.repository)
                options = dict(actor_id="fictional-reviewer", idempotency_key="fictional-retire", now=retired_at)
                preview = lifecycle.retire(target, **options)
                lifecycle.retire(target, **options, confirm=True, preview_token=preview.preview_token)
                self.assert_historical_authority(material_id, str(approval["approved_at"]))
                before = Path(self.repository.database).read_bytes()
                with self.assertRaises(MaterialBlocked):
                    self.service.get(material_id)
                with self.assertRaises(MaterialBlocked):
                    self.service.is_approved(material_id)
                # A retired answer changes the prepared-answer request digest,
                # so reusing its original build key fails the workflow audit.
                with self.assertRaises(MaterialBlocked if selected else RepositoryError):
                    self.build()
                with self.assertRaisesRegex(MaterialHistoryIntegrityError, "^Material historical use failed integrity checks$"):
                    self.service.validate_historical_use(material_id, used_at=retired_at.isoformat())
                self.assertEqual(self.service.list(self.job_id)[0]["status"], "needs_review")
                self.assertEqual(original, self.repository.get_material_version(material_id))
                self.assertEqual(original_claim, self.repository.get_claim(target))
                self.assertEqual(approval, self.repository.get_material_approval(material_id))
                self.assertEqual(before, Path(self.repository.database).read_bytes())

    def test_statement_provenance_corruption_blocks_current_and_historical_facts(self) -> None:
        first = True
        for selected in (True, False):
            for mutation in ("missing_association", "changed_digest", "claim_origin", "evidence_origin", "erased_markers"):
                with self.subTest(selected=selected, mutation=mutation):
                    if not first:
                        self.reset_repository()
                    first = False
                    result = self.build()
                    material_id = result["material_id"]
                    approval = self.approve(result)
                    target = self.statement_ids[1 if selected else 2]
                    evidence_id = self.repository.list_evidence(claim_id=target)[0]["id"]
                    # Deliberate corruption of a disposable fictional runtime.
                    # Saved material/PDF/approval bytes stay intact throughout.
                    with closing(sqlite3.connect(self.repository.database)) as connection, connection:
                        if mutation in ("missing_association", "erased_markers"):
                            connection.execute("DELETE FROM profile_import_review_items WHERE claim_id = ?", (target,))
                        elif mutation == "changed_digest":
                            connection.execute("UPDATE profile_import_review_items SET record_sha256 = ? WHERE claim_id = ?", ("f" * 64, target))
                        elif mutation == "claim_origin":
                            connection.execute("UPDATE claims SET source_type = 'imported_resume' WHERE id = ?", (target,))
                        else:
                            connection.execute("UPDATE evidence SET source_type = 'imported_resume' WHERE id = ?", (evidence_id,))
                        if mutation == "erased_markers":
                            connection.execute("UPDATE claims SET source_ref = 'fictional generic answer' WHERE id = ?", (target,))
                            connection.execute("UPDATE evidence SET source_ref = 'fictional generic answer', artifact_id = NULL, extraction_method = 'manual' WHERE id = ?", (evidence_id,))
                    before = Path(self.repository.database).read_bytes()
                    with self.assertRaises(RepositoryError):
                        self.service.get(material_id)
                    for method in (self.service.validate_historical_facts,
                                   self.service.validate_historical_approval_facts,
                                   self.service.validate_historical_approval_eligibility):
                        with self.assertRaises(MaterialHistoryIntegrityError) as error:
                            method(material_id)
                        self.assertNotIn(target, str(error.exception))
                    with self.assertRaises(MaterialHistoryIntegrityError):
                        self.service.validate_historical_use(material_id, used_at=str(approval["approved_at"]))
                    with self.assertRaises(MaterialHistoryIntegrityError):
                        self.service.validate_historical_inventory()
                    self.assertEqual(before, Path(self.repository.database).read_bytes())
                    self.assertFalse(self.repository._connection.in_transaction)


if __name__ == "__main__":
    unittest.main()
