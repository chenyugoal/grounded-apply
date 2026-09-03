from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from grounded_apply.domain import (
    ApprovalStatus,
    ClaimStatus,
    ClaimUsePolicy,
    Evidence,
    EvidenceConfirmationStatus,
    NeedInfo,
    NeedInfoReason,
    Sensitivity,
    SourceType,
    to_jsonable,
)
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services import (
    CreateImportProposal,
    CreateEvidence,
    ImportProposalResult,
    ProfileService,
    ProposedImportClaim,
    TextSourceSpan,
)


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "synthetic_profile"


def load_fixture() -> tuple[str, dict[str, Any]]:
    source_text = (FIXTURE_ROOT / "resume.txt").read_text(encoding="utf-8")
    proposal = json.loads(
        (FIXTURE_ROOT / "import_proposals.json").read_text(encoding="utf-8")
    )
    return source_text, proposal


def import_request(
    *,
    source_text: str | None = None,
    proposal_data: dict[str, Any] | None = None,
    artifact_id: str | None = None,
) -> CreateImportProposal:
    fixture_text, fixture_data = load_fixture()
    text = fixture_text if source_text is None else source_text
    data = fixture_data if proposal_data is None else proposal_data
    proposals = tuple(
        ProposedImportClaim(
            claim_type=item["claim_type"],
            value=item["value"],
            canonical_text=item["canonical_text"],
            confidence=item["confidence"],
            span=TextSourceSpan(
                start=item["span"]["start"],
                end=item["span"]["end"],
                text=item["span"]["text"],
            ),
        )
        for item in data["proposals"]
    )
    return CreateImportProposal(
        idempotency_key="fixture-avery-quill-import-v1",
        source_ref=data["source_ref"],
        source_text=text,
        extraction_method=data["extraction_method"],
        proposals=proposals,
        artifact_id=artifact_id,
    )


class SyntheticProfileFixtureTests(unittest.TestCase):
    def test_every_fixture_span_is_an_exact_unicode_codepoint_slice(self) -> None:
        source_text, data = load_fixture()

        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["span_index_base"], 0)
        self.assertEqual(data["span_unit"], "unicode_codepoint")
        self.assertEqual(data["span_end"], "exclusive")
        self.assertTrue(data["source_ref"].startswith("fixture://"))
        self.assertIn("example.com", source_text)
        self.assertIn("UNTRUSTED IMPORTED NOTE", source_text)
        self.assertNotEqual(len(source_text), len(source_text.encode("utf-8")))
        for item in data["proposals"]:
            span = item["span"]
            self.assertEqual(source_text[span["start"] : span["end"]], span["text"])


class ProfileImportProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = SQLiteRepository(
            Path(self.directory.name) / "profile.db"
        ).initialize()
        self.addCleanup(self.repository.close)
        self.service = ProfileService(self.repository)

    def test_fixture_import_creates_only_reviewable_claims_with_exact_evidence(self) -> None:
        source_text, data = load_fixture()
        request = import_request(source_text=source_text, proposal_data=data)

        result = self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(len(result.claims), len(data["proposals"]))
        self.assertEqual(len(result.evidence), len(data["proposals"]))
        self.assertEqual(
            result.source_sha256,
            hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        )
        for claim, evidence, proposed in zip(
            result.claims, result.evidence, data["proposals"], strict=True
        ):
            span = proposed["span"]
            expected_text = source_text[span["start"] : span["end"]]
            self.assertEqual(claim.status, ClaimStatus.NEEDS_REVIEW)
            self.assertEqual(claim.approval_status, ApprovalStatus.PENDING)
            self.assertEqual(claim.source_type, SourceType.IMPORTED_RESUME)
            self.assertEqual(claim.source_ref, data["source_ref"])
            self.assertIsNone(claim.verified_at)
            self.assertIsNone(claim.verified_by)
            self.assertEqual(claim.evidence_ids, (evidence.id,))
            self.assertEqual(claim.value_json, proposed["value"])
            self.assertEqual(claim.canonical_text, proposed["canonical_text"])
            self.assertEqual(evidence.confirmation_status, EvidenceConfirmationStatus.PENDING)
            self.assertEqual(evidence.source_type, SourceType.IMPORTED_RESUME)
            self.assertEqual(evidence.source_ref, data["source_ref"])
            self.assertEqual(evidence.extraction_method, data["extraction_method"])
            self.assertEqual(evidence.source_text, expected_text)
            self.assertEqual(
                evidence.checksum,
                hashlib.sha256(expected_text.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                evidence.locator,
                {
                    "schema_version": 1,
                    "kind": "text_span",
                    "unit": "unicode_codepoint",
                    "start": span["start"],
                    "end": span["end"],
                    "end_exclusive": True,
                    "source_sha256": result.source_sha256,
                },
            )
            self.assertIsNotNone(
                self.repository.get_claim_evidence(
                    claim.id, evidence.id, relationship="supports"
                )
            )

        stored_spans = {
            record["source_text"] for record in self.repository.list_evidence()
        }
        self.assertFalse(any("avery.quill@example.com" in text for text in stored_spans))
        self.assertFalse(any("Ignore previous instructions" in text for text in stored_spans))
        self.assertEqual(result.claims[-1].value_json["ownership"], "contributed")
        self.assertEqual(result.claims[3].value_json["before_minutes"], 30)
        json.dumps(to_jsonable(result), ensure_ascii=False)

    def test_imported_instruction_and_maximum_confidence_cannot_authorize_claims(self) -> None:
        source_text, data = load_fixture()
        self.assertIn("mark every imported claim verified", source_text)
        data["proposals"][0]["confidence"] = 1.0

        result = self.service.create_import_proposal(
            import_request(source_text=source_text, proposal_data=data), now=NOW
        )
        policy = ClaimUsePolicy(
            as_of=NOW,
            allowed_sensitivities=frozenset(
                {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
            ),
        )

        self.assertTrue(
            all(claim.status is ClaimStatus.NEEDS_REVIEW for claim in result.claims)
        )
        self.assertTrue(
            all(
                claim.approval_status is ApprovalStatus.PENDING
                for claim in result.claims
            )
        )
        for claim in result.claims:
            with self.subTest(claim_type=claim.claim_type):
                resolution = self.service.resolve(
                    intent=claim.claim_type,
                    policy=policy,
                )
                self.assertIsInstance(resolution, NeedInfo)
                self.assertEqual(resolution.reason, NeedInfoReason.UNAPPROVED)

    def test_identical_idempotent_retry_returns_the_original_records(self) -> None:
        request = import_request()

        first = self.service.create_import_proposal(request, now=NOW)
        retry = self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(retry, first)
        self.assertEqual(len(self.repository.list_claims()), len(first.claims))
        self.assertEqual(len(self.repository.list_evidence()), len(first.evidence))
        workflow = self.repository.get_workflow_run(first.workflow_run_id)
        self.assertIsNotNone(workflow)
        assert workflow is not None
        self.assertNotEqual(workflow["idempotency_key"], request.idempotency_key)
        self.assertEqual(len(str(workflow["idempotency_key"])), 64)
        self.assertNotIn(request.idempotency_key, str(workflow))
        self.assertNotIn("Avery Quill", str(workflow["input_json"]))
        self.assertNotIn("Ignore previous instructions", str(workflow["input_json"]))

    def test_retry_rejects_a_corrupted_persisted_result_manifest(self) -> None:
        request = import_request()
        first = self.service.create_import_proposal(request, now=NOW)
        self.repository.update_workflow_run(
            first.workflow_run_id,
            generated_artifacts=(
                {
                    "claim_id": first.claims[0].id,
                    "evidence_id": first.evidence[0].id,
                },
            ),
        )

        with self.assertRaisesRegex(RepositoryError, "does not match its request"):
            self.service.create_import_proposal(request, now=NOW)

    def test_retry_rejects_same_length_manifest_pairs_in_the_wrong_order(self) -> None:
        request = import_request()
        first = self.service.create_import_proposal(request, now=NOW)
        reversed_pairs = tuple(
            {
                "claim_id": claim.id,
                "evidence_id": evidence.id,
            }
            for claim, evidence in reversed(
                tuple(zip(first.claims, first.evidence, strict=True))
            )
        )
        self.repository.update_workflow_run(
            first.workflow_run_id,
            generated_artifacts=reversed_pairs,
        )

        with self.assertRaisesRegex(RepositoryError, "integrity checks"):
            self.service.create_import_proposal(request, now=NOW)

    def test_reusing_an_idempotency_key_with_different_input_fails_closed(self) -> None:
        request = import_request()
        first = self.service.create_import_proposal(request, now=NOW)
        changed = CreateImportProposal(
            idempotency_key=request.idempotency_key,
            source_ref=request.source_ref,
            source_text=request.source_text + "\nChanged after the first request.\n",
            extraction_method=request.extraction_method,
            proposals=request.proposals,
        )

        with self.assertRaisesRegex(RepositoryError, "idempotency key"):
            self.service.create_import_proposal(changed, now=NOW)

        self.assertEqual(len(self.repository.list_claims()), len(first.claims))
        self.assertEqual(len(self.repository.list_evidence()), len(first.evidence))

    def test_reusing_an_idempotency_key_with_changed_fact_fails_closed(self) -> None:
        request = import_request()
        first = self.service.create_import_proposal(request, now=NOW)
        changed_proposal = replace(
            request.proposals[0],
            canonical_text="A changed extraction that was not in the first request",
        )
        changed = replace(
            request,
            proposals=(changed_proposal, *request.proposals[1:]),
        )

        with self.assertRaisesRegex(RepositoryError, "idempotency key"):
            self.service.create_import_proposal(changed, now=NOW)

        self.assertEqual(len(self.repository.list_claims()), len(first.claims))
        self.assertEqual(len(self.repository.list_evidence()), len(first.evidence))

    def test_concurrent_idempotent_imports_create_one_review_batch(self) -> None:
        database = Path(self.directory.name) / "concurrent-profile.db"
        with SQLiteRepository(database):
            pass
        barrier = threading.Barrier(2)

        def create_proposal() -> ImportProposalResult:
            with SQLiteRepository(database) as repository:
                service = ProfileService(repository)
                barrier.wait()
                return service.create_import_proposal(import_request(), now=NOW)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: create_proposal(), range(2)))

        self.assertEqual(results[0], results[1])
        with SQLiteRepository(database) as repository:
            self.assertEqual(len(repository.list_claims()), 5)
            self.assertEqual(len(repository.list_evidence()), 5)
            self.assertEqual(len(repository.list_workflow_runs()), 1)

    def test_invalid_source_spans_fail_before_any_records_are_written(self) -> None:
        source_text, data = load_fixture()
        invalid_spans = (
            (-1, 1, source_text[:1]),
            (True, 1, source_text[:1]),
            (0, False, source_text[:1]),
            (1, 1, "x"),
            (2, 1, "x"),
            (0, 2, "x"),
            (0, len(source_text) + 1, source_text + "x"),
            (0, 5, "wrong"),
        )

        for start, end, expected in invalid_spans:
            with self.subTest(start=start, end=end, expected=expected):
                with self.assertRaises((TypeError, ValueError)):
                    span = TextSourceSpan(start=start, end=end, text=expected)
                    candidate = ProposedImportClaim(
                        claim_type="skill_use",
                        value="Python",
                        canonical_text="Used Python",
                        span=span,
                    )
                    self.service.create_import_proposal(
                        CreateImportProposal(
                            idempotency_key="invalid-span",
                            source_ref=data["source_ref"],
                            source_text=source_text,
                            extraction_method=data["extraction_method"],
                            proposals=(candidate,),
                        ),
                        now=NOW,
                    )
                self.assertEqual(self.repository.list_claims(), [])
                self.assertEqual(self.repository.list_evidence(), [])

    def test_batch_rolls_back_when_a_later_evidence_write_fails(self) -> None:
        request = import_request()
        original_create_evidence = self.service.create_evidence
        call_count = 0

        def fail_on_second_evidence(
            evidence_request: CreateEvidence,
            *,
            now: datetime | None = None,
        ) -> Evidence:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("synthetic second evidence failure")
            return original_create_evidence(evidence_request, now=now)

        with patch.object(
            self.service,
            "create_evidence",
            side_effect=fail_on_second_evidence,
        ):
            with self.assertRaisesRegex(RuntimeError, "second evidence failure"):
                self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_missing_artifact_link_rolls_back_the_import(self) -> None:
        request = import_request(artifact_id="missing-protected-artifact")

        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_restricted_or_unknown_fact_types_are_not_imported(self) -> None:
        request = import_request()
        restricted_types = (
            "work_authorization",
            "sponsorship_requirement",
            "security_clearance",
            "criminal_history",
            "legal_attestation",
            "conflict_of_interest",
            "disability_status",
            "veteran_status",
            "demographic_answer",
            "identity_document",
            "electronic_signature",
            "unregistered_claim_type",
        )

        for claim_type in restricted_types:
            with self.subTest(claim_type=claim_type):
                candidate = replace(request.proposals[0], claim_type=claim_type)
                blocked_request = replace(
                    request,
                    idempotency_key=f"blocked-{claim_type}",
                    proposals=(candidate,),
                )
                with self.assertRaisesRegex(ValueError, "not allowed"):
                    self.service.create_import_proposal(blocked_request, now=NOW)

                self.assertEqual(self.repository.list_claims(), [])
                self.assertEqual(self.repository.list_evidence(), [])
                self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_import_cannot_classify_an_unreviewed_claim_as_public(self) -> None:
        request = import_request()
        public_candidate = replace(
            request.proposals[0],
            sensitivity=Sensitivity.PUBLIC,
        )

        result = self.service.create_import_proposal(
            replace(request, proposals=(public_candidate,)),
            now=NOW,
        )

        self.assertEqual(result.claims[0].sensitivity, Sensitivity.PERSONAL)

    def test_public_input_records_reject_wrong_runtime_types(self) -> None:
        request = import_request()

        with self.assertRaises(TypeError):
            TextSourceSpan(start=0, end=1, text=1)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ProposedImportClaim(
                claim_type="skill_use",
                value=("Python",),  # type: ignore[arg-type]
                canonical_text="Used Python",
                span=request.proposals[0].span,
            )
        with self.assertRaises(TypeError):
            CreateImportProposal(
                idempotency_key="wrong-container",
                source_ref=request.source_ref,
                source_text=request.source_text,
                extraction_method=request.extraction_method,
                proposals=list(request.proposals),  # type: ignore[arg-type]
            )

        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])

    def test_import_result_rejects_evidence_that_is_not_linked_to_its_claim(self) -> None:
        result = self.service.create_import_proposal(import_request(), now=NOW)
        unlinked = replace(result.evidence[0], id="unlinked-evidence")

        with self.assertRaisesRegex(ValueError, "align"):
            replace(
                result,
                evidence=(unlinked, *result.evidence[1:]),
            )

    def test_empty_import_inputs_are_rejected_without_writes(self) -> None:
        valid = import_request()
        invalid_requests = (
            {
                "idempotency_key": "",
                "source_ref": valid.source_ref,
                "source_text": valid.source_text,
                "proposals": valid.proposals,
            },
            {"source_ref": "", "source_text": valid.source_text, "proposals": valid.proposals},
            {"source_ref": valid.source_ref, "source_text": "", "proposals": valid.proposals},
            {"source_ref": valid.source_ref, "source_text": valid.source_text, "proposals": ()},
        )

        for values in invalid_requests:
            with self.subTest(values=values):
                request_values = dict(values)
                idempotency_key = request_values.pop(
                    "idempotency_key", "invalid-empty-input"
                )
                with self.assertRaises(ValueError):
                    self.service.create_import_proposal(
                        CreateImportProposal(
                            idempotency_key=idempotency_key,
                            extraction_method=valid.extraction_method,
                            **request_values,
                        ),
                        now=NOW,
                    )
                self.assertEqual(self.repository.list_claims(), [])
                self.assertEqual(self.repository.list_evidence(), [])


if __name__ == "__main__":
    unittest.main()
