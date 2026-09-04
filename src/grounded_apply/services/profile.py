"""Validated candidate-claim workflows over the mapping repository API."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from grounded_apply.domain import (
    ApprovalStatus,
    Claim,
    ClaimStatus,
    ClaimUsePolicy,
    Derivation,
    Evidence,
    EvidenceConfirmationStatus,
    JsonValue,
    ResolutionOutcome,
    Scope,
    ScopeType,
    Sensitivity,
    SourceType,
    resolve_claims,
    to_jsonable,
)
from grounded_apply.repositories import (
    Record,
    RecordNotFoundError,
    RepositoryError,
    SQLiteRepository,
)
from grounded_apply.services.profile_import_validation import (
    PROFILE_IMPORT_CONTENT_POLICY_VERSION,
    PROFILE_IMPORT_MAX_SOURCE_BYTES,
    PROFILE_IMPORT_MAX_TOTAL_EVIDENCE_CODEPOINTS,
    PROFILE_IMPORT_MAX_TOTAL_METADATA_CODEPOINTS,
    PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
    registered_profile_import_claim_types,
    validate_profile_import_batch,
    validate_profile_import_metadata,
    validate_profile_import_proposal,
)


_PROFILE_IMPORT_WORKFLOW = "profile_import_proposal"
_MAX_IMPORT_PROPOSALS = 1000
_EXTRACTION_METHOD_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*")
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_SOURCE_REF_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\x00-\x1f\x7f]+"
)
_LINE_BREAK_PATTERN = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _required_timestamp(record: Record, field_name: str) -> datetime:
    value = _parse_timestamp(record.get(field_name), field_name=field_name)
    if value is None:
        raise ValueError(f"Stored {field_name} must not be null")
    return value


def _json_value(value: object, *, field_name: str) -> JsonValue:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be stored as JSON text")
    decoded: Any = json.loads(value)
    return decoded


def _required_text(record: Record, field_name: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Stored {field_name} must be non-blank text")
    return value


def _optional_text(record: Record, field_name: str) -> str | None:
    value = record.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"Stored {field_name} must be text or null")
    return value


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{field_name} must contain valid Unicode text") from None


def _validate_json_value(value: object, path: str = "value") -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError(f"{path} must contain valid Unicode text") from None
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            try:
                key.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError(f"{path} object keys must contain valid Unicode") from None
            _validate_json_value(item, f"{path}[object]")
        return
    raise TypeError(f"{path} contains a non-JSON value: {type(value).__name__}")


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateClaim:
    claim_type: str
    value: JsonValue
    canonical_text: str
    source_type: SourceType
    source_ref: str
    status: ClaimStatus = ClaimStatus.NEEDS_REVIEW
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    subject_type: str = "person"
    subject_id: str | None = None
    confidence: float = 1.0
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    scope: Scope = field(default_factory=Scope)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    verified_at: datetime | None = None
    verified_by: str | None = None
    derivation: Derivation | None = None
    supersedes_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    claim_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateEvidence:
    claim_id: str
    source_type: SourceType
    source_ref: str
    source_text: str
    locator: JsonValue = None
    extraction_method: str = "manual"
    confirmation_status: EvidenceConfirmationStatus = EvidenceConfirmationStatus.PENDING
    artifact_id: str | None = None
    checksum: str | None = None
    confirmed_by: str | None = None
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TextSourceSpan:
    """An exact 0-based, half-open range of Unicode code points."""

    start: int
    end: int
    text: str

    def __post_init__(self) -> None:
        if isinstance(self.start, bool) or not isinstance(self.start, int):
            raise TypeError("span start must be an integer")
        if isinstance(self.end, bool) or not isinstance(self.end, int):
            raise TypeError("span end must be an integer")
        if self.start < 0:
            raise ValueError("span start must not be negative")
        if self.end <= self.start:
            raise ValueError("span end must be greater than span start")
        _require_text(self.text, "span text")
        if self.end - self.start != len(self.text):
            raise ValueError("span offsets must describe the supplied span text")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedImportClaim:
    """One untrusted extraction candidate awaiting explicit user review."""

    claim_type: str
    value: JsonValue
    canonical_text: str
    span: TextSourceSpan
    confidence: float = 1.0
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    scope: Scope = field(default_factory=Scope)
    subject_type: str = "person"
    subject_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.claim_type, "claim_type")
        _require_text(self.canonical_text, "canonical_text")
        if not isinstance(self.span, TextSourceSpan):
            raise TypeError("span must be a TextSourceSpan")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("confidence must be a finite value from 0.0 through 1.0")
        if not isinstance(self.sensitivity, Sensitivity):
            raise TypeError("sensitivity must be a Sensitivity")
        if not isinstance(self.scope, Scope):
            raise TypeError("scope must be a Scope")
        if not isinstance(self.scope.type, ScopeType):
            raise TypeError("scope type must be a ScopeType")
        if self.scope.id is not None:
            _require_text(self.scope.id, "scope id")
        _require_text(self.subject_type, "subject_type")
        if self.subject_id is not None:
            _require_text(self.subject_id, "subject_id")
        _validate_json_value(self.value)


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateImportProposal:
    """A typed batch of extracted facts and the source text that anchors them."""

    idempotency_key: str
    source_ref: str
    source_text: str
    proposals: tuple[ProposedImportClaim, ...]
    extraction_method: str
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.idempotency_key, "idempotency_key")
        _require_text(self.source_ref, "source_ref")
        _require_text(self.source_text, "source_text")
        _require_text(self.extraction_method, "extraction_method")
        if _IDEMPOTENCY_KEY_PATTERN.fullmatch(self.idempotency_key) is None:
            raise ValueError("idempotency_key must be an opaque identifier")
        if (
            len(self.source_ref) > 512
            or _SOURCE_REF_PATTERN.fullmatch(self.source_ref) is None
        ):
            raise ValueError("source_ref must be a bounded absolute source URI")
        if len(self.source_text.encode("utf-8")) > PROFILE_IMPORT_MAX_SOURCE_BYTES:
            raise ValueError("source_text must not exceed 16 MiB as UTF-8")
        if len(self.extraction_method) > 128:
            raise ValueError("extraction_method must not exceed 128 characters")
        if _EXTRACTION_METHOD_PATTERN.fullmatch(self.extraction_method) is None:
            raise ValueError("extraction_method must be a versioned identifier such as name@1")
        if not isinstance(self.proposals, tuple):
            raise TypeError("proposals must be a tuple")
        if not self.proposals:
            raise ValueError("proposals must not be empty")
        if len(self.proposals) > _MAX_IMPORT_PROPOSALS:
            raise ValueError("proposals must not contain more than 1000 items")
        if any(not isinstance(item, ProposedImportClaim) for item in self.proposals):
            raise TypeError("proposals must contain only ProposedImportClaim values")
        if self.artifact_id is not None:
            _require_text(self.artifact_id, "artifact_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportProposalResult:
    """Persisted, review-only claims and their exact pending evidence."""

    workflow_run_id: str
    source_sha256: str
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        _require_text(self.workflow_run_id, "workflow_run_id")
        if (
            len(self.source_sha256) != 64
            or self.source_sha256 != self.source_sha256.lower()
            or any(character not in "0123456789abcdef" for character in self.source_sha256)
        ):
            raise ValueError("source_sha256 must contain a SHA-256 digest")
        if not self.claims or len(self.claims) != len(self.evidence):
            raise ValueError("import result requires one evidence record per claim")
        if any(
            claim.id != evidence.claim_id
            or evidence.id not in claim.evidence_ids
            for claim, evidence in zip(self.claims, self.evidence, strict=True)
        ):
            raise ValueError("import result evidence must align with its claims")
        if any(
            claim.status is not ClaimStatus.NEEDS_REVIEW
            or claim.approval_status is not ApprovalStatus.PENDING
            or claim.verified_at is not None
            or claim.verified_by is not None
            or claim.source_type is not SourceType.IMPORTED_RESUME
            for claim in self.claims
        ):
            raise ValueError("import result claims must remain pending user review")
        if any(
            item.confirmation_status is not EvidenceConfirmationStatus.PENDING
            or item.source_type is not SourceType.IMPORTED_RESUME
            for item in self.evidence
        ):
            raise ValueError("import result evidence must remain pending user review")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportProposalPreview:
    """Persistence-independent request validation containing no candidate text.

    Counts describe a new import batch. This preview deliberately does not inspect
    artifact foreign keys, schema state, or prior use of the idempotency key.
    """

    source_sha256: str
    proposal_count: int
    planned_claim_count: int
    planned_evidence_count: int
    review_required: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if (
            len(self.source_sha256) != 64
            or self.source_sha256 != self.source_sha256.lower()
            or any(character not in "0123456789abcdef" for character in self.source_sha256)
        ):
            raise ValueError("source_sha256 must contain a SHA-256 digest")
        if isinstance(self.proposal_count, bool) or not isinstance(
            self.proposal_count, int
        ):
            raise TypeError("proposal_count must be an integer")
        if isinstance(self.planned_claim_count, bool) or not isinstance(
            self.planned_claim_count, int
        ):
            raise TypeError("planned_claim_count must be an integer")
        if isinstance(self.planned_evidence_count, bool) or not isinstance(
            self.planned_evidence_count, int
        ):
            raise TypeError("planned_evidence_count must be an integer")
        if self.proposal_count <= 0:
            raise ValueError("proposal_count must be positive")
        if (
            self.planned_claim_count != self.proposal_count
            or self.planned_evidence_count != self.proposal_count
        ):
            raise ValueError("an import preview requires one claim and evidence per proposal")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileReviewItem:
    """One pending claim and its supporting evidence for read-only review."""

    claim: Claim
    evidence: tuple[Evidence, ...]
    content_trust: str = field(default="untrusted", init=False)
    usable: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.claim, Claim):
            raise TypeError("review claim must be a Claim")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise TypeError("review evidence must be a tuple of Evidence records")
        if (
            self.claim.status is not ClaimStatus.NEEDS_REVIEW
            or self.claim.approval_status is not ApprovalStatus.PENDING
        ):
            raise ValueError("review items must contain pending needs-review claims")
        evidence_ids = tuple(item.id for item in self.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("review evidence must be unique")
        if any(
            item.claim_id != self.claim.id or item.id not in self.claim.evidence_ids
            for item in self.evidence
        ):
            raise ValueError("review evidence must be linked to its claim")
        if self.claim.source_type is SourceType.IMPORTED_RESUME and (
            not self.evidence
            or set(evidence_ids) != set(self.claim.evidence_ids)
            or any(
                item.source_type is not SourceType.IMPORTED_RESUME
                or item.source_ref != self.claim.source_ref
                or item.confirmation_status is not EvidenceConfirmationStatus.PENDING
                for item in self.evidence
            )
        ):
            raise ValueError(
                "imported review claims require complete pending imported evidence"
            )


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _text_span_locator(span: TextSourceSpan, source_sha256: str) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "kind": "text_span",
        "unit": "unicode_codepoint",
        "start": span.start,
        "end": span.end,
        "end_exclusive": True,
        "source_sha256": source_sha256,
    }


def _json_identity(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _snapshot_import_request(request: CreateImportProposal) -> CreateImportProposal:
    """Copy every nested request object before validation, hashing, and storage."""

    if not isinstance(request, CreateImportProposal):
        raise TypeError("request must be a CreateImportProposal")
    proposals: list[ProposedImportClaim] = []
    for proposal in request.proposals:
        _validate_json_value(proposal.value)
        value_snapshot: Any = json.loads(_json_identity(proposal.value))
        proposals.append(
            replace(
                proposal,
                value=value_snapshot,
                span=TextSourceSpan(
                    start=proposal.span.start,
                    end=proposal.span.end,
                    text=proposal.span.text,
                ),
                scope=Scope(type=proposal.scope.type, id=proposal.scope.id),
            )
        )
    return replace(request, proposals=tuple(proposals))


def _import_request_sha256(request: CreateImportProposal) -> str:
    payload = json.dumps(
        {
            "request": to_jsonable(request),
            "content_policy_version": PROFILE_IMPORT_CONTENT_POLICY_VERSION,
            "value_schema_version": PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _text_sha256(payload)


def _validated_source_spans(request: CreateImportProposal) -> tuple[str, ...]:
    source_length = len(request.source_text)
    result: list[str] = []
    selected_spans: list[tuple[int, int]] = []
    persisted_metadata: list[str | None] = [
        request.source_ref,
        request.extraction_method,
        request.artifact_id,
    ]
    total_metadata_codepoints = len(request.proposals) * (
        (2 * len(request.source_ref))
        + len(request.extraction_method)
        + (0 if request.artifact_id is None else len(request.artifact_id))
    )
    total_selected_codepoints = 0
    validate_profile_import_metadata(
        (
            request.source_ref,
            request.extraction_method,
            request.artifact_id,
        )
    )
    if total_metadata_codepoints > PROFILE_IMPORT_MAX_TOTAL_METADATA_CODEPOINTS:
        raise ValueError("profile import metadata exceeds the batch limit")
    claim_types = registered_profile_import_claim_types()
    for index, proposal in enumerate(request.proposals):
        if proposal.claim_type not in claim_types:
            raise ValueError(
                f"proposal {index} claim type is not allowed for profile import"
            )
        validate_profile_import_metadata(
            (
                proposal.subject_type,
                proposal.subject_id,
                proposal.scope.id,
            )
        )
        persisted_metadata.extend(
            (proposal.subject_type, proposal.subject_id, proposal.scope.id)
        )
        total_metadata_codepoints += sum(
            len(item)
            for item in (
                proposal.subject_type,
                proposal.subject_id,
                proposal.scope.id,
            )
            if item is not None
        )
        if total_metadata_codepoints > PROFILE_IMPORT_MAX_TOTAL_METADATA_CODEPOINTS:
            raise ValueError("profile import metadata exceeds the batch limit")
        span = proposal.span
        if span.end > source_length:
            raise ValueError(f"proposal {index} source span is outside the source text")
        exact_text = request.source_text[span.start : span.end]
        if exact_text != span.text:
            raise ValueError(f"proposal {index} source span does not match the source text")
        lookbehind_start = max(0, span.start - 512)
        preceding_text = request.source_text[lookbehind_start:span.start]
        preceding_breaks = tuple(_LINE_BREAK_PATTERN.finditer(preceding_text))
        if preceding_breaks:
            current_line_start = lookbehind_start + preceding_breaks[-1].end()
            previous_line_end = preceding_breaks[-1].start()
            previous_line_start = (
                preceding_breaks[-2].end() if len(preceding_breaks) > 1 else 0
            )
            preceding_line = preceding_text[
                previous_line_start:previous_line_end
            ].strip()
            if not preceding_line:
                preceding_line = None
        else:
            current_line_start = lookbehind_start
            preceding_line = None
        following_text = request.source_text[
            span.end : min(source_length, span.end + 256)
        ]
        following_break = _LINE_BREAK_PATTERN.search(following_text)
        current_line_end = (
            span.end + following_break.start()
            if following_break is not None
            else min(source_length, span.end + 256)
        )
        context_start = max(current_line_start, span.start - 256)
        validate_profile_import_proposal(
            claim_type=proposal.claim_type,
            value=proposal.value,
            canonical_text=proposal.canonical_text,
            evidence_text=exact_text,
            evidence_context=request.source_text[context_start:current_line_end],
            preceding_line=preceding_line,
        )
        total_selected_codepoints += len(exact_text)
        if total_selected_codepoints > PROFILE_IMPORT_MAX_TOTAL_EVIDENCE_CODEPOINTS:
            raise ValueError("profile import selected evidence exceeds the batch limit")
        result.append(exact_text)
        selected_spans.append((span.start, span.end))
    validate_profile_import_batch(
        source_text=request.source_text,
        spans=tuple(selected_spans),
        proposals=tuple(
            (proposal.value, proposal.canonical_text, exact_text)
            for proposal, exact_text in zip(request.proposals, result, strict=True)
        ),
        metadata=tuple(persisted_metadata),
    )
    return tuple(result)


def _import_sensitivity(proposal: ProposedImportClaim) -> Sensitivity:
    if proposal.sensitivity is Sensitivity.PUBLIC:
        return Sensitivity.PERSONAL
    return proposal.sensitivity


class ProfileService:
    """Validate storage mutations and expose deterministic resolution."""

    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def create_claim(self, request: CreateClaim, *, now: datetime | None = None) -> Claim:
        created_at = now or utc_now()
        if request.status is ClaimStatus.DERIVED:
            raise ValueError(
                "Derived claims must be created by a registered deterministic rule"
            )
        verified_at = request.verified_at
        if request.status is ClaimStatus.VERIFIED and verified_at is None:
            verified_at = created_at
        if request.status is ClaimStatus.VERIFIED and not (
            request.verified_by and request.verified_by.strip()
        ):
            raise ValueError("verified claims require verified_by")
        claim = Claim(
            id=request.claim_id or str(uuid.uuid4()),
            claim_type=request.claim_type,
            value_json=request.value,
            canonical_text=request.canonical_text,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            status=request.status,
            approval_status=request.approval_status,
            confidence=request.confidence,
            sensitivity=request.sensitivity,
            scope=request.scope,
            effective_from=request.effective_from,
            effective_to=request.effective_to,
            source_type=request.source_type,
            source_ref=request.source_ref,
            evidence_ids=request.evidence_ids,
            verified_at=verified_at,
            verified_by=request.verified_by,
            derivation=request.derivation,
            supersedes_id=request.supersedes_id,
            created_at=created_at,
            updated_at=created_at,
        )
        if not request.source_ref.strip():
            raise ValueError("source_ref must not be blank")
        derivation = claim.derivation
        self._repository.add_claim(
            claim_id=claim.id,
            claim_type=claim.claim_type,
            value=claim.value_json,
            canonical_text=claim.canonical_text,
            subject_type=claim.subject_type,
            subject_id=claim.subject_id,
            status=claim.status.value,
            approval_status=claim.approval_status.value,
            confidence=claim.confidence,
            sensitivity=claim.sensitivity.value,
            scope_type=claim.scope.type.value,
            scope_id=claim.scope.id,
            effective_from=_timestamp(claim.effective_from),
            effective_to=_timestamp(claim.effective_to),
            source_type=claim.source_type.value,
            source_ref=claim.source_ref,
            verified_at=_timestamp(claim.verified_at),
            verified_by=claim.verified_by,
            derivation_rule_name=None if derivation is None else derivation.rule_name,
            derivation_rule_version=None if derivation is None else derivation.rule_version,
            derivation_input_claim_ids=(
                () if derivation is None else derivation.input_claim_ids
            ),
            derivation_staleness_policy=(
                None if derivation is None else derivation.staleness_policy
            ),
            derivation_calculated_at=(
                None if derivation is None else _timestamp(derivation.calculated_at)
            ),
            supersedes_id=claim.supersedes_id,
            evidence_ids=claim.evidence_ids,
            created_at=_timestamp(claim.created_at),
        )
        return claim

    def create_evidence(
        self,
        request: CreateEvidence,
        *,
        now: datetime | None = None,
    ) -> Evidence:
        captured_at = now or utc_now()
        evidence = Evidence(
            id=request.evidence_id or str(uuid.uuid4()),
            claim_id=request.claim_id,
            source_type=request.source_type,
            source_ref=request.source_ref,
            artifact_id=request.artifact_id,
            locator=request.locator,
            source_text=request.source_text,
            extraction_method=request.extraction_method,
            captured_at=captured_at,
            checksum=request.checksum,
            confirmation_status=request.confirmation_status,
        )
        if request.confirmation_status is EvidenceConfirmationStatus.CONFIRMED and not (
            request.confirmed_by and request.confirmed_by.strip()
        ):
            raise ValueError("confirmed evidence requires confirmed_by")
        if self._repository.get_claim(evidence.claim_id) is None:
            raise RecordNotFoundError(f"Claim does not exist: {evidence.claim_id}")
        self._repository.add_evidence(
            evidence_id=evidence.id,
            claim_id=evidence.claim_id,
            artifact_id=evidence.artifact_id,
            locator=evidence.locator,
            source_text=evidence.source_text,
            extraction_method=evidence.extraction_method or "manual",
            captured_at=_timestamp(evidence.captured_at),
            checksum_sha256=evidence.checksum,
            source_type=evidence.source_type.value,
            source_ref=evidence.source_ref,
            confirmation_status=evidence.confirmation_status.value,
            confirmed_by=request.confirmed_by,
        )
        return evidence

    def create_import_proposal(
        self,
        request: CreateImportProposal,
        *,
        now: datetime | None = None,
    ) -> ImportProposalResult:
        """Atomically persist exact imported spans as review-only claims.

        The typed request deliberately exposes no status, approval, confirmation,
        or verification fields. Imported content and extractor confidence can
        therefore never authorize a claim for use. A successful idempotent retry
        reloads the original records instead of duplicating them.
        """

        request = _snapshot_import_request(request)
        created_at = now or utc_now()
        created_at_text = _timestamp(created_at)
        assert created_at_text is not None
        exact_spans = _validated_source_spans(request)
        source_sha256 = _text_sha256(request.source_text)
        request_sha256 = _import_request_sha256(request)
        stored_idempotency_key = _text_sha256(request.idempotency_key)
        workflow_input = {
            "request_schema_version": 1,
            "content_policy_version": PROFILE_IMPORT_CONTENT_POLICY_VERSION,
            "value_schema_version": PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
            "proposal_count": len(request.proposals),
            "source_sha256": source_sha256,
        }

        with self._repository.transaction():
            existing = self._repository.get_workflow_run_by_idempotency_key(
                _PROFILE_IMPORT_WORKFLOW,
                stored_idempotency_key,
            )
            workflow = self._repository.add_workflow_run(
                workflow_type=_PROFILE_IMPORT_WORKFLOW,
                status="running",
                idempotency_key=stored_idempotency_key,
                input_hash_sha256=request_sha256,
                input_data=workflow_input,
                current_step="persist_reviewable_claims",
                created_at=created_at_text,
            )
            if existing is not None:
                return self._import_result_from_workflow(
                    workflow,
                    request=request,
                    exact_spans=exact_spans,
                    source_sha256=source_sha256,
                )

            claim_ids: list[str] = []
            evidence_items: list[Evidence] = []
            generated_records: list[dict[str, str]] = []
            for proposal, exact_text in zip(
                request.proposals, exact_spans, strict=True
            ):
                claim = self.create_claim(
                    CreateClaim(
                        claim_type=proposal.claim_type,
                        value=proposal.value,
                        canonical_text=proposal.canonical_text,
                        source_type=SourceType.IMPORTED_RESUME,
                        source_ref=request.source_ref,
                        status=ClaimStatus.NEEDS_REVIEW,
                        approval_status=ApprovalStatus.PENDING,
                        subject_type=proposal.subject_type,
                        subject_id=proposal.subject_id,
                        confidence=proposal.confidence,
                        sensitivity=_import_sensitivity(proposal),
                        scope=proposal.scope,
                    ),
                    now=created_at,
                )
                evidence = self.create_evidence(
                    CreateEvidence(
                        claim_id=claim.id,
                        source_type=SourceType.IMPORTED_RESUME,
                        source_ref=request.source_ref,
                        source_text=exact_text,
                        locator=_text_span_locator(proposal.span, source_sha256),
                        extraction_method=request.extraction_method,
                        confirmation_status=EvidenceConfirmationStatus.PENDING,
                        artifact_id=request.artifact_id,
                        checksum=_text_sha256(exact_text),
                    ),
                    now=created_at,
                )
                claim_ids.append(claim.id)
                evidence_items.append(evidence)
                generated_records.append(
                    {"claim_id": claim.id, "evidence_id": evidence.id}
                )

            self._repository.update_workflow_run(
                _required_text(workflow, "id"),
                status="succeeded",
                current_step="awaiting_review",
                completed_steps=("validate_source_spans", "persist_reviewable_claims"),
                generated_artifacts=generated_records,
                finished_at=created_at_text,
            )
            claims = tuple(self.get_claim(claim_id) for claim_id in claim_ids)
            return ImportProposalResult(
                workflow_run_id=_required_text(workflow, "id"),
                source_sha256=source_sha256,
                claims=claims,
                evidence=tuple(evidence_items),
            )

    @staticmethod
    def preview_import_proposal(request: CreateImportProposal) -> ImportProposalPreview:
        """Validate a proposed import without consulting or mutating persistence."""

        request = _snapshot_import_request(request)
        _validated_source_spans(request)
        proposal_count = len(request.proposals)
        return ImportProposalPreview(
            source_sha256=_text_sha256(request.source_text),
            proposal_count=proposal_count,
            planned_claim_count=proposal_count,
            planned_evidence_count=proposal_count,
        )

    def get_claim(self, claim_id: str) -> Claim:
        record = self._repository.get_claim(claim_id)
        if record is None:
            raise RecordNotFoundError(f"Claim does not exist: {claim_id}")
        return self._claim_from_record(record)

    def list_claims(
        self,
        *,
        claim_type: str | None = None,
        status: ClaimStatus | None = None,
        approval_status: ApprovalStatus | None = None,
    ) -> tuple[Claim, ...]:
        records = self._repository.list_claims(
            claim_type=claim_type,
            status=None if status is None else status.value,
            approval_status=None if approval_status is None else approval_status.value,
        )
        return tuple(self._claim_from_record(record) for record in records)

    def list_review_items(self) -> tuple[ProfileReviewItem, ...]:
        """Return the pending review queue without changing trust state."""

        claims = self.list_claims(
            status=ClaimStatus.NEEDS_REVIEW,
            approval_status=ApprovalStatus.PENDING,
        )
        return tuple(
            ProfileReviewItem(
                claim=claim,
                evidence=self._evidence_for_claim(claim.id),
            )
            for claim in claims
        )

    def resolve(
        self,
        *,
        intent: str,
        policy: ClaimUsePolicy,
        require_question: str | None = None,
        requested_sensitivity: Sensitivity = Sensitivity.PERSONAL,
    ) -> ResolutionOutcome:
        claims = self.list_claims()
        evidence = tuple(
            item
            for claim in claims
            for item in self._evidence_for_claim(claim.id)
        )
        return resolve_claims(
            claims,
            intent=intent,
            policy=policy,
            evidence=evidence,
            question=require_question,
            requested_sensitivity=requested_sensitivity,
        )

    def _import_result_from_workflow(
        self,
        workflow: Record,
        *,
        request: CreateImportProposal,
        exact_spans: tuple[str, ...],
        source_sha256: str,
    ) -> ImportProposalResult:
        workflow_id = _required_text(workflow, "id")
        if workflow.get("status") != "succeeded":
            raise RepositoryError(
                f"Profile import proposal is not safely retryable: {workflow_id}"
            )
        stored_items = _json_value(
            workflow.get("generated_artifacts_json"),
            field_name="generated_artifacts_json",
        )
        if (
            not isinstance(stored_items, list)
            or len(stored_items) != len(request.proposals)
        ):
            raise RepositoryError(
                f"Profile import proposal result does not match its request: {workflow_id}"
            )

        claims: list[Claim] = []
        evidence_items: list[Evidence] = []
        seen_claim_ids: set[str] = set()
        seen_evidence_ids: set[str] = set()
        for item, proposal, exact_text in zip(
            stored_items,
            request.proposals,
            exact_spans,
            strict=True,
        ):
            if not isinstance(item, dict):
                raise RepositoryError(
                    f"Profile import proposal result is malformed: {workflow_id}"
                )
            claim_id = item.get("claim_id")
            evidence_id = item.get("evidence_id")
            if not isinstance(claim_id, str) or not isinstance(evidence_id, str):
                raise RepositoryError(
                    f"Profile import proposal result is malformed: {workflow_id}"
                )
            if claim_id in seen_claim_ids or evidence_id in seen_evidence_ids:
                raise RepositoryError(
                    f"Profile import proposal result contains duplicates: {workflow_id}"
                )
            seen_claim_ids.add(claim_id)
            seen_evidence_ids.add(evidence_id)
            claim = self.get_claim(claim_id)
            evidence = next(
                (
                    candidate
                    for candidate in self._evidence_for_claim(claim_id)
                    if candidate.id == evidence_id
                ),
                None,
            )
            if evidence is None:
                raise RepositoryError(
                    f"Profile import proposal evidence is missing: {workflow_id}"
                )
            expected_locator = _text_span_locator(proposal.span, source_sha256)
            if (
                claim.claim_type != proposal.claim_type
                or _json_identity(claim.value_json) != _json_identity(proposal.value)
                or claim.canonical_text != proposal.canonical_text
                or claim.subject_type != proposal.subject_type
                or claim.subject_id != proposal.subject_id
                or claim.confidence != proposal.confidence
                or claim.sensitivity is not _import_sensitivity(proposal)
                or claim.scope != proposal.scope
                or claim.source_ref != request.source_ref
                or evidence.artifact_id != request.artifact_id
                or evidence.locator != expected_locator
                or evidence.source_text != exact_text
                or evidence.extraction_method != request.extraction_method
                or evidence.checksum != _text_sha256(exact_text)
                or evidence.source_ref != request.source_ref
            ):
                raise RepositoryError(
                    f"Profile import proposal result failed integrity checks: {workflow_id}"
                )
            claims.append(claim)
            evidence_items.append(evidence)

        return ImportProposalResult(
            workflow_run_id=workflow_id,
            source_sha256=source_sha256,
            claims=tuple(claims),
            evidence=tuple(evidence_items),
        )

    def _claim_from_record(self, record: Record) -> Claim:
        status = ClaimStatus(_required_text(record, "status"))
        derivation: Derivation | None = None
        if status is ClaimStatus.DERIVED:
            derivation = Derivation(
                rule_name=_required_text(record, "derivation_rule_name"),
                rule_version=_required_text(record, "derivation_rule_version"),
                input_claim_ids=tuple(
                    self._repository.list_derivation_input_ids(_required_text(record, "id"))
                ),
                staleness_policy=_required_text(record, "derivation_staleness_policy"),
                calculated_at=_required_timestamp(record, "derivation_calculated_at"),
            )
        evidence_ids = tuple(
            _required_text(link, "evidence_id")
            for link in self._repository.list_claim_evidence(
                claim_id=_required_text(record, "id")
            )
        )
        confidence = record.get("confidence")
        if not isinstance(confidence, (int, float)):
            raise TypeError("Stored confidence must be numeric")
        return Claim(
            id=_required_text(record, "id"),
            claim_type=_required_text(record, "claim_type"),
            value_json=_json_value(record.get("value_json"), field_name="value_json"),
            canonical_text=_required_text(record, "canonical_text"),
            subject_type=_required_text(record, "subject_type"),
            subject_id=_optional_text(record, "subject_id"),
            status=status,
            approval_status=ApprovalStatus(_required_text(record, "approval_status")),
            confidence=float(confidence),
            sensitivity=Sensitivity(_required_text(record, "sensitivity")),
            scope=Scope(
                type=ScopeType(_required_text(record, "scope_type")),
                id=_optional_text(record, "scope_id"),
            ),
            effective_from=_parse_timestamp(
                record.get("effective_from"), field_name="effective_from"
            ),
            effective_to=_parse_timestamp(
                record.get("effective_to"), field_name="effective_to"
            ),
            source_type=SourceType(_required_text(record, "source_type")),
            source_ref=_optional_text(record, "source_ref"),
            evidence_ids=evidence_ids,
            verified_at=_parse_timestamp(record.get("verified_at"), field_name="verified_at"),
            verified_by=_optional_text(record, "verified_by"),
            derivation=derivation,
            supersedes_id=_optional_text(record, "supersedes_id"),
            created_at=_required_timestamp(record, "created_at"),
            updated_at=_required_timestamp(record, "updated_at"),
        )

    def _evidence_for_claim(self, claim_id: str) -> tuple[Evidence, ...]:
        result: list[Evidence] = []
        links = self._repository.list_claim_evidence(
            claim_id=claim_id,
            relationship="supports",
        )
        for link in links:
            evidence_id = _required_text(link, "evidence_id")
            record = self._repository.get_evidence(evidence_id)
            if record is None:
                raise RecordNotFoundError(f"Evidence does not exist: {evidence_id}")
            locator = _json_value(record.get("locator_json"), field_name="locator_json")
            result.append(
                Evidence(
                    id=_required_text(record, "id"),
                    claim_id=claim_id,
                    source_type=SourceType(_required_text(record, "source_type")),
                    source_ref=_required_text(record, "source_ref"),
                    artifact_id=_optional_text(record, "artifact_id"),
                    locator=locator,
                    source_text=_optional_text(record, "source_text"),
                    extraction_method=_optional_text(record, "extraction_method"),
                    captured_at=_required_timestamp(record, "captured_at"),
                    checksum=_optional_text(record, "checksum_sha256"),
                    confirmation_status=EvidenceConfirmationStatus(
                        _required_text(record, "confirmation_status")
                    ),
                )
            )
        return tuple(result)
