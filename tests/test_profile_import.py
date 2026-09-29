from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from unittest.mock import patch

import grounded_apply.services.profile as profile_service_module
import grounded_apply.services.profile_import_validation as profile_import_validation
from grounded_apply.domain import (
    ApprovalStatus,
    ClaimStatus,
    ClaimUsePolicy,
    Evidence,
    EvidenceConfirmationStatus,
    NeedInfo,
    NeedInfoReason,
    Scope,
    ScopeType,
    Sensitivity,
    SourceType,
    to_jsonable,
)
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services import (
    CreateClaim,
    CreateImportProposal,
    CreateEvidence,
    ImportProposalResult,
    PROFILE_IMPORT_EXTRACTOR_ID,
    PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION,
    PROFILE_IMPORT_MAX_SOURCE_BYTES,
    PROFILE_IMPORT_RESTRICTED_TAXONOMY_SHA256,
    PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION,
    ProfileService,
    ProfileReviewItem,
    ProposedImportClaim,
    TextSourceSpan,
    registered_profile_import_extractors,
    registered_profile_import_claim_types,
    registered_profile_import_restricted_categories,
)


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
NOW_TEXT = "2026-08-11T12:00:00Z"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "synthetic_profile"

SAFE_IMPORT_VALUES: dict[str, Any] = {
    "candidate_name": "Avery Quill",
    "contact_email": "avery.quill@example.com",
    "contact_phone": "+1 202-555-0142",
    "contact_location": "Fictional City, ZZ",
    "contact_url": "https://portfolio.example.com/avery",
    "achievement": "Improved a fictional build check",
    "certification": "Example Systems Certificate",
    "education": "Example University",
    "education_degree": "Bachelor of Synthetic Science",
    "education_field": "Synthetic Systems",
    "employment_dates": {"start": "2022-01", "end": "2025-03"},
    "employment_description": "Built fictional warehouse tooling",
    "employment_title": {
        "employer": "Example Robotics LLC",
        "title": "Software Engineer",
    },
    "language": "Esperanto",
    "portfolio_item": "https://portfolio.example.com/avery-quill",
    "project_contribution": {
        "project": "Moonshot Compiler",
        "contribution": "Rust parsing code",
        "ownership": "contributed",
    },
    "project_outcome": {
        "before_minutes": 30,
        "after_minutes": 10,
        "activity": "synthetic test setup",
    },
    "publication": "Testing Fictional Systems",
    "research_description": "Studied fictional evaluation methods",
    "skill_use": "Python",
}


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
        source_text=text,
        expected_source_sha256=data["source_sha256"],
        proposals=proposals,
    )


def single_proposal_request(
    *,
    claim_type: str = "skill_use",
    value: Any = "Python",
    canonical_text: str = "Used Python on a fictional project",
    source_text: str = (
        "Synthetic document header.\n"
        "Synthetic supporting evidence.\n"
        "Synthetic document footer."
    ),
    span: TextSourceSpan | None = None,
    idempotency_key: str = "single-safe-import",
) -> CreateImportProposal:
    if span is None:
        selected_text = "Synthetic supporting evidence."
        selected_start = source_text.index(selected_text)
        selected_span = TextSourceSpan(
            start=selected_start,
            end=selected_start + len(selected_text),
            text=selected_text,
        )
    else:
        selected_span = span
    return CreateImportProposal(
        idempotency_key=idempotency_key,
        source_text=source_text,
        expected_source_sha256=hashlib.sha256(
            source_text.encode("utf-8")
        ).hexdigest(),
        proposals=(
            ProposedImportClaim(
                claim_type=claim_type,
                value=value,
                canonical_text=canonical_text,
                span=selected_span,
            ),
        ),
    )


class SyntheticProfileFixtureTests(unittest.TestCase):
    def test_every_fixture_span_is_an_exact_unicode_codepoint_slice(self) -> None:
        source_text, data = load_fixture()

        self.assertEqual(data["schema_version"], PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION)
        self.assertEqual(data["span_index_base"], 0)
        self.assertEqual(data["span_unit"], "unicode_codepoint")
        self.assertEqual(data["span_end"], "exclusive")
        self.assertEqual(
            data["source_sha256"],
            hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn("source_ref", data)
        self.assertNotIn("extraction_method", data)
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

    def assert_rejected_before_storage(
        self,
        request: CreateImportProposal,
        *,
        forbidden_text: str | None = None,
        message_pattern: str | None = None,
    ) -> None:
        database = Path(self.repository.database)
        before_bytes = database.read_bytes()
        before_stat = database.stat()

        with patch.object(
            self.repository,
            "transaction",
            side_effect=AssertionError("storage transaction entered"),
        ) as transaction:
            with self.assertRaises(ValueError) as preview_error:
                self.service.preview_import_proposal(request)
            with self.assertRaises(ValueError) as persisted_error:
                self.service.create_import_proposal(request, now=NOW)

        transaction.assert_not_called()
        if message_pattern is not None:
            self.assertRegex(str(preview_error.exception), message_pattern)
            self.assertRegex(str(persisted_error.exception), message_pattern)
        if forbidden_text is not None:
            self.assertNotIn(forbidden_text, str(preview_error.exception))
            self.assertNotIn(forbidden_text, str(persisted_error.exception))
        self.assertEqual(database.read_bytes(), before_bytes)
        self.assertEqual(database.stat().st_mode, before_stat.st_mode)
        self.assertEqual(database.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(self.repository.list_artifacts(), [])
        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_registered_value_schemas_accept_every_allowed_claim_type(self) -> None:
        self.assertEqual(
            registered_profile_import_claim_types(),
            frozenset(SAFE_IMPORT_VALUES),
        )

        for index, (claim_type, value) in enumerate(SAFE_IMPORT_VALUES.items()):
            with self.subTest(claim_type=claim_type):
                request = single_proposal_request(
                    claim_type=claim_type,
                    value=value,
                    idempotency_key=f"valid-schema-{index}",
                )
                preview = self.service.preview_import_proposal(request)
                result = self.service.create_import_proposal(request, now=NOW)

                self.assertEqual(preview.proposal_count, 1)
                self.assertEqual(result.claims[0].claim_type, claim_type)
                self.assertEqual(result.claims[0].value_json, value)

        present = single_proposal_request(
            claim_type="employment_dates",
            value={"start": "2022-01", "end": "present"},
            idempotency_key="valid-current-employment",
        )
        self.service.preview_import_proposal(present)
        for ownership in ("supported", "contributed", "co-led", "led", "owned"):
            with self.subTest(ownership=ownership):
                contribution = single_proposal_request(
                    claim_type="project_contribution",
                    value={
                        "project": "Moonshot Compiler",
                        "contribution": "Synthetic parser work",
                        "ownership": ownership,
                    },
                    idempotency_key=f"valid-ownership-{ownership}",
                )
                self.service.preview_import_proposal(contribution)

        maximum_minutes = single_proposal_request(
            claim_type="project_outcome",
            value={
                "activity": "Synthetic annual maintenance",
                "before_minutes": 525_600,
                "after_minutes": 0,
            },
            idempotency_key="valid-maximum-minutes",
        )
        self.service.preview_import_proposal(maximum_minutes)

    def test_restricted_taxonomy_has_stable_versioned_categories(self) -> None:
        self.assertEqual(PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION, 1)
        self.assertEqual(
            PROFILE_IMPORT_RESTRICTED_TAXONOMY_SHA256,
            "65bff405611618692610a54a0cff99d77a32af01330cddc78b7d1a5f1ec529f7",
        )
        self.assertEqual(
            registered_profile_import_restricted_categories(),
            frozenset(
                {
                    "authentication_credential",
                    "conflict_of_interest",
                    "criminal_legal_attestation",
                    "demographic_self_identification",
                    "disability_status",
                    "government_identifier",
                    "security_clearance",
                    "veteran_status",
                    "work_authorization_immigration",
                }
            ),
        )

    def test_scalar_value_schemas_enforce_each_registered_length_limit(self) -> None:
        limits = {
            "achievement": 2048,
            "certification": 512,
            "education": 1024,
            "education_degree": 512,
            "education_field": 512,
            "employment_description": 2048,
            "language": 128,
            "portfolio_item": 2048,
            "publication": 2048,
            "skill_use": 256,
        }
        source_text = "S" * 5000 + "\nSynthetic evidence.\n" + "T" * 5000
        start = 5001
        span = TextSourceSpan(
            start=start,
            end=start + len("Synthetic evidence."),
            text="Synthetic evidence.",
        )

        for index, (claim_type, limit) in enumerate(limits.items()):
            with self.subTest(claim_type=claim_type):
                self.service.preview_import_proposal(
                    single_proposal_request(
                        claim_type=claim_type,
                        value="X" * limit,
                        source_text=source_text,
                        span=span,
                        idempotency_key=f"scalar-boundary-{index}",
                    )
                )
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        claim_type=claim_type,
                        value="X" * (limit + 1),
                        source_text=source_text,
                        span=span,
                        idempotency_key=f"scalar-over-limit-{index}",
                    ),
                    message_pattern="length limit",
                )

    def test_scalar_value_schemas_require_trimmed_visible_text(self) -> None:
        scalar_types = (
            "achievement",
            "certification",
            "education",
            "education_degree",
            "education_field",
            "employment_description",
            "language",
            "portfolio_item",
            "publication",
            "skill_use",
        )
        invalid_values = (
            ("", "non-blank trimmed"),
            (" ", "non-blank trimmed"),
            (" leading", "non-blank trimmed"),
            ("trailing ", "non-blank trimmed"),
            ("hidden\u200btext", "one line without control or format"),
        )

        for type_index, claim_type in enumerate(scalar_types):
            for value_index, (value, message_pattern) in enumerate(invalid_values):
                with self.subTest(claim_type=claim_type, value_index=value_index):
                    self.assert_rejected_before_storage(
                        single_proposal_request(
                            claim_type=claim_type,
                            value=value,
                            idempotency_key=(
                                f"invalid-scalar-text-{type_index}-{value_index}"
                            ),
                        ),
                        message_pattern=message_pattern,
                    )

    def test_structured_value_schemas_accept_exact_field_boundaries(self) -> None:
        source_text = "S" * 5000 + "\nSynthetic evidence.\n" + "T" * 5000
        span = TextSourceSpan(
            start=5001,
            end=5001 + len("Synthetic evidence."),
            text="Synthetic evidence.",
        )
        boundary_values: tuple[tuple[str, Any], ...] = (
            (
                "employment_title",
                {"employer": "E" * 512, "title": "T" * 512},
            ),
            (
                "employment_dates",
                {"start": "1900-01", "end": "2099-12"},
            ),
            (
                "project_contribution",
                {
                    "project": "P" * 512,
                    "contribution": "C" * 2048,
                    "ownership": "owned",
                },
            ),
            (
                "project_outcome",
                {
                    "activity": "A" * 512,
                    "before_minutes": 525_600,
                    "after_minutes": 525_600,
                },
            ),
        )

        for index, (claim_type, value) in enumerate(boundary_values):
            with self.subTest(claim_type=claim_type):
                self.service.preview_import_proposal(
                    single_proposal_request(
                        claim_type=claim_type,
                        value=value,
                        source_text=source_text,
                        span=span,
                        idempotency_key=f"structured-boundary-{index}",
                    )
                )

    def test_registered_value_schemas_reject_wrong_containers_before_storage(
        self,
    ) -> None:
        structured_types = {
            "employment_dates",
            "employment_title",
            "project_contribution",
            "project_outcome",
        }

        for index, claim_type in enumerate(SAFE_IMPORT_VALUES):
            with self.subTest(claim_type=claim_type):
                wrong_value: Any = (
                    "not a structured value"
                    if claim_type in structured_types
                    else {"unexpected": "object"}
                )
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        claim_type=claim_type,
                        value=wrong_value,
                        idempotency_key=f"wrong-container-{index}",
                    )
                )

    def test_structured_value_schemas_reject_invalid_fields_before_storage(
        self,
    ) -> None:
        invalid_values: tuple[tuple[str, Any], ...] = (
            ("employment_title", {"employer": "Example Robotics LLC"}),
            (
                "employment_title",
                {
                    "employer": "Example Robotics LLC",
                    "title": "Engineer",
                    "private": "unexpected",
                },
            ),
            ("employment_title", {"employer": "", "title": "Engineer"}),
            ("employment_title", {"employer": 7, "title": "Engineer"}),
            ("employment_title", {"employer": "E" * 513, "title": "Engineer"}),
            ("employment_title", {"employer": "Example", "title": " "}),
            ("employment_title", {"employer": "Example", "title": 7}),
            ("employment_title", {"employer": "Example", "title": "T" * 513}),
            ("employment_dates", {"start": 2022, "end": "2025-03"}),
            ("employment_dates", {"start": "2022-13", "end": "2025-03"}),
            ("employment_dates", {"start": "2022-01", "end": "2025-13"}),
            ("employment_dates", {"start": "1899-12", "end": "2025-03"}),
            ("employment_dates", {"start": "2022-01", "end": "2100-01"}),
            ("employment_dates", {"start": "2025-03", "end": "2022-01"}),
            ("employment_dates", {"start": "2022-01", "end": None}),
            ("employment_dates", {"start": "2022-01", "end": "Present"}),
            (
                "employment_dates",
                {"start": "2022-01", "end": "2025-03", "extra": "no"},
            ),
            (
                "project_outcome",
                {
                    "before_minutes": True,
                    "after_minutes": 10,
                    "activity": "synthetic setup",
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 30,
                    "after_minutes": 525_601,
                    "activity": "synthetic setup",
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 30,
                    "after_minutes": 10,
                    "activity": 7,
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 30,
                    "after_minutes": 10,
                    "activity": " ",
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 30,
                    "after_minutes": 10,
                    "activity": "A" * 513,
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 30,
                    "after_minutes": 10,
                    "activity": "setup",
                    "extra": 1,
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 525_601,
                    "after_minutes": 10,
                    "activity": "synthetic setup",
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 30,
                    "after_minutes": -1,
                    "activity": "synthetic setup",
                },
            ),
            (
                "project_outcome",
                {
                    "before_minutes": 30.0,
                    "after_minutes": 10,
                    "activity": "synthetic setup",
                },
            ),
            (
                "project_contribution",
                {"project": "Moonshot", "contribution": "Parsing code"},
            ),
            (
                "project_contribution",
                {
                    "project": "Moonshot",
                    "contribution": "Parsing code",
                    "ownership": "invented-owner-level",
                },
            ),
            (
                "project_contribution",
                {"project": 7, "contribution": "Parsing", "ownership": "owned"},
            ),
            (
                "project_contribution",
                {"project": " ", "contribution": "Parsing", "ownership": "owned"},
            ),
            (
                "project_contribution",
                {"project": "P" * 513, "contribution": "Parsing", "ownership": "owned"},
            ),
            (
                "project_contribution",
                {"project": "Moonshot", "contribution": 7, "ownership": "owned"},
            ),
            (
                "project_contribution",
                {"project": "Moonshot", "contribution": " ", "ownership": "owned"},
            ),
            (
                "project_contribution",
                {
                    "project": "Moonshot",
                    "contribution": "C" * 2049,
                    "ownership": "owned",
                },
            ),
            (
                "project_contribution",
                {"project": "Moonshot", "contribution": "Parsing", "ownership": 7},
            ),
            (
                "project_contribution",
                {"project": "Moonshot", "contribution": "Parsing", "ownership": " "},
            ),
            (
                "project_contribution",
                {
                    "project": "Moonshot",
                    "contribution": "Parsing",
                    "ownership": "owned",
                    "extra": "no",
                },
            ),
            ("achievement", "x" * 2049),
            ("language", "English\nembedded second claim"),
        )

        for index, (claim_type, value) in enumerate(invalid_values):
            with self.subTest(index=index, claim_type=claim_type):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        claim_type=claim_type,
                        value=value,
                        idempotency_key=f"invalid-fields-{index}",
                    )
                )

    def test_mutated_nested_value_is_revalidated_at_service_call_time(self) -> None:
        mutable_value: dict[str, Any] = {
            "employer": "Example Robotics LLC",
            "title": "Software Engineer",
        }
        request = single_proposal_request(
            claim_type="employment_title",
            value=mutable_value,
            idempotency_key="mutated-after-construction",
        )
        self.service.preview_import_proposal(request)
        mutable_value["title"] = "api_key=SYNTHETIC_NOT_A_KEY_1234567890"

        self.assert_rejected_before_storage(request)

    def test_nested_value_is_snapshotted_before_the_storage_transaction(self) -> None:
        mutable_value: dict[str, Any] = {
            "employer": "Example Robotics LLC",
            "title": "Software Engineer",
        }
        request = single_proposal_request(
            claim_type="employment_title",
            value=mutable_value,
            idempotency_key="mutated-at-transaction-entry",
        )
        original_transaction = self.repository.transaction

        def mutate_then_open_transaction() -> Any:
            mutable_value["title"] = "api_key=SYNTHETIC_NOT_A_KEY_1234567890"
            return original_transaction()

        with patch.object(
            self.repository,
            "transaction",
            side_effect=mutate_then_open_transaction,
        ):
            result = self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(
            result.claims[0].value_json,
            {"employer": "Example Robotics LLC", "title": "Software Engineer"},
        )
        self.assertEqual(
            self.repository.list_claims()[0]["value_json"],
            '{"employer":"Example Robotics LLC","title":"Software Engineer"}',
        )

    def test_scope_and_span_are_snapshotted_before_the_storage_transaction(
        self,
    ) -> None:
        safe_scope_id = "qzxqzxqz"
        unsafe_scope_id = "token=SYNTHETIC_NOT_A_TOKEN_1234567890"
        mutable_scope = Scope(type=ScopeType.COMPANY, id=safe_scope_id)
        safe = single_proposal_request()
        mutable_span = safe.proposals[0].span
        original_start = mutable_span.start
        original_end = mutable_span.end
        original_text = mutable_span.text
        request = replace(
            safe,
            idempotency_key="mutated-scope-and-span-at-transaction-entry",
            proposals=(
                replace(
                    safe.proposals[0],
                    scope=mutable_scope,
                    span=mutable_span,
                ),
            ),
        )
        original_transaction = self.repository.transaction

        def mutate_then_open_transaction() -> Any:
            object.__setattr__(mutable_scope, "id", unsafe_scope_id)
            object.__setattr__(mutable_span, "start", 0)
            object.__setattr__(mutable_span, "end", 1)
            object.__setattr__(mutable_span, "text", request.source_text[:1])
            return original_transaction()

        with patch.object(
            self.repository,
            "transaction",
            side_effect=mutate_then_open_transaction,
        ):
            result = self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(result.claims[0].scope.id, safe_scope_id)
        self.assertEqual(result.evidence[0].source_text, original_text)
        self.assertEqual(result.evidence[0].locator["start"], original_start)
        self.assertEqual(result.evidence[0].locator["end"], original_end)
        self.assertNotIn(unsafe_scope_id, str(self.repository.list_claims()))

        retry_request = replace(
            request,
            proposals=(
                replace(
                    request.proposals[0],
                    scope=Scope(type=ScopeType.COMPANY, id=safe_scope_id),
                    span=TextSourceSpan(
                        start=original_start,
                        end=original_end,
                        text=original_text,
                    ),
                ),
            ),
        )
        self.assertEqual(
            self.service.create_import_proposal(retry_request, now=NOW),
            result,
        )

    def test_selected_evidence_has_per_claim_and_batch_limits(self) -> None:
        prefix = "P" * 5000
        selected = "E" * 4096
        suffix = "S" * 5000
        source_text = prefix + "\n" + selected + "\n" + suffix
        span = TextSourceSpan(
            start=len(prefix) + 1,
            end=len(prefix) + 1 + len(selected),
            text=selected,
        )
        one = single_proposal_request(
            source_text=source_text,
            span=span,
            idempotency_key="evidence-batch-limit",
        )
        self.service.preview_import_proposal(one)
        self.service.preview_import_proposal(replace(one, proposals=one.proposals * 16))
        too_many = replace(one, proposals=one.proposals * 17)

        self.assert_rejected_before_storage(
            too_many,
            message_pattern="batch limit",
        )

        oversized_selected = selected + "E"
        oversized_source = prefix + "\n" + oversized_selected + "\n" + suffix
        oversized_span = TextSourceSpan(
            start=len(prefix) + 1,
            end=len(prefix) + 1 + len(oversized_selected),
            text=oversized_selected,
        )
        self.assert_rejected_before_storage(
            single_proposal_request(
                source_text=oversized_source,
                span=oversized_span,
                idempotency_key="evidence-item-limit",
            ),
            message_pattern="selected evidence exceeds the atomic claim limit",
        )

        sixteen_lines = "\n".join(f"synthetic evidence line {index}" for index in range(16))
        line_source = "P" * 500 + sixteen_lines + "S" * 500
        line_span = TextSourceSpan(
            start=500,
            end=500 + len(sixteen_lines),
            text=sixteen_lines,
        )
        self.service.preview_import_proposal(
            single_proposal_request(
                source_text=line_source,
                span=line_span,
                idempotency_key="evidence-line-boundary",
            )
        )

        seventeen_lines = sixteen_lines + "\nsynthetic evidence line 16"
        seventeen_line_source = "P" * 500 + seventeen_lines + "S" * 500
        self.assert_rejected_before_storage(
            single_proposal_request(
                source_text=seventeen_line_source,
                span=TextSourceSpan(
                    start=500,
                    end=500 + len(seventeen_lines),
                    text=seventeen_lines,
                ),
                idempotency_key="evidence-line-over-limit",
            ),
            message_pattern="too many lines",
        )

        canonical_boundary = single_proposal_request(
            canonical_text="C" * 2048,
            source_text=source_text,
            span=span,
        )
        self.service.preview_import_proposal(canonical_boundary)
        self.assert_rejected_before_storage(
            replace(canonical_boundary, proposals=(replace(
                canonical_boundary.proposals[0],
                canonical_text="C" * 2049,
            ),)),
            message_pattern="canonical text exceeds the atomic claim limit",
        )

    def test_document_coverage_accepts_79_percent_and_rejects_80_percent(self) -> None:
        source_text = "A" * 100
        accepted_text = source_text[:79]
        accepted = single_proposal_request(
            value="Synthetic narrow fact",
            canonical_text="Synthetic narrow fact",
            source_text=source_text,
            span=TextSourceSpan(start=0, end=79, text=accepted_text),
            idempotency_key="coverage-79-percent",
        )
        self.service.preview_import_proposal(accepted)

        rejected_text = source_text[:80]
        self.assert_rejected_before_storage(
            single_proposal_request(
                value="Synthetic boundary fact",
                canonical_text="Synthetic boundary fact",
                source_text=source_text,
                span=TextSourceSpan(start=0, end=80, text=rejected_text),
                idempotency_key="coverage-80-percent",
            ),
            message_pattern="too broad|cover too much",
        )

    def test_service_source_limit_is_exactly_16_mib_of_utf8(self) -> None:
        exact_source = "é" * (PROFILE_IMPORT_MAX_SOURCE_BYTES // 2)
        proposal = ProposedImportClaim(
            claim_type="skill_use",
            value="Python",
            canonical_text="Synthetic Python use",
            span=TextSourceSpan(start=0, end=1, text="é"),
        )
        request = CreateImportProposal(
            idempotency_key="exact-service-source-byte-limit",
            source_text=exact_source,
            expected_source_sha256=hashlib.sha256(
                exact_source.encode("utf-8")
            ).hexdigest(),
            proposals=(proposal,),
        )

        self.assertEqual(len(request.source_text.encode("utf-8")), 16 * 1024 * 1024)
        self.service.preview_import_proposal(request)
        with self.assertRaisesRegex(ValueError, "16 MiB"):
            replace(request, source_text=exact_source + "a")

    def test_maximum_safe_batch_with_assignment_punctuation_is_accepted(self) -> None:
        evidence_text = "Synthetic evidence anchor."
        source_text = evidence_text + " " + ("Z" * 10_000)
        proposals = tuple(
            ProposedImportClaim(
                claim_type="skill_use",
                value=f"Tool{index}",
                canonical_text=f"Synthetic work {index}: built tool",
                span=TextSourceSpan(
                    start=0,
                    end=len(evidence_text),
                    text=evidence_text,
                ),
            )
            for index in range(1000)
        )
        request = CreateImportProposal(
            idempotency_key="safe-maximum-proposal-batch",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        preview = self.service.preview_import_proposal(request)

        self.assertEqual(preview.proposal_count, 1000)

    def test_total_persisted_metadata_limit_is_service_owned(self) -> None:
        safe = single_proposal_request()
        request = replace(
            safe,
            proposals=(safe.proposals[0],) * 3,
        )
        unique_input_size = (
            len(request.source_ref)
            + len(request.extractor_id)
            + len(request.source_artifact_id)
            + sum(len(proposal.subject_type) for proposal in request.proposals)
        )
        persisted_size = len(request.proposals) * (
            (2 * len(request.source_ref))
            + len(request.extractor_id)
            + len(request.source_artifact_id)
        ) + sum(len(proposal.subject_type) for proposal in request.proposals)
        test_limit = (unique_input_size + persisted_size) // 2
        self.assertLess(unique_input_size, test_limit)
        self.assertLess(test_limit, persisted_size)
        with patch(
            "grounded_apply.services.profile."
            "PROFILE_IMPORT_MAX_TOTAL_METADATA_CODEPOINTS",
            test_limit,
        ):
            self.assert_rejected_before_storage(
                request,
                message_pattern="metadata exceeds the batch limit",
            )

    def test_import_scope_identifier_is_revalidated_at_service_call_time(self) -> None:
        mutable_scope = Scope(type=ScopeType.COMPANY, id="synthetic-company")
        safe = single_proposal_request()
        request = replace(
            safe,
            proposals=(replace(safe.proposals[0], scope=mutable_scope),),
        )
        object.__setattr__(mutable_scope, "id", "\ud800")

        self.assert_rejected_before_storage(
            request,
            message_pattern="valid Unicode",
        )

    def test_source_identity_and_extractor_are_application_owned(self) -> None:
        request = single_proposal_request()
        forbidden_values = {
            "source_sha256": "0" * 64,
            "source_ref": "file:///Users/SYNTHETIC_PRIVATE_PERSON/resume.txt",
            "source_artifact_id": "forged-artifact",
            "extractor_id": "trusted-looking@999",
        }

        for field_name, value in forbidden_values.items():
            with self.subTest(field_name=field_name):
                with self.assertRaises((TypeError, ValueError)):
                    replace(request, **{field_name: value})

        object.__setattr__(request, "source_sha256", "0" * 64)
        object.__setattr__(
            request,
            "source_ref",
            "file:///Users/SYNTHETIC_PRIVATE_PERSON/resume.txt",
        )
        object.__setattr__(request, "source_artifact_id", "forged-artifact")
        object.__setattr__(request, "extractor_id", "trusted-looking@999")

        preview = self.service.preview_import_proposal(request)
        result = self.service.create_import_proposal(request, now=NOW)

        expected_digest = hashlib.sha256(request.source_text.encode("utf-8")).hexdigest()
        self.assertEqual(preview.source_sha256, expected_digest)
        self.assertEqual(result.source_ref, f"sha256:{expected_digest}")
        self.assertEqual(result.extractor_id, PROFILE_IMPORT_EXTRACTOR_ID)
        self.assertNotIn("SYNTHETIC_PRIVATE_PERSON", str(result))

    def test_source_digest_assertion_and_extractor_registry_fail_closed(self) -> None:
        request = single_proposal_request()

        with self.assertRaisesRegex(ValueError, "source digest"):
            replace(request, expected_source_sha256="0" * 64)
        with patch(
            "grounded_apply.services.profile.PROFILE_IMPORT_EXTRACTOR_ID",
            "unregistered.profile-import@1",
        ):
            with self.assertRaisesRegex(ValueError, "registered identifier"):
                replace(request)

        self.assertEqual(self.repository.list_artifacts(), [])
        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

    def assert_disallowed_content_on_every_surface(
        self,
        content: str,
        *,
        message_pattern: str = "disallowed sensitive",
    ) -> None:
        evidence_prefix = "Synthetic unselected prefix text. " * 3
        evidence_suffix = " Synthetic unselected suffix text." * 3
        evidence_source = evidence_prefix + content + evidence_suffix
        requests = (
            single_proposal_request(
                value=content,
                idempotency_key="unsafe-scalar-value",
            ),
            single_proposal_request(
                claim_type="employment_title",
                value={"employer": "Example Robotics LLC", "title": content},
                idempotency_key="unsafe-structured-value",
            ),
            single_proposal_request(
                canonical_text=content,
                idempotency_key="unsafe-canonical-text",
            ),
            single_proposal_request(
                source_text=evidence_source,
                span=TextSourceSpan(
                    start=len(evidence_prefix),
                    end=len(evidence_prefix) + len(content),
                    text=content,
                ),
                idempotency_key="unsafe-selected-evidence",
            ),
        )

        for index, request in enumerate(requests):
            with self.subTest(surface=index):
                self.assert_rejected_before_storage(
                    request,
                    forbidden_text=content,
                    message_pattern=message_pattern,
                )

    def test_work_authorization_cannot_hide_in_allowed_content(self) -> None:
        for content in (
            "Authorized to work in the United States.",
            "Able to work in the United States.",
            "Requires sponsorship.",
            "Sponsorship will be required.",
            "No sponsorship required.",
            "Has the legal right to work in the United States.",
            "Legally permitted to work in the U.S.",
            "Has a valid work permit.",
            "Employment eligibility confirmed.",
            "Currently on OPT.",
            "currently on opt.",
            "OPT eligible.",
            "CPT holder.",
            "H-1B visa holder.",
            "I hold an H-1B.",
            "EAD holder.",
            "Canadian citizen.",
            "canadian citizen.",
            "CANADIAN CITIZEN.",
            "Citizen of Canada.",
        ):
            with self.subTest(content=content):
                self.assert_disallowed_content_on_every_surface(content)

    def test_restricted_taxonomy_cannot_hide_in_allowed_content(self) -> None:
        cases = (
            (
                "clearance",
                "Synthetic candidate holds an active Secret security clearance.",
            ),
            ("clearance_direct", "I have a Secret clearance."),
            ("clearance_possessive", "My security clearance is Secret."),
            (
                "clearance_expiration",
                "Active Secret clearance valid through 2028.",
            ),
            (
                "clearance_grant",
                "Active Top Secret clearance granted in 2024.",
            ),
            (
                "clearance_eligibility",
                "Eligible for security clearance upon hire.",
            ),
            (
                "clearance_conditional",
                "Able to obtain a Secret clearance if required.",
            ),
            ("clearance_ts_sci", "TS/SCI with polygraph."),
            (
                "clearance_polygraph",
                "Holds active Secret clearance with polygraph.",
            ),
            ("clearance_parenthetical", "Active Secret clearance (DoD)."),
            ("public_trust", "Public Trust clearance."),
            ("visa_tn", "TN visa holder."),
            (
                "veteran",
                "Synthetic candidate veteran status: protected veteran.",
            ),
            (
                "disability",
                "Synthetic candidate disability status: yes.",
            ),
            ("disability_direct", "I am disabled."),
            (
                "criminal",
                "Synthetic candidate criminal history: no convictions.",
            ),
            ("criminal_direct", "I have never been convicted."),
            (
                "legal_attestation",
                "Synthetic candidate certifies these answers under penalty of perjury.",
            ),
            (
                "legal_attestation_direct",
                "I certify that this application is true and complete.",
            ),
            (
                "conflict_of_interest",
                "Synthetic candidate conflict of interest: none disclosed.",
            ),
            (
                "conflict_attestation",
                "Synthetic candidate does not have a conflict of interest.",
            ),
            (
                "outside_employment_conflict",
                "Synthetic candidate has no outside employment conflict.",
            ),
            (
                "demographic_race",
                "Synthetic candidate race or ethnicity: prefer not to answer.",
            ),
            (
                "demographic_gender",
                "Synthetic candidate gender identity: fictional response withheld.",
            ),
            ("demographic_age", "My age is 42."),
            ("demographic_birth_date", "My date of birth is 2000-01-01."),
            ("demographic_marital", "I am married."),
            ("demographic_pronouns", "I use she/her pronouns."),
            ("demographic_religion", "I am Christian."),
        )

        for category, content in cases:
            with self.subTest(category=category):
                self.assert_disallowed_content_on_every_surface(content)

    def test_restricted_taxonomy_assignment_aliases_are_rejected(self) -> None:
        cases = (
            ("clearance", "security_clearance=SYNTHETIC_SECRET"),
            ("public_trust", "public_trust_status=SYNTHETIC_ACTIVE"),
            ("veteran", "veteran_status=SYNTHETIC_YES"),
            ("veteran_short", "veteran=SYNTHETIC_NO"),
            ("disability", "disability_status=SYNTHETIC_NO"),
            ("criminal", "criminal_history=SYNTHETIC_NO"),
            ("legal_attestation", "legal_attestation=SYNTHETIC_ACCEPTED"),
            ("conflict", "conflict_of_interest=SYNTHETIC_NONE"),
            ("demographic", "race_ethnicity=SYNTHETIC_WITHHELD"),
            ("sponsor", "sponsor=SYNTHETIC_YES"),
            ("work_authorisation", "workauthorisation=SYNTHETIC_YES"),
            (
                "employment_authorization",
                "employmentauthorization=SYNTHETIC_YES",
            ),
            ("driver_licence", "driver_licence=SYNTHETIC_D00000000"),
        )

        for index, (category, content) in enumerate(cases):
            with self.subTest(category=category):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=content,
                        idempotency_key=f"restricted-assignment-{index}",
                    ),
                    forbidden_text=content,
                    message_pattern="disallowed sensitive",
                )

    def test_restricted_taxonomy_label_blocks_answer_only_span(self) -> None:
        cases = (
            ("clearance", "Security clearance", "Active Secret"),
            ("veteran", "Veteran status", "Yes"),
            ("disability", "Disability status", "No"),
            ("criminal", "Criminal history", "No"),
            ("legal_attestation", "Legal attestation", "I agree"),
            ("conflict", "Conflict of interest", "None"),
            ("demographic", "Race/ethnicity", "Prefer not to answer"),
            ("gender", "Gender", "Male"),
            ("veteran_question", "Are you a protected veteran?", "No"),
            ("disability_question", "Do you have a disability?", "No"),
            (
                "conflict_question",
                "Do you have any conflicts of interest?",
                "No",
            ),
            (
                "clearance_question",
                "Do you hold a security clearance?",
                "No",
            ),
            (
                "criminal_question",
                "Have you ever been convicted of a crime?",
                "No",
            ),
            (
                "criminal_felony_question",
                "Have you been convicted of a felony?",
                "No",
            ),
            (
                "optional_demographic",
                "Race / Ethnicity (optional)",
                "Prefer not to answer",
            ),
            ("required_gender", "Gender *", "Male"),
            ("gender_question", "What is your gender?", "Male"),
            (
                "work_authorization_question",
                "Are you legally authorized to work in the United States?",
                "Yes",
            ),
            (
                "sponsorship_question",
                "Will you now or in the future require sponsorship?",
                "No",
            ),
            (
                "clearance_level_question",
                "What level of security clearance do you currently hold?",
                "Top Secret",
            ),
            (
                "disability_history_question",
                "Do you have a disability or have you ever had one?",
                "No",
            ),
            (
                "potential_conflict_question",
                "Do you have any actual or potential conflicts of interest?",
                "No",
            ),
            (
                "arrest_charge_question",
                "Have you ever been arrested or charged with a crime?",
                "No",
            ),
            ("race_question", "Please select your race", "Withheld"),
            (
                "race_select_one",
                "Race/Ethnicity (Select one)",
                "Withheld",
            ),
            (
                "ethnicity_question",
                "Are you Hispanic or Latino?",
                "Withheld",
            ),
            ("age_question", "What is your age?", "Withheld"),
            (
                "signature_instruction",
                "Type your full legal name as your electronic signature",
                "Synthetic Name",
            ),
        )

        for index, (category, label, selected) in enumerate(cases):
            source_text = (
                f"Synthetic application header.\n{label}\n{selected}\n"
                "Synthetic application footer."
            )
            start = source_text.index(selected)
            with self.subTest(category=category):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=selected,
                        canonical_text="Synthetic answer requiring explicit review",
                        source_text=source_text,
                        span=TextSourceSpan(
                            start=start,
                            end=start + len(selected),
                            text=selected,
                        ),
                        idempotency_key=f"restricted-answer-only-{index}",
                    ),
                    forbidden_text=selected,
                    message_pattern="disallowed sensitive",
                )

    def test_restricted_taxonomy_normalizes_obfuscated_preceding_labels(self) -> None:
        cases = (
            ("Security%20clearance", "Active Secret"),
            ("Veteran\u200b status", "Yes"),
            ("Race%252Fethnicity", "Prefer not to answer"),
        )
        for index, (label, selected) in enumerate(cases):
            source_text = (
                f"Synthetic application header.\n{label}\n{selected}\n"
                "Synthetic application footer."
            )
            start = source_text.index(selected)
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=selected,
                        canonical_text="Synthetic answer requiring explicit review",
                        source_text=source_text,
                        span=TextSourceSpan(
                            start=start,
                            end=start + len(selected),
                            text=selected,
                        ),
                        idempotency_key=f"obfuscated-restricted-label-{index}",
                    ),
                    forbidden_text=selected,
                    message_pattern="disallowed sensitive",
                )

    def test_restricted_taxonomy_label_blocks_same_line_answer_only_span(self) -> None:
        cases = (
            ("Gender", " ", "Male"),
            ("Have you ever been convicted of a crime?", " ", "No"),
            ("Race / Ethnicity (optional)", " ", "Prefer not to answer"),
            ("Security clearance", " " * 300, "Top Secret"),
        )
        for index, (label, separator, selected) in enumerate(cases):
            source_text = (
                f"Synthetic application header.\n{label}{separator}{selected}\n"
                "Synthetic application footer."
            )
            start = source_text.index(selected)
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=selected,
                        canonical_text="Synthetic answer requiring explicit review",
                        source_text=source_text,
                        span=TextSourceSpan(
                            start=start,
                            end=start + len(selected),
                            text=selected,
                        ),
                        idempotency_key=f"same-line-restricted-label-{index}",
                    ),
                    forbidden_text=selected,
                    message_pattern="disallowed sensitive",
                )

    def test_restricted_taxonomy_label_blocks_answer_after_blank_lines(self) -> None:
        cases = (
            ("Gender", "Male"),
            ("Veteran status", "No"),
            ("Security clearance", "No"),
            ("Race / Ethnicity", "Prefer not to answer"),
        )
        for index, (label, selected) in enumerate(cases):
            source_text = (
                f"Synthetic application header.\n{label}\n\n{selected}\n"
                "Synthetic application footer."
            )
            start = source_text.index(selected)
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=selected,
                        canonical_text="Synthetic answer requiring explicit review",
                        source_text=source_text,
                        span=TextSourceSpan(
                            start=start,
                            end=start + len(selected),
                            text=selected,
                        ),
                        idempotency_key=f"blank-line-restricted-label-{index}",
                    ),
                    forbidden_text=selected,
                    message_pattern="disallowed sensitive",
                )

    def test_same_line_context_limit_fails_closed_at_the_boundary(self) -> None:
        selected = "Python"
        exact_prefix = "x" * 512
        exact_source = exact_prefix + selected + "\nSynthetic footer."
        exact_start = len(exact_prefix)
        preview = self.service.preview_import_proposal(
            single_proposal_request(
                value=selected,
                source_text=exact_source,
                span=TextSourceSpan(
                    start=exact_start,
                    end=exact_start + len(selected),
                    text=selected,
                ),
                idempotency_key="exact-same-line-context-limit",
            )
        )
        self.assertEqual(preview.proposal_count, 1)

        overlong_prefix = "x" * 513
        overlong_source = overlong_prefix + selected + "\nSynthetic footer."
        overlong_start = len(overlong_prefix)
        self.assert_rejected_before_storage(
            single_proposal_request(
                value=selected,
                source_text=overlong_source,
                span=TextSourceSpan(
                    start=overlong_start,
                    end=overlong_start + len(selected),
                    text=selected,
                ),
                idempotency_key="overlong-same-line-context",
            ),
            message_pattern="same-line context exceeds",
        )

    def test_government_identifiers_cannot_hide_in_allowed_content(self) -> None:
        for content in (
            "Social Security number (synthetic).",
            "000-12-3456",
            "Passport SYNTHETIC-P00000000",
            "Passport #SYNTHETIC-P00000000",
            "Driver license SYNTHETIC-D00000000",
            "Drivers license #SYNTHETIC-D00000000",
            "DL: SYNTHETIC-D00000000",
            "EIN 00-0000000",
            "Tax ID #SYNTHETIC-T00000000",
            "Government ID #SYNTHETIC-G00000000",
        ):
            with self.subTest(content=content):
                self.assert_disallowed_content_on_every_surface(content)

    def test_credentials_and_tokens_cannot_hide_in_allowed_content(self) -> None:
        for content in (
            "api_token=SYNTHETIC_NOT_A_TOKEN_1234567890",
            "token=SYNTHETIC_NOT_A_TOKEN_1234567890",
            "secret=SYNTHETIC_NOT_A_SECRET_1234567890",
            "session_token=SYNTHETIC_NOT_A_TOKEN_1234567890",
            "AWS_SECRET_ACCESS_KEY=SYNTHETIC_NOT_A_KEY_1234567890",
            "credential=SYNTHETIC_NOT_A_CREDENTIAL_1234567890",
            "Cookie: sessionid=SYNTHETIC_NOT_A_COOKIE_1234567890",
            "session_id=SYNTHETIC_NOT_A_SESSION_1234567890",
            "api+key=SYNTHETIC_NOT_A_KEY_1234567890",
            "Authorization: Bearer SYNTHETIC_NOT_A_TOKEN_1234567890",
            "Authorization: Basic SYNTHETIC_NOT_A_CREDENTIAL_1234567890",
            "https://synthetic-user:synthetic-password@portfolio.example.com/item",
            "-----BEGIN PRIVATE KEY----- SYNTHETIC_NOT_A_PRIVATE_KEY",
            f"github_pat_{'A' * 20}",
            f"sk-proj-{'A' * 20}",
            f"AKIA{'A' * 16}",
            f"xoxb-{'A' * 16}",
            f"eyJ{'A' * 12}.eyJ{'B' * 12}.eyJ{'C' * 12}",
        ):
            with self.subTest(content_kind=content.split(":", 1)[0]):
                self.assert_disallowed_content_on_every_surface(content)

    def test_sensitive_assignment_labels_cannot_hide_in_allowed_values(self) -> None:
        assignments = (
            "work_authorization=yes",
            "sponsorship=no",
            "visa=F-1",
            "EAD=yes",
            "passport=SYNTHETIC-P00000000",
            "driver_license=SYNTHETIC-D00000000",
            "tax_id=SYNTHETIC-T00000000",
            "EIN=SYNTHETIC-E00000000",
            "work.authorization=yes",
            "api.key=SYNTHETIC_NOT_A_KEY_1234567890",
            "passport.no=SYNTHETIC-P00000000",
            "candidate work.authorization=yes",
            "set api.key=SYNTHETIC_NOT_A_KEY_1234567890",
            "id passport.no=SYNTHETIC-P00000000",
            "https://example.com/?api.key=SYNTHETIC_NOT_A_KEY_1234567890",
        )

        for index, content in enumerate(assignments):
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=content,
                        idempotency_key=f"sensitive-assignment-{index}",
                    ),
                    forbidden_text=content,
                    message_pattern="disallowed sensitive",
                )

    def test_sensitive_content_cannot_be_fragmented_across_value_fields(self) -> None:
        fragmented_values = (
            {"employer": "api_to", "title": "ken=SYNTHETIC_NOT_A_TOKEN_1234567890"},
            {"employer": "ken=SYNTHETIC_NOT_A_TOKEN_1234567890", "title": "api_to"},
            {"employer": "Social Secu", "title": "rity number SYNTHETIC-ID"},
            {"employer": "rity number SYNTHETIC-ID", "title": "Social Secu"},
            {"employer": "Requires spon", "title": "sorship."},
            {"employer": "sorship.", "title": "Requires spon"},
        )

        for index, value in enumerate(fragmented_values):
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        claim_type="employment_title",
                        value=value,
                        idempotency_key=f"fragmented-sensitive-value-{index}",
                    ),
                    message_pattern="fragmented sensitive",
                )

    def test_sensitive_content_cannot_be_fragmented_across_value_and_canonical_text(
        self,
    ) -> None:
        request = single_proposal_request(
            claim_type="employment_title",
            value={
                "employer": "Requires spon",
                "title": "Software Engineer",
            },
            canonical_text="sorship.",
            idempotency_key="cross-surface-fragmented-sensitive-content",
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="fragmented sensitive",
        )

    def test_control_padding_cannot_hide_cross_surface_sensitive_fragments(
        self,
    ) -> None:
        request = single_proposal_request(
            claim_type="employment_title",
            value={
                "employer": "sorship.",
                "title": "Software Engineer",
            },
            canonical_text="Requires spon" + ("\u200b" * 65),
            idempotency_key="control-padded-sensitive-fragments",
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="fragmented sensitive",
        )

    def test_percent_encoded_sensitive_content_is_rejected(self) -> None:
        encoded = "token%253DSYNTHETIC_NOT_A_TOKEN_1234567890"
        self.assert_rejected_before_storage(
            single_proposal_request(
                value=encoded,
                idempotency_key="encoded-sensitive-value",
            ),
            forbidden_text=encoded,
            message_pattern="disallowed sensitive",
        )

    def test_control_characters_cannot_obfuscate_sensitive_content(self) -> None:
        for content in (
            "Authorized\u200bto\u200bwork in the United States.",
            "api_\x00key=SYNTHETIC_NOT_A_KEY_1234567890",
            "Requires\x1bsponsorship.",
        ):
            with self.subTest(content=repr(content)):
                self.assert_disallowed_content_on_every_surface(
                    content,
                    message_pattern=(
                        "disallowed sensitive|one line without control or format"
                    ),
                )

    def test_sensitive_classifier_allows_noneligibility_resume_language(self) -> None:
        for index, content in enumerate(
            (
                "Able to work independently on cross-functional teams.",
                "Contributed to a citizen science publication.",
                "Built a passport renewal service.",
                "Improved drivers license renewal workflow.",
                "Designed passport photo upload UX.",
                "DL: PyTorch",
            )
        ):
            with self.subTest(index=index):
                self.service.preview_import_proposal(
                    single_proposal_request(
                        value=content,
                        idempotency_key=f"safe-sensitive-negative-control-{index}",
                    )
                )

    def test_restricted_taxonomy_allows_legitimate_resume_language(self) -> None:
        cases = (
            (
                "clearance_workflow",
                "Automated a fictional customs security clearance workflow.",
            ),
            (
                "security_backlog",
                "Cleared a fictional application-security backlog.",
            ),
            (
                "clearance_documentation",
                "Maintained clearance documentation for fictional customs shipments.",
            ),
            (
                "clearance_process",
                "Maintained a clearance workflow for fictional customs shipments.",
            ),
            (
                "clearance_status_dashboard",
                "Built a fictional customs clearance status dashboard.",
            ),
            (
                "clearance_eligibility_logistics",
                "Made fictional shipments eligible for clearance upon arrival.",
            ),
            (
                "clearance_customs_documents",
                "Goods were able to obtain clearance if documents matched.",
            ),
            (
                "active_customs_clearance",
                "Tracked active clearance and fictional customs release.",
            ),
            ("customs_clearance_value", "Clearance: approved"),
            (
                "public_trust_outcome",
                "Public trust: improved through transparent fictional reporting.",
            ),
            (
                "veteran_owned_vendor",
                "Integrated a fictional veteran-owned vendor catalog.",
            ),
            (
                "veteran_status_reporting",
                "Built a fictional veteran status reporting dashboard.",
            ),
            (
                "disability_accessibility",
                "Built accessibility tools for fictional users with disabilities.",
            ),
            (
                "disability_focused_research",
                "I have a disability-focused synthetic research portfolio.",
            ),
            (
                "disability_accessibility_research",
                "I have a disability accessibility research portfolio.",
            ),
            (
                "disability_status_reporting",
                "Built a fictional disability status reporting dashboard.",
            ),
            (
                "criminal_justice",
                "Published synthetic criminal justice research.",
            ),
            (
                "legal_research",
                "Conducted legal research for a fictional policy team.",
            ),
            (
                "legal_attestation_workflow",
                "Implemented a fictional legal attestation workflow.",
            ),
            (
                "electronic_signature_workflow",
                "Implemented an electronic signature workflow for a fictional product.",
            ),
            (
                "electronic_signature_verification",
                "Built electronic-signature verification for a fictional product.",
            ),
            (
                "merge_conflicts",
                "Resolved Git merge conflicts in a fictional monorepo.",
            ),
            (
                "demographic_analytics",
                "Analyzed aggregate user demographics for a fictional product.",
            ),
            (
                "data_race",
                "Verified the fictional data race is no longer reproducible.",
            ),
            (
                "black_belt",
                "I am black-belt certified in fictional Lean Six Sigma.",
            ),
            (
                "pronoun_setting",
                "The fictional service reports whether pronouns are optional.",
            ),
            ("cryptographic_signature", "Signature: Ed25519"),
            (
                "professional_certification",
                "Earned the Example Cloud Certified Architect certification.",
            ),
        )

        for index, (category, content) in enumerate(cases):
            with self.subTest(category=category):
                preview = self.service.preview_import_proposal(
                    single_proposal_request(
                        value=content,
                        idempotency_key=f"safe-restricted-control-{index}",
                    )
                )
                self.assertEqual(preview.proposal_count, 1)

    def test_fragmented_assignment_detector_does_not_match_inside_a_label(self) -> None:
        self.service.preview_import_proposal(
            single_proposal_request(
                claim_type="skill_use",
                value="API",
                canonical_text="Turnkey: delivered",
                idempotency_key="safe-api-turnkey-boundary",
            )
        )

    def test_sensitive_label_in_selected_line_blocks_answer_only_span(self) -> None:
        cases = (
            ("Work authorization: Yes.", "Yes", "Yes"),
            (
                "Passport: SYNTHETIC-P00000000",
                "SYNTHETIC-P00000000",
                "SYNTHETIC-P00000000",
            ),
            (
                "API token: SYNTHETIC_RANDOM_VALUE_1234567890",
                "SYNTHETIC_RANDOM_VALUE_1234567890",
                "SYNTHETIC_RANDOM_VALUE_1234567890",
            ),
        )
        for index, (line, selected, value) in enumerate(cases):
            source_text = f"Synthetic header.\n{line}\nSynthetic footer."
            start = source_text.index(selected)
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=value,
                        canonical_text="Confirmed synthetic answer",
                        source_text=source_text,
                        span=TextSourceSpan(
                            start=start,
                            end=start + len(selected),
                            text=selected,
                        ),
                        idempotency_key=f"answer-only-sensitive-line-{index}",
                    ),
                    forbidden_text=selected,
                    message_pattern="disallowed sensitive",
                )

    def test_sensitive_label_on_preceding_line_blocks_answer_only_span(self) -> None:
        cases = (
            ("Work authorization:", "Yes", "\n"),
            ("Passport number", "SYNTHETIC-P00000000", "\r\n"),
            ("API token", "SYNTHETIC_RANDOM_VALUE_1234567890", "\u2028"),
        )
        for index, (label, selected, separator) in enumerate(cases):
            source_text = (
                f"Synthetic header.{separator}{label}{separator}{selected}"
                f"{separator}Synthetic footer."
            )
            start = source_text.index(selected)
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    single_proposal_request(
                        value=selected,
                        canonical_text="Confirmed synthetic answer",
                        source_text=source_text,
                        span=TextSourceSpan(
                            start=start,
                            end=start + len(selected),
                            text=selected,
                        ),
                        idempotency_key=f"preceding-sensitive-label-{index}",
                    ),
                    forbidden_text=selected,
                    message_pattern="disallowed sensitive",
                )

    def test_credentials_cannot_hide_in_persisted_import_metadata(self) -> None:
        safe = single_proposal_request()
        token = "SYNTHETIC_NOT_A_TOKEN_1234567890"
        metadata_cases = (
            (
                replace(
                    safe,
                    proposals=(
                        replace(safe.proposals[0], subject_id=f"token={token}"),
                    ),
                ),
                token,
            ),
            (
                replace(
                    safe,
                    proposals=(
                        replace(
                            safe.proposals[0],
                            scope=Scope(type=ScopeType.COMPANY, id=f"token={token}"),
                        ),
                    ),
                ),
                token,
            ),
        )

        for index, (request, forbidden_text) in enumerate(metadata_cases):
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    request,
                    forbidden_text=forbidden_text,
                    message_pattern="disallowed sensitive",
                )

    def test_sensitive_content_cannot_be_split_across_metadata_fields(self) -> None:
        token = "SYNTHETIC_NOT_A_TOKEN_1234567890"
        safe = single_proposal_request()
        proposal = replace(
            safe.proposals[0],
            subject_id="api_to",
            scope=Scope(type=ScopeType.COMPANY, id=f"ken={token}"),
        )

        self.assert_rejected_before_storage(
            replace(
                safe,
                idempotency_key="fragmented-sensitive-metadata",
                proposals=(proposal,),
            ),
            forbidden_text=token,
            message_pattern="fragmented sensitive",
        )

    def test_sensitive_assignment_fragments_cannot_hide_across_proposals(self) -> None:
        safe = single_proposal_request()
        values = (
            "api_to",
            "Synthetic harmless filler",
            "ken=SYNTHETIC_NOT_A_TOKEN_1234567890",
        )
        proposals = tuple(
            replace(
                safe.proposals[0],
                value=value,
                canonical_text=f"Synthetic fragment claim {index}",
            )
            for index, value in enumerate(values)
        )

        self.assert_rejected_before_storage(
            replace(
                safe,
                idempotency_key="nonadjacent-fragmented-assignment",
                proposals=proposals,
            ),
            forbidden_text="SYNTHETIC_NOT_A_TOKEN_1234567890",
            message_pattern="fragmented sensitive",
        )

    def test_fragmented_assignment_scan_indexes_legal_prefix_complements(
        self,
    ) -> None:
        keys = profile_import_validation._RESTRICTED_ASSIGNMENT_KEYS
        legal_prefixes = tuple(
            sorted(
                {
                    key[:split]
                    for key in keys
                    for split in range(1, len(key))
                }
            )
        )
        components = (*legal_prefixes, "9=9")
        assignment_fragment_hashes = {
            hash(key[split:])
            for key in keys
            for split in range(1, len(key))
        }
        unmatched_fragment = "z"
        while hash(unmatched_fragment) in assignment_fragment_hashes:
            unmatched_fragment += "z"
        comparison_count = 0

        class ComparisonProbe(str):
            __hash__ = str.__hash__

            def __eq__(self, other: object) -> bool:
                nonlocal comparison_count
                comparison_count += 1
                return bool(super().__eq__(other))

            def __ne__(self, other: object) -> bool:
                nonlocal comparison_count
                comparison_count += 1
                return bool(super().__ne__(other))

        probe = ComparisonProbe(unmatched_fragment)
        fragments_per_component = 64
        with patch.object(
            profile_import_validation,
            "_assignment_left_fragments",
            side_effect=lambda _: iter((probe,) * fragments_per_component),
        ):
            found = (
                profile_import_validation._contains_fragmented_restricted_assignment(
                    components
                )
            )

        self.assertFalse(found)
        self.assertEqual(comparison_count, 0)

    def test_whole_document_content_is_rejected_before_storage(self) -> None:
        source_text, _ = load_fixture()
        trimmed_source = source_text.strip()
        safe_span = TextSourceSpan(start=87, end=127, text=source_text[87:127])
        full_span = TextSourceSpan(
            start=0,
            end=len(source_text),
            text=source_text,
        )
        trimmed_span = TextSourceSpan(
            start=0,
            end=len(trimmed_source),
            text=trimmed_source,
        )
        requests = (
            single_proposal_request(
                claim_type="achievement",
                value=" ".join(source_text.split()),
                source_text=source_text,
                span=safe_span,
                idempotency_key="whole-document-value",
            ),
            single_proposal_request(
                canonical_text=trimmed_source,
                source_text=source_text,
                span=safe_span,
                idempotency_key="whole-document-canonical",
            ),
            single_proposal_request(
                canonical_text=" ".join(source_text.split()),
                source_text=source_text,
                span=safe_span,
                idempotency_key="normalized-whole-document-canonical",
            ),
            single_proposal_request(
                source_text=source_text,
                span=full_span,
                idempotency_key="whole-document-evidence",
            ),
            single_proposal_request(
                source_text=source_text,
                span=trimmed_span,
                idempotency_key="trimmed-whole-document-evidence",
            ),
        )

        for request in requests:
            with self.subTest(idempotency_key=request.idempotency_key):
                self.assert_rejected_before_storage(
                    request,
                    message_pattern="whole source|too broad|cover too much",
                )

        short_source = "Synthetic Person\nPython developer"
        short_span = TextSourceSpan(
            start=0,
            end=len(short_source),
            text=short_source,
        )
        self.assert_rejected_before_storage(
            single_proposal_request(
                source_text=short_source,
                span=short_span,
                idempotency_key="short-whole-document",
            ),
            message_pattern="whole source|too broad|cover too much",
        )

    def test_whole_document_cannot_be_split_across_persisted_content_channels(
        self,
    ) -> None:
        value_text = "A" * 120
        canonical_text = "B" * 120
        evidence_text = "C" * 60
        source_text = value_text + canonical_text + evidence_text
        request = single_proposal_request(
            claim_type="achievement",
            value=value_text,
            canonical_text=canonical_text,
            source_text=source_text,
            span=TextSourceSpan(
                start=len(value_text) + len(canonical_text),
                end=len(source_text),
                text=evidence_text,
            ),
            idempotency_key="cross-channel-whole-document",
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="content.*whole source",
        )

    def test_whole_document_cannot_be_split_across_structured_value_leaves(
        self,
    ) -> None:
        source_text = "Example Robotics LLC Software Engineer"
        evidence_text = "Example Robotics LLC"
        request = single_proposal_request(
            claim_type="employment_title",
            value={
                "employer": "Example Robotics LLC",
                "title": "Software Engineer",
            },
            canonical_text="Synthetic title claim",
            source_text=source_text,
            span=TextSourceSpan(
                start=0,
                end=len(evidence_text),
                text=evidence_text,
            ),
            idempotency_key="split-document-value",
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="whole source|too broad",
        )

    def test_whole_document_cannot_be_split_across_reordered_values(self) -> None:
        chunks = ("A" * 200, "B" * 200, "C" * 200)
        source_text = "".join(chunks)
        evidence_text = source_text[:40]
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=chunk,
                canonical_text=f"Synthetic chunk {index}",
                span=TextSourceSpan(
                    start=0,
                    end=len(evidence_text),
                    text=evidence_text,
                ),
            )
            for index, chunk in enumerate(reversed(chunks))
        )
        request = CreateImportProposal(
            idempotency_key="reordered-whole-document-values",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="whole source|too broad",
        )

    def test_short_chunks_cannot_reconstruct_a_source_in_arbitrary_order(self) -> None:
        source_text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        chunks = tuple(source_text[index : index + 2] for index in range(0, 36, 2))
        permutation = chunks[4:11] + chunks[:4] + chunks[11:]
        evidence_text = source_text[:4]
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=chunk,
                canonical_text=f"Synthetic fragment {index}",
                span=TextSourceSpan(start=0, end=4, text=evidence_text),
            )
            for index, chunk in enumerate(permutation)
        )
        request = CreateImportProposal(
            idempotency_key="short-reordered-whole-document",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="too broad",
        )

    def test_single_character_chunks_cannot_reconstruct_a_source(self) -> None:
        source_text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        chunks = tuple(source_text)
        permutation = chunks[7:19] + chunks[:7] + chunks[19:]
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=chunk,
                canonical_text=f"Synthetic character {index}",
                span=TextSourceSpan(start=0, end=2, text=source_text[:2]),
            )
            for index, chunk in enumerate(permutation)
        )
        request = CreateImportProposal(
            idempotency_key="single-character-whole-document",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="too broad",
        )

    def test_punctuation_cannot_hide_reordered_single_character_chunks(self) -> None:
        source_text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        chunks = tuple(source_text)
        permutation = chunks[7:19] + chunks[:7] + chunks[19:]
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=chunk + ("!" * 20),
                canonical_text=f"Synthetic padded character {index}",
                span=TextSourceSpan(start=0, end=2, text=source_text[:2]),
            )
            for index, chunk in enumerate(permutation)
        )
        request = CreateImportProposal(
            idempotency_key="punctuation-padded-source-characters",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="too broad",
        )

    def test_repeated_source_character_padding_cannot_hide_reordered_chunks(
        self,
    ) -> None:
        source_text = "0123456789"
        permutation = source_text[3:8] + source_text[:3] + source_text[8:]
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=character + ("0" * 20),
                canonical_text="Synthetic padded digit claim",
                span=TextSourceSpan(start=0, end=1, text=source_text[:1]),
            )
            for character in permutation
        )
        request = CreateImportProposal(
            idempotency_key="source-character-padded-document",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="raw values.*too broad",
        )

    def test_unrelated_aggregate_value_length_is_not_document_coverage(self) -> None:
        source_text = "A" * 600
        evidence_text = source_text[:40]
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value="Z" * 200,
                canonical_text=f"Synthetic unrelated claim {index}",
                span=TextSourceSpan(
                    start=0,
                    end=len(evidence_text),
                    text=evidence_text,
                ),
            )
            for index in range(3)
        )
        request = CreateImportProposal(
            idempotency_key="unrelated-value-length",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.service.preview_import_proposal(request)

    def test_whole_document_cannot_hide_in_persisted_metadata(self) -> None:
        source_text = "SYNTHETICPRIVATESOURCE" * 12
        evidence_text = source_text[:30]
        span = TextSourceSpan(start=0, end=len(evidence_text), text=evidence_text)
        safe = single_proposal_request(
            canonical_text="Synthetic metadata claim",
            source_text=source_text,
            span=span,
            idempotency_key="whole-document-metadata",
        )
        requests = (
            replace(
                safe,
                proposals=(replace(safe.proposals[0], subject_id=source_text),),
            ),
            replace(
                safe,
                proposals=(
                    replace(
                        safe.proposals[0],
                        subject_id=source_text[: len(source_text) // 2],
                        scope=Scope(
                            type=ScopeType.COMPANY,
                            id=source_text[len(source_text) // 2 :],
                        ),
                    ),
                ),
            ),
        )

        for index, request in enumerate(requests):
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    request,
                    message_pattern="metadata.*whole source|metadata.*too broad",
                )

    def test_percent_encoded_whole_document_content_is_rejected(self) -> None:
        source_text, _ = load_fixture()
        encoded_source = quote(source_text, safe="")
        safe_span = TextSourceSpan(start=87, end=127, text=source_text[87:127])
        for index, request in enumerate(
            (
                single_proposal_request(
                    claim_type="achievement",
                    value=encoded_source,
                    source_text=source_text,
                    span=safe_span,
                    idempotency_key="encoded-whole-document-value",
                ),
                single_proposal_request(
                    canonical_text=encoded_source,
                    source_text=source_text,
                    span=safe_span,
                    idempotency_key="encoded-whole-document-canonical",
                ),
            )
        ):
            with self.subTest(index=index):
                self.assert_rejected_before_storage(
                    request,
                    message_pattern="whole source|too broad",
                )

    def test_raw_percent_syntax_cannot_hide_an_exact_whole_document_value(self) -> None:
        source_text = "%41" * 100
        request = single_proposal_request(
            claim_type="achievement",
            value=source_text,
            canonical_text="Synthetic percent syntax claim",
            source_text=source_text,
            span=TextSourceSpan(start=0, end=1, text="%"),
            idempotency_key="raw-percent-whole-document",
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="whole source|too broad",
        )

    def test_intermediate_percent_decode_layer_cannot_hide_whole_document_value(
        self,
    ) -> None:
        source_text = "A%42" * 100
        request = single_proposal_request(
            claim_type="achievement",
            value="A%2542" * 100,
            canonical_text="Synthetic intermediate encoding claim",
            source_text=source_text,
            span=TextSourceSpan(start=0, end=1, text="A"),
            idempotency_key="intermediate-percent-layer-whole-document",
        )

        self.assert_rejected_before_storage(
            request,
            forbidden_text=source_text,
            message_pattern=(
                "values percent-decode layer 1.*reconstructs the whole source"
            ),
        )

    def test_split_whole_document_evidence_is_rejected_before_storage(self) -> None:
        source_text, _ = load_fixture()
        boundaries = (0, len(source_text) // 3, 2 * len(source_text) // 3, len(source_text))
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=f"Synthetic achievement {index}",
                canonical_text=f"Synthetic achievement {index}",
                span=TextSourceSpan(
                    start=start,
                    end=end,
                    text=source_text[start:end],
                ),
            )
            for index, (start, end) in enumerate(
                zip(boundaries[:-1], boundaries[1:], strict=True)
            )
        )
        request = CreateImportProposal(
            idempotency_key="split-whole-document",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(request)

    def test_whitespace_padding_cannot_hide_split_whole_document_evidence(self) -> None:
        source_body, _ = load_fixture()
        padding = " " * 10_000
        source_text = padding + "\n" + source_body + "\n" + padding
        body_start = len(padding) + 1
        boundaries = (
            body_start,
            body_start + len(source_body) // 3,
            body_start + 2 * len(source_body) // 3,
            body_start + len(source_body),
        )
        intervals = tuple(zip(boundaries[:-1], boundaries[1:], strict=True))
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=f"Synthetic padded achievement {index}",
                canonical_text=f"Synthetic padded achievement {index}",
                span=TextSourceSpan(
                    start=start,
                    end=end,
                    text=source_text[start:end],
                ),
            )
            for index, (start, end) in enumerate(reversed(intervals))
        )
        request = CreateImportProposal(
            idempotency_key="padded-split-whole-document",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="whole source|too broad|cover too much",
        )

    def test_invisible_and_punctuation_padding_cannot_hide_document_coverage(self) -> None:
        source_body, _ = load_fixture()
        padding = "\u200b.!?" * 5_000
        source_text = padding + "\n" + source_body + "\n" + padding
        body_start = len(padding) + 1
        boundaries = (
            body_start,
            body_start + len(source_body) // 3,
            body_start + 2 * len(source_body) // 3,
            body_start + len(source_body),
        )
        proposals = tuple(
            ProposedImportClaim(
                claim_type="achievement",
                value=f"Synthetic padded content {index}",
                canonical_text=f"Synthetic padded content {index}",
                span=TextSourceSpan(start=start, end=end, text=source_text[start:end]),
            )
            for index, (start, end) in enumerate(
                reversed(tuple(zip(boundaries[:-1], boundaries[1:], strict=True)))
            )
        )
        request = CreateImportProposal(
            idempotency_key="invisible-padded-document",
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=proposals,
        )

        self.assert_rejected_before_storage(
            request,
            message_pattern="whole source|too broad|cover too much",
        )

    def test_unselected_sensitive_source_content_is_not_scanned_or_stored(self) -> None:
        private_unselected = "api_token=SYNTHETIC_UNSELECTED_TOKEN_1234567890"
        selected = "Built a synthetic Python fixture."
        source_text = f"{private_unselected}\n{selected}\nSynthetic footer."
        start = source_text.index(selected)
        request = single_proposal_request(
            source_text=source_text,
            span=TextSourceSpan(
                start=start,
                end=start + len(selected),
                text=selected,
            ),
            idempotency_key="unselected-sensitive-source",
        )

        self.service.preview_import_proposal(request)
        result = self.service.create_import_proposal(request, now=NOW)

        self.assertNotIn(private_unselected, str(result))
        self.assertNotIn(private_unselected, str(self.repository.list_claims()))
        self.assertNotIn(private_unselected, str(self.repository.list_evidence()))
        self.assertNotIn(private_unselected, str(self.repository.list_workflow_runs()))

    def test_invalid_later_proposal_fails_before_storage_is_touched(self) -> None:
        safe_request = single_proposal_request()
        unsafe_later = replace(
            safe_request.proposals[0],
            canonical_text="Social Security number 000-00-0000 (synthetic).",
        )
        request = replace(
            safe_request,
            idempotency_key="invalid-later-proposal",
            proposals=(safe_request.proposals[0], unsafe_later),
        )

        self.assert_rejected_before_storage(request)

    def test_rejected_import_does_not_reserve_its_idempotency_key(self) -> None:
        unsafe = single_proposal_request(
            value="password=SYNTHETIC_NOT_A_PASSWORD_123456",
            idempotency_key="reusable-after-rejection",
        )
        self.assert_rejected_before_storage(unsafe)

        corrected = single_proposal_request(
            idempotency_key=unsafe.idempotency_key,
        )
        result = self.service.create_import_proposal(corrected, now=NOW)

        self.assertEqual(len(result.claims), 1)
        self.assertEqual(len(self.repository.list_claims()), 1)
        self.assertEqual(len(self.repository.list_evidence()), 1)
        self.assertEqual(len(self.repository.list_workflow_runs()), 1)

    def test_preview_validates_without_reserving_records_or_idempotency(self) -> None:
        request = import_request()

        preview = self.service.preview_import_proposal(request)

        self.assertEqual(preview.proposal_count, len(request.proposals))
        self.assertEqual(preview.planned_claim_count, len(request.proposals))
        self.assertEqual(preview.planned_evidence_count, len(request.proposals))
        self.assertEqual(
            preview.source_sha256,
            hashlib.sha256(request.source_text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(preview.source_ref, request.source_ref)
        self.assertEqual(preview.source_artifact_id, request.source_artifact_id)
        self.assertEqual(preview.extractor_id, PROFILE_IMPORT_EXTRACTOR_ID)
        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

        result = self.service.create_import_proposal(request, now=NOW)
        self.assertEqual(result.source_sha256, preview.source_sha256)
        self.assertEqual(result.source_ref, preview.source_ref)
        self.assertEqual(result.source_artifact_id, preview.source_artifact_id)
        self.assertEqual(result.extractor_id, preview.extractor_id)
        self.assertEqual(len(result.claims), preview.proposal_count)

    def test_review_items_expose_only_pending_claims_and_exact_support(self) -> None:
        result = self.service.create_import_proposal(import_request(), now=NOW)

        items = self.service.list_review_items()

        self.assertEqual(len(items), len(result.claims))
        self.assertEqual(
            {item.claim.id for item in items},
            {claim.id for claim in result.claims},
        )
        for item in items:
            self.assertEqual(item.claim.status, ClaimStatus.NEEDS_REVIEW)
            self.assertEqual(item.claim.approval_status, ApprovalStatus.PENDING)
            self.assertEqual(len(item.evidence), 1)
            self.assertEqual(item.evidence[0].claim_id, item.claim.id)
            self.assertEqual(item.evidence[0].source_ref, item.claim.source_ref)
        json.dumps(to_jsonable(items), ensure_ascii=False)

    def test_import_review_item_rejects_nonpending_or_incomplete_evidence(self) -> None:
        result = self.service.create_import_proposal(import_request(), now=NOW)
        claim = result.claims[0]
        evidence = result.evidence[0]

        with self.assertRaisesRegex(ValueError, "complete pending"):
            ProfileReviewItem(claim=claim, evidence=())
        with self.assertRaisesRegex(ValueError, "complete pending"):
            ProfileReviewItem(
                claim=claim,
                evidence=(
                    replace(
                        evidence,
                        confirmation_status=EvidenceConfirmationStatus.CONFIRMED,
                    ),
                ),
            )

    def test_public_generic_mutations_reject_imported_resume_records(self) -> None:
        private_source_ref = "file:///Users/SYNTHETIC_PRIVATE_PERSON/resume.txt"

        with self.assertRaisesRegex(ValueError, "profile import workflow") as claim_error:
            self.service.create_claim(
                CreateClaim(
                    claim_type="skill_use",
                    value="Python",
                    canonical_text="Used Python in a fictional project",
                    source_type=SourceType.IMPORTED_RESUME,
                    source_ref=private_source_ref,
                ),
                now=NOW,
            )

        ordinary_claim = self.service.create_claim(
            CreateClaim(
                claim_type="skill_use",
                value="Python",
                canonical_text="Used Python in a fictional user statement",
                source_type=SourceType.USER_STATEMENT,
                source_ref="user-statement://synthetic-profile/1",
            ),
            now=NOW,
        )
        with self.assertRaisesRegex(ValueError, "profile import workflow") as evidence_error:
            self.service.create_evidence(
                CreateEvidence(
                    claim_id=ordinary_claim.id,
                    source_type=SourceType.IMPORTED_RESUME,
                    source_ref=private_source_ref,
                    source_text="Synthetic supporting evidence",
                    extraction_method="caller-claimed@999",
                ),
                now=NOW,
            )

        self.assertNotIn(private_source_ref, str(claim_error.exception))
        self.assertNotIn(private_source_ref, str(evidence_error.exception))
        self.assertEqual(len(self.repository.list_claims()), 1)
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_artifacts(), [])

    def test_review_blocks_legacy_path_and_unregistered_extractor_provenance(
        self,
    ) -> None:
        private_source_ref = "file:///Users/SYNTHETIC_PRIVATE_PERSON/resume.txt"
        current = self.service.create_import_proposal(import_request(), now=NOW)
        legacy_claim = self.repository.add_claim(
            claim_id="synthetic-legacy-import-claim",
            claim_type="skill_use",
            value="Python",
            canonical_text="Used Python in a fictional legacy import",
            source_type=SourceType.IMPORTED_RESUME.value,
            source_ref=private_source_ref,
            created_at=NOW_TEXT,
        )
        self.repository.add_evidence(
            evidence_id="synthetic-legacy-import-evidence",
            claim_id=str(legacy_claim["id"]),
            source_type=SourceType.IMPORTED_RESUME.value,
            source_ref=private_source_ref,
            source_text="Synthetic supporting evidence",
            extraction_method="caller-claimed@999",
            captured_at=NOW_TEXT,
            created_at=NOW_TEXT,
        )

        with self.assertRaisesRegex(
            RepositoryError,
            "review provenance failed integrity checks",
        ) as error:
            self.service.list_review_items()

        self.assertNotIn(private_source_ref, str(error.exception))
        self.assertNotIn("SYNTHETIC_PRIVATE_PERSON", str(error.exception))
        self.assertEqual(len(self.repository.list_claims()), len(current.claims) + 1)
        self.assertEqual(len(self.repository.list_evidence()), len(current.evidence) + 1)

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
        self.assertEqual(result.source_ref, f"sha256:{result.source_sha256}")
        self.assertEqual(
            result.source_artifact_id,
            f"profile-import-source:sha256:{result.source_sha256}",
        )
        self.assertEqual(result.extractor_id, PROFILE_IMPORT_EXTRACTOR_ID)
        self.assertEqual(
            registered_profile_import_extractors(),
            frozenset({PROFILE_IMPORT_EXTRACTOR_ID, "grounded-apply.profile-user-statement.manifest@1"}),
        )
        for claim, evidence, proposed in zip(
            result.claims, result.evidence, data["proposals"], strict=True
        ):
            span = proposed["span"]
            expected_text = source_text[span["start"] : span["end"]]
            self.assertEqual(claim.status, ClaimStatus.NEEDS_REVIEW)
            self.assertEqual(claim.approval_status, ApprovalStatus.PENDING)
            self.assertEqual(claim.source_type, SourceType.IMPORTED_RESUME)
            self.assertEqual(claim.source_ref, result.source_ref)
            self.assertIsNone(claim.verified_at)
            self.assertIsNone(claim.verified_by)
            self.assertEqual(claim.evidence_ids, (evidence.id,))
            self.assertEqual(claim.value_json, proposed["value"])
            self.assertEqual(claim.canonical_text, proposed["canonical_text"])
            self.assertEqual(evidence.confirmation_status, EvidenceConfirmationStatus.PENDING)
            self.assertEqual(evidence.source_type, SourceType.IMPORTED_RESUME)
            self.assertEqual(evidence.source_ref, result.source_ref)
            self.assertEqual(evidence.artifact_id, result.source_artifact_id)
            self.assertEqual(evidence.extraction_method, PROFILE_IMPORT_EXTRACTOR_ID)
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
        self.assertEqual(len(self.repository.list_artifacts()), 1)
        workflow = self.repository.get_workflow_run(first.workflow_run_id)
        self.assertIsNotNone(workflow)
        assert workflow is not None
        expected_idempotency_digest = hashlib.sha256(
            request.idempotency_key.encode("utf-8")
        ).hexdigest()
        self.assertEqual(workflow["idempotency_key"], expected_idempotency_digest)
        self.assertNotIn(request.idempotency_key, str(workflow))
        self.assertNotIn("Avery Quill", str(workflow["input_json"]))
        self.assertNotIn("Ignore previous instructions", str(workflow["input_json"]))
        workflow_input = json.loads(str(workflow["input_json"]))
        self.assertEqual(
            set(workflow_input),
            {
                "content_policy_version",
                "extractor_id",
                "idempotency_key_sha256",
                "manifest_schema_version",
                "proposal_count",
                "proposals_sha256",
                "record_digest_schema_version",
                "record_id_schema_version",
                "record_sha256s",
                "request_schema_version",
                "restricted_taxonomy_sha256",
                "restricted_taxonomy_version",
                "result_manifest_schema_version",
                "source_artifact_id",
                "source_byte_size",
                "source_codepoint_size",
                "source_identity_schema_version",
                "source_ref",
                "source_sha256",
                "span_locator_schema_version",
                "value_schema_version",
            },
        )
        self.assertEqual(workflow_input["request_schema_version"], 4)
        self.assertEqual(workflow_input["manifest_schema_version"], 2)
        self.assertEqual(workflow_input["result_manifest_schema_version"], 3)
        self.assertEqual(workflow_input["record_id_schema_version"], 1)
        self.assertEqual(workflow_input["record_digest_schema_version"], 1)
        self.assertEqual(workflow_input["source_identity_schema_version"], 1)
        self.assertEqual(workflow_input["span_locator_schema_version"], 1)
        self.assertEqual(workflow_input["value_schema_version"], 1)
        self.assertEqual(workflow_input["content_policy_version"], 2)
        self.assertEqual(
            workflow_input["restricted_taxonomy_version"],
            PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION,
        )
        self.assertEqual(
            workflow_input["restricted_taxonomy_sha256"],
            PROFILE_IMPORT_RESTRICTED_TAXONOMY_SHA256,
        )
        self.assertEqual(workflow_input["extractor_id"], PROFILE_IMPORT_EXTRACTOR_ID)
        self.assertEqual(
            workflow_input["idempotency_key_sha256"], expected_idempotency_digest
        )
        self.assertEqual(workflow_input["source_sha256"], request.source_sha256)
        self.assertEqual(workflow_input["source_ref"], request.source_ref)
        self.assertEqual(
            workflow_input["source_codepoint_size"], len(request.source_text)
        )
        self.assertEqual(
            workflow_input["source_artifact_id"], request.source_artifact_id
        )
        result_manifest = json.loads(str(workflow["generated_artifacts_json"]))
        self.assertEqual(result_manifest["schema_version"], 3)
        self.assertEqual(
            workflow_input["record_sha256s"],
            [record["record_sha256"] for record in result_manifest["records"]],
        )
        self.assertEqual(
            workflow["input_hash_sha256"],
            hashlib.sha256(str(workflow["input_json"]).encode("utf-8")).hexdigest(),
        )

    def test_earlier_content_policy_workflow_cannot_replay_under_current_identity(
        self,
    ) -> None:
        request = single_proposal_request(
            idempotency_key="earlier-policy-replay",
        )
        stored_key = hashlib.sha256(
            request.idempotency_key.encode("utf-8")
        ).hexdigest()
        current_input = profile_service_module._import_workflow_input(
            request,
            idempotency_key_sha256=stored_key,
            record_sha256s=tuple(
                profile_service_module._profile_import_record_sha256_for_proposal(
                    request,
                    proposal,
                    exact_text,
                )
                for proposal, exact_text in zip(
                    request.proposals,
                    profile_service_module._validated_source_spans(request),
                    strict=True,
                )
            ),
        )
        earlier_input = {
            key: value
            for key, value in current_input.items()
            if key
            not in {
                "restricted_taxonomy_sha256",
                "restricted_taxonomy_version",
            }
        }
        earlier_input["request_schema_version"] = 2
        earlier_input["content_policy_version"] = 1
        self.assertEqual(
            set(current_input) - set(earlier_input),
            {"restricted_taxonomy_sha256", "restricted_taxonomy_version"},
        )
        earlier = self.repository.add_workflow_run(
            workflow_type="profile_import_proposal",
            status="succeeded",
            idempotency_key=stored_key,
            input_data=earlier_input,
            current_step="awaiting_review",
            completed_steps=("persist_reviewable_claims",),
            generated_artifacts=(),
            created_at=NOW_TEXT,
        )

        with self.assertRaisesRegex(RepositoryError, "different input"):
            self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(self.repository.list_workflow_runs(), [earlier])
        self.assertEqual(self.repository.list_artifacts(), [])
        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])

    def test_idempotency_digest_is_bound_into_the_workflow_request_identity(self) -> None:
        first_request = import_request()
        second_request = replace(
            first_request,
            idempotency_key="fixture-avery-quill-import-second-key",
        )
        first = self.service.create_import_proposal(first_request, now=NOW)
        second = self.service.create_import_proposal(second_request, now=NOW)
        first_workflow = self.repository.get_workflow_run(first.workflow_run_id)
        second_workflow = self.repository.get_workflow_run(second.workflow_run_id)
        assert first_workflow is not None and second_workflow is not None
        first_input = json.loads(str(first_workflow["input_json"]))
        second_input = json.loads(str(second_workflow["input_json"]))

        self.assertEqual(
            first_input["idempotency_key_sha256"], first_workflow["idempotency_key"]
        )
        self.assertEqual(
            second_input["idempotency_key_sha256"], second_workflow["idempotency_key"]
        )
        self.assertNotEqual(first_input, second_input)
        self.assertNotEqual(
            first_workflow["input_hash_sha256"], second_workflow["input_hash_sha256"]
        )

        first_input["idempotency_key_sha256"] = second_input[
            "idempotency_key_sha256"
        ]
        tampered_input = json.dumps(
            first_input,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                "UPDATE workflow_runs SET input_json = ?, input_hash_sha256 = ? "
                "WHERE id = ?",
                (
                    tampered_input,
                    hashlib.sha256(tampered_input.encode("utf-8")).hexdigest(),
                    first.workflow_run_id,
                ),
            )
            connection.commit()

        with self.assertRaisesRegex(RepositoryError, "idempotency key") as error:
            self.service.create_import_proposal(first_request, now=NOW)

        self.assertNotIn(first_request.idempotency_key, str(error.exception))
        self.assertEqual(len(self.repository.list_workflow_runs()), 2)

    def test_record_ids_are_deterministic_and_bound_to_their_workflow(self) -> None:
        first_request = import_request()
        second_request = replace(
            first_request,
            idempotency_key="fixture-avery-quill-import-distinct-workflow",
        )
        first = self.service.create_import_proposal(first_request, now=NOW)
        second = self.service.create_import_proposal(second_request, now=NOW)

        for result in (first, second):
            for index, (claim, evidence) in enumerate(
                zip(result.claims, result.evidence, strict=True)
            ):
                prefix = (
                    "urn:grounded-apply:profile-import-record:"
                    f"v1:{result.workflow_run_id}:{index}"
                )
                self.assertEqual(
                    claim.id,
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}:claim")),
                )
                self.assertEqual(
                    evidence.id,
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}:evidence")),
                )

        self.assertTrue(
            {claim.id for claim in first.claims}.isdisjoint(
                claim.id for claim in second.claims
            )
        )
        self.assertTrue(
            {evidence.id for evidence in first.evidence}.isdisjoint(
                evidence.id for evidence in second.evidence
            )
        )

        second_workflow = self.repository.get_workflow_run(second.workflow_run_id)
        assert second_workflow is not None
        substituted_manifest = json.loads(
            str(second_workflow["generated_artifacts_json"])
        )
        self.repository.update_workflow_run(
            first.workflow_run_id,
            generated_artifacts=substituted_manifest,
        )

        with self.assertRaisesRegex(
            RepositoryError,
            "workflow provenance failed integrity checks",
        ) as error:
            self.service.create_import_proposal(first_request, now=NOW)

        self.assertNotIn(first_request.source_text, str(error.exception))
        self.assertEqual(len(self.repository.list_claims()), 10)
        self.assertEqual(len(self.repository.list_evidence()), 10)
        self.assertEqual(len(self.repository.list_workflow_runs()), 2)

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

        with self.assertRaisesRegex(
            RepositoryError,
            "workflow provenance failed integrity checks",
        ):
            self.service.create_import_proposal(request, now=NOW)

    def test_retry_rejects_same_length_manifest_pairs_in_the_wrong_order(self) -> None:
        request = import_request()
        first = self.service.create_import_proposal(request, now=NOW)
        workflow = self.repository.get_workflow_run(first.workflow_run_id)
        assert workflow is not None
        stored_manifest = json.loads(str(workflow["generated_artifacts_json"]))
        reversed_records = tuple(reversed(stored_manifest["records"]))
        self.repository.update_workflow_run(
            first.workflow_run_id,
            generated_artifacts={
                "schema_version": 3,
                "source_artifact_id": request.source_artifact_id,
                "records": reversed_records,
            },
        )

        with self.assertRaisesRegex(
            RepositoryError,
            "workflow provenance failed integrity checks",
        ):
            self.service.create_import_proposal(request, now=NOW)

    def test_retry_revalidates_every_persisted_provenance_copy(self) -> None:
        corruptions = (
            ("claims", "source_ref", f"sha256:{'0' * 64}"),
            ("evidence", "source_ref", f"sha256:{'0' * 64}"),
            ("evidence", "extraction_method", "unregistered.profile-import@1"),
            ("evidence", "locator_json", "{}"),
            ("evidence", "checksum_sha256", "0" * 64),
        )

        for table, column, value in corruptions:
            with self.subTest(table=table, column=column):
                with tempfile.TemporaryDirectory() as directory:
                    database = Path(directory) / "profile.db"
                    with SQLiteRepository(database) as repository:
                        service = ProfileService(repository)
                        request = import_request()
                        first = service.create_import_proposal(request, now=NOW)
                        with closing(sqlite3.connect(database)) as connection:
                            connection.execute(
                                f"UPDATE {table} SET {column} = ?",
                                (value,),
                            )
                            connection.commit()

                        with self.assertRaisesRegex(
                            RepositoryError,
                            "integrity checks",
                        ):
                            service.create_import_proposal(request, now=NOW)

                        self.assertEqual(len(repository.list_artifacts()), 1)
                        self.assertEqual(
                            len(repository.list_claims()), len(first.claims)
                        )
                        self.assertEqual(
                            len(repository.list_evidence()), len(first.evidence)
                        )
                        self.assertEqual(len(repository.list_workflow_runs()), 1)

    def test_retry_rejects_tampered_support_link_strength_and_note(self) -> None:
        private_marker = "SYNTHETIC_PRIVATE_LINK_NOTE"
        corruptions: tuple[tuple[str, object], ...] = (
            ("strength", 0.0),
            ("note", private_marker),
        )

        for column, value in corruptions:
            with self.subTest(column=column), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "profile.db"
                with SQLiteRepository(database) as repository:
                    service = ProfileService(repository)
                    request = import_request()
                    first = service.create_import_proposal(request, now=NOW)
                    with closing(sqlite3.connect(database)) as connection:
                        connection.execute(
                            f"UPDATE claim_evidence SET {column} = ? "
                            "WHERE claim_id = ? AND evidence_id = ?",
                            (value, first.claims[0].id, first.evidence[0].id),
                        )
                        connection.commit()

                    with self.assertRaisesRegex(
                        RepositoryError,
                        "review provenance failed integrity checks",
                    ) as error:
                        service.create_import_proposal(request, now=NOW)

                    self.assertNotIn(private_marker, str(error.exception))
                    self.assertEqual(len(repository.list_claims()), len(first.claims))
                    self.assertEqual(len(repository.list_evidence()), len(first.evidence))
                    self.assertEqual(len(repository.list_workflow_runs()), 1)

    def test_retry_rejects_unrequested_claim_dates_and_supersession(self) -> None:
        for column in ("effective_from", "effective_to", "supersedes_id"):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "profile.db"
                with SQLiteRepository(database) as repository:
                    service = ProfileService(repository)
                    request = import_request()
                    first = service.create_import_proposal(request, now=NOW)
                    value = (
                        first.claims[1].id
                        if column == "supersedes_id"
                        else "2025-01-01T00:00:00Z"
                    )
                    with closing(sqlite3.connect(database)) as connection:
                        connection.execute(
                            f"UPDATE claims SET {column} = ? WHERE id = ?",
                            (value, first.claims[0].id),
                        )
                        connection.commit()

                    with self.assertRaisesRegex(
                        RepositoryError,
                        "integrity checks",
                    ):
                        service.create_import_proposal(request, now=NOW)

                    self.assertEqual(len(repository.list_claims()), len(first.claims))
                    self.assertEqual(len(repository.list_evidence()), len(first.evidence))
                    self.assertEqual(len(repository.list_workflow_runs()), 1)

    def test_retry_rejects_evidence_metadata_and_confirmation_mutations(self) -> None:
        private_marker = "SYNTHETIC_PRIVATE_EVIDENCE_METADATA"
        corruptions: tuple[tuple[str, tuple[object, ...]], ...] = (
            (
                "UPDATE evidence SET metadata_json = ? WHERE id = ?",
                (json.dumps({"private": private_marker}),),
            ),
            (
                "UPDATE evidence SET confirmed_at = ? WHERE id = ?",
                (NOW_TEXT,),
            ),
            (
                "UPDATE evidence SET confirmed_by = ? WHERE id = ?",
                (private_marker,),
            ),
            (
                "UPDATE evidence SET confirmation_status = 'confirmed', "
                "confirmed_at = ? WHERE id = ?",
                (NOW_TEXT,),
            ),
        )

        for index, (statement, parameters) in enumerate(corruptions):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "profile.db"
                with SQLiteRepository(database) as repository:
                    service = ProfileService(repository)
                    request = import_request()
                    first = service.create_import_proposal(request, now=NOW)
                    with closing(sqlite3.connect(database)) as connection:
                        connection.execute(
                            statement,
                            (*parameters, first.evidence[0].id),
                        )
                        connection.commit()

                    with self.assertRaisesRegex(
                        RepositoryError,
                        "integrity checks",
                    ) as error:
                        service.create_import_proposal(request, now=NOW)

                    self.assertNotIn(private_marker, str(error.exception))
                    self.assertEqual(len(repository.list_claims()), len(first.claims))
                    self.assertEqual(len(repository.list_evidence()), len(first.evidence))
                    self.assertEqual(len(repository.list_workflow_runs()), 1)

    def test_retry_rejects_corrupted_workflow_checkpoint_fields(self) -> None:
        private_marker = "SYNTHETIC_PRIVATE_CHECKPOINT"
        corruptions: tuple[tuple[str, object], ...] = (
            ("status", "running"),
            ("current_step", "persist_reviewable_claims"),
            ("completed_steps_json", "[]"),
            ("completed_steps_json", "{"),
            ("outstanding_need_info_json", json.dumps([private_marker])),
            ("retry_policy_json", json.dumps({"tampered": True})),
            ("model_name", "synthetic-model"),
            ("prompt_version", "synthetic-prompt@1"),
            ("failure_code", "synthetic_failure"),
            ("failure_reason", private_marker),
            ("started_at", "2026-08-10T12:00:00Z"),
            ("finished_at", "2026-08-12T12:00:00Z"),
        )

        for column, value in corruptions:
            with (
                self.subTest(column=column, value=value),
                tempfile.TemporaryDirectory() as directory,
            ):
                database = Path(directory) / "profile.db"
                with SQLiteRepository(database) as repository:
                    service = ProfileService(repository)
                    request = import_request()
                    first = service.create_import_proposal(request, now=NOW)
                    with closing(sqlite3.connect(database)) as connection:
                        connection.execute(
                            f"UPDATE workflow_runs SET {column} = ? WHERE id = ?",
                            (value, first.workflow_run_id),
                        )
                        connection.commit()

                    with self.assertRaisesRegex(
                        RepositoryError,
                        "workflow provenance failed integrity checks",
                    ) as error:
                        service.create_import_proposal(request, now=NOW)

                    self.assertNotIn(private_marker, str(error.exception))
                    self.assertEqual(len(repository.list_claims()), len(first.claims))
                    self.assertEqual(len(repository.list_evidence()), len(first.evidence))
                    self.assertEqual(len(repository.list_workflow_runs()), 1)

    def test_retry_rejects_an_extra_evidence_link(self) -> None:
        request = import_request()
        first = self.service.create_import_proposal(request, now=NOW)
        claim = first.claims[0]
        evidence = first.evidence[0]
        self.repository.add_evidence(
            evidence_id="synthetic-extra-import-evidence",
            claim_id=claim.id,
            source_type=SourceType.IMPORTED_RESUME.value,
            source_ref=request.source_ref,
            source_text=evidence.source_text or "Synthetic evidence",
            locator=evidence.locator,
            extraction_method=PROFILE_IMPORT_EXTRACTOR_ID,
            artifact_id=request.source_artifact_id,
            checksum_sha256=evidence.checksum,
            captured_at=NOW_TEXT,
            created_at=NOW_TEXT,
        )

        with self.assertRaisesRegex(
            RepositoryError,
            "review provenance failed integrity checks",
        ):
            self.service.list_review_items()
        with self.assertRaisesRegex(
            RepositoryError,
            "review provenance failed integrity checks",
        ):
            self.service.create_import_proposal(request, now=NOW)

    def test_reusing_an_idempotency_key_with_different_input_fails_closed(self) -> None:
        request = import_request()
        first = self.service.create_import_proposal(request, now=NOW)
        changed_source = request.source_text + "\nChanged after the first request.\n"
        changed = CreateImportProposal(
            idempotency_key=request.idempotency_key,
            source_text=changed_source,
            expected_source_sha256=hashlib.sha256(
                changed_source.encode("utf-8")
            ).hexdigest(),
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
            self.assertEqual(len(repository.list_artifacts()), 1)
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
                            source_text=source_text,
                            expected_source_sha256=data["source_sha256"],
                            proposals=(candidate,),
                        ),
                        now=NOW,
                    )
                self.assertEqual(self.repository.list_claims(), [])
                self.assertEqual(self.repository.list_evidence(), [])

    def test_batch_rolls_back_when_a_later_evidence_write_fails(self) -> None:
        request = import_request()
        original_create_evidence = self.service._create_evidence
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
            "_create_evidence",
            side_effect=fail_on_second_evidence,
        ):
            with self.assertRaisesRegex(RuntimeError, "second evidence failure"):
                self.service.create_import_proposal(request, now=NOW)

        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_artifacts(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_source_artifact_is_application_owned_and_reused(self) -> None:
        request = import_request()

        first = self.service.create_import_proposal(request, now=NOW)
        second = self.service.create_import_proposal(
            replace(request, idempotency_key="same-source-second-workflow"),
            now=NOW,
        )

        self.assertNotEqual(first.workflow_run_id, second.workflow_run_id)
        artifacts = self.repository.list_artifacts()
        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertEqual(
            set(artifact),
            {
                "id",
                "artifact_type",
                "uri",
                "local_path",
                "original_name",
                "media_type",
                "byte_size",
                "content_sha256",
                "sensitivity",
                "metadata_json",
                "captured_at",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(artifact["id"], request.source_artifact_id)
        self.assertEqual(artifact["artifact_type"], "profile_import_source_digest")
        self.assertEqual(artifact["uri"], request.source_ref)
        self.assertIsNone(artifact["local_path"])
        self.assertIsNone(artifact["original_name"])
        self.assertEqual(artifact["media_type"], "text/plain; charset=utf-8")
        self.assertEqual(artifact["content_sha256"], request.source_sha256)
        self.assertEqual(artifact["sensitivity"], Sensitivity.PERSONAL.value)
        self.assertEqual(
            artifact["byte_size"], len(request.source_text.encode("utf-8"))
        )
        self.assertEqual(
            json.loads(str(artifact["metadata_json"])),
            {
                "retention": "digest_only",
                "source_codepoint_size": len(request.source_text),
                "source_identity_schema_version": 1,
            },
        )
        self.assertEqual(artifact["captured_at"], NOW_TEXT)
        self.assertEqual(artifact["created_at"], NOW_TEXT)
        self.assertEqual(artifact["updated_at"], NOW_TEXT)
        self.assertNotIn(request.source_text, str(artifact))
        self.assertEqual(
            {evidence.artifact_id for evidence in first.evidence + second.evidence},
            {request.source_artifact_id},
        )

    def test_preexisting_corrupt_source_artifact_collision_rolls_back_without_repair(
        self,
    ) -> None:
        request = import_request()
        private_marker = "/Users/SYNTHETIC_PRIVATE_PERSON/resume.txt"
        before = self.repository.add_artifact(
            artifact_id=request.source_artifact_id,
            artifact_type="profile_import_source_digest",
            uri=request.source_ref,
            local_path=None,
            original_name=private_marker,
            media_type="text/plain; charset=utf-8",
            byte_size=len(request.source_text.encode("utf-8")),
            content_sha256=request.source_sha256,
            sensitivity=Sensitivity.PERSONAL.value,
            metadata={
                "retention": "digest_only",
                "source_codepoint_size": len(request.source_text),
                "source_identity_schema_version": 1,
            },
            captured_at=NOW_TEXT,
            created_at=NOW_TEXT,
        )

        with self.assertRaisesRegex(
            RepositoryError,
            "source identity.*integrity",
        ) as error:
            self.service.create_import_proposal(request, now=NOW)

        self.assertNotIn(private_marker, str(error.exception))
        self.assertEqual(self.repository.get_artifact(request.source_artifact_id), before)
        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_review_rejects_corrupt_source_provenance_without_disclosing_it(self) -> None:
        private_marker = "/Users/SYNTHETIC_PRIVATE_PERSON/resume.txt"
        corruption_names = (
            "artifact",
            "locator_digest",
            "checksum",
            "out_of_source_codepoint_bound",
        )

        for corruption in corruption_names:
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "profile.db"
                with SQLiteRepository(database) as repository:
                    service = ProfileService(repository)
                    request = import_request()
                    first = service.create_import_proposal(request, now=NOW)
                    evidence = first.evidence[0]
                    assert isinstance(evidence.locator, dict)
                    with closing(sqlite3.connect(database)) as connection:
                        if corruption == "artifact":
                            connection.execute(
                                "UPDATE artifacts SET original_name = ? WHERE id = ?",
                                (private_marker, request.source_artifact_id),
                            )
                        elif corruption == "locator_digest":
                            locator = {**evidence.locator, "source_sha256": "0" * 64}
                            connection.execute(
                                "UPDATE evidence SET locator_json = ? WHERE id = ?",
                                (json.dumps(locator), evidence.id),
                            )
                        elif corruption == "checksum":
                            connection.execute(
                                "UPDATE evidence SET checksum_sha256 = ? WHERE id = ?",
                                ("0" * 64, evidence.id),
                            )
                        else:
                            self.assertGreater(
                                len(request.source_text.encode("utf-8")),
                                len(request.source_text),
                            )
                            selected_text = "X"
                            start = len(request.source_text)
                            locator = {
                                **evidence.locator,
                                "start": start,
                                "end": start + len(selected_text),
                            }
                            connection.execute(
                                "UPDATE evidence SET locator_json = ?, source_text = ?, "
                                "checksum_sha256 = ? WHERE id = ?",
                                (
                                    json.dumps(locator),
                                    selected_text,
                                    hashlib.sha256(
                                        selected_text.encode("utf-8")
                                    ).hexdigest(),
                                    evidence.id,
                                ),
                            )
                        connection.commit()

                    with self.assertRaises((RepositoryError, ValueError)) as error:
                        service.list_review_items()

                    self.assertIn("integrity", str(error.exception))
                    self.assertNotIn(private_marker, str(error.exception))
                    self.assertNotIn(request.source_text, str(error.exception))
                    self.assertEqual(len(repository.list_claims()), len(first.claims))
                    self.assertEqual(len(repository.list_evidence()), len(first.evidence))
                    self.assertEqual(len(repository.list_workflow_runs()), 1)

    def test_corrupted_source_artifact_blocks_retry_without_repair(self) -> None:
        request = import_request()
        self.service.create_import_proposal(request, now=NOW)
        forged_uri = "file:///Users/SYNTHETIC_PRIVATE_PERSON/resume.txt"
        with closing(sqlite3.connect(self.repository.database)) as connection:
            connection.execute(
                "UPDATE artifacts SET uri = ? WHERE id = ?",
                (forged_uri, request.source_artifact_id),
            )
            connection.commit()

        with self.assertRaisesRegex(RepositoryError, "source identity.*integrity") as error:
            self.service.create_import_proposal(request, now=NOW)

        self.assertNotIn(forged_uri, str(error.exception))
        artifact = self.repository.get_artifact(request.source_artifact_id)
        assert artifact is not None
        self.assertEqual(artifact["uri"], forged_uri)

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
                source_text=request.source_text,
                expected_source_sha256=request.source_sha256,
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
                "source_text": valid.source_text,
                "expected_source_sha256": valid.source_sha256,
                "proposals": valid.proposals,
            },
            {
                "source_text": "",
                "expected_source_sha256": valid.source_sha256,
                "proposals": valid.proposals,
            },
            {
                "source_text": valid.source_text,
                "expected_source_sha256": "",
                "proposals": valid.proposals,
            },
            {
                "source_text": valid.source_text,
                "expected_source_sha256": valid.source_sha256,
                "proposals": (),
            },
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
                            **request_values,
                        ),
                        now=NOW,
                    )
                self.assertEqual(self.repository.list_claims(), [])
                self.assertEqual(self.repository.list_evidence(), [])

    def test_import_metadata_requires_bounded_opaque_identifiers(self) -> None:
        valid = import_request()
        invalid_values = (
            {"idempotency_key": "candidate private sentence with spaces"},
            {"idempotency_key": "x" * 257},
            {"expected_source_sha256": "A" * 64},
            {"expected_source_sha256": "0" * 63},
            {"expected_source_sha256": "g" * 64},
        )

        for changed in invalid_values:
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    self.service.preview_import_proposal(replace(valid, **changed))
                self.assertEqual(self.repository.list_claims(), [])
                self.assertEqual(self.repository.list_evidence(), [])
                self.assertEqual(self.repository.list_workflow_runs(), [])

    def test_import_rejects_unbounded_batches_and_invalid_unicode(self) -> None:
        valid = import_request()

        with self.assertRaisesRegex(ValueError, "more than 1000"):
            replace(valid, proposals=(valid.proposals[0],) * 1001)
        with self.assertRaisesRegex(ValueError, "valid Unicode"):
            replace(valid.proposals[0], value={"private-field": "\ud800"})
        with self.assertRaisesRegex(ValueError, "valid Unicode"):
            replace(valid.proposals[0], canonical_text="\ud800")

        self.assertEqual(self.repository.list_claims(), [])
        self.assertEqual(self.repository.list_evidence(), [])
        self.assertEqual(self.repository.list_workflow_runs(), [])


if __name__ == "__main__":
    unittest.main()
