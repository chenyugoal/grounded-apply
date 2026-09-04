"""Validated candidate-claim workflows over the mapping repository API."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping, TypeVar

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
    PROFILE_IMPORT_RESTRICTED_TAXONOMY_SHA256,
    PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION,
    PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
    registered_profile_import_claim_types,
    validate_profile_import_batch,
    validate_profile_import_metadata,
    validate_profile_import_proposal,
)


_PROFILE_IMPORT_WORKFLOW = "profile_import_proposal"
_MAX_IMPORT_PROPOSALS = 1000
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_PROFILE_IMPORT_SOURCE_REF_PATTERN = re.compile(r"sha256:(?P<digest>[0-9a-f]{64})")
_LINE_BREAK_PATTERN = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")

PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION = 2
PROFILE_IMPORT_REQUEST_SCHEMA_VERSION = 3
PROFILE_IMPORT_SOURCE_IDENTITY_SCHEMA_VERSION = 1
PROFILE_IMPORT_RESULT_MANIFEST_SCHEMA_VERSION = 2
PROFILE_IMPORT_SPAN_LOCATOR_SCHEMA_VERSION = 1
PROFILE_IMPORT_RECORD_ID_SCHEMA_VERSION = 1
PROFILE_IMPORT_EXTRACTOR_ID = "grounded-apply.profile-import.manifest@1"

_PROFILE_IMPORT_EXTRACTORS: Mapping[str, int] = MappingProxyType(
    {PROFILE_IMPORT_EXTRACTOR_ID: PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION}
)
_PROFILE_IMPORT_ARTIFACT_TYPE = "profile_import_source_digest"
_PROFILE_IMPORT_ARTIFACT_MEDIA_TYPE = "text/plain; charset=utf-8"
_StoredEnum = TypeVar("_StoredEnum", bound=Enum)
_PROFILE_IMPORT_ARTIFACT_METADATA_BASE: Mapping[str, JsonValue] = MappingProxyType(
    {
        "retention": "digest_only",
        "source_identity_schema_version": PROFILE_IMPORT_SOURCE_IDENTITY_SCHEMA_VERSION,
    }
)


def _profile_import_artifact_metadata(
    source_codepoint_size: int,
) -> dict[str, JsonValue]:
    if (
        isinstance(source_codepoint_size, bool)
        or not isinstance(source_codepoint_size, int)
        or source_codepoint_size <= 0
    ):
        raise ValueError("source_codepoint_size must be a positive integer")
    return {
        **_PROFILE_IMPORT_ARTIFACT_METADATA_BASE,
        "source_codepoint_size": source_codepoint_size,
    }


def registered_profile_import_extractors() -> frozenset[str]:
    """Return immutable identifiers for application-owned import adapters."""

    return frozenset(_PROFILE_IMPORT_EXTRACTORS)


def _profile_import_extractor_manifest_version(extractor_id: object) -> int | None:
    if not isinstance(extractor_id, str):
        return None
    return _PROFILE_IMPORT_EXTRACTORS.get(extractor_id)


def _require_registered_profile_import_extractor(
    extractor_id: object,
    *,
    manifest_schema_version: int = PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION,
) -> str:
    if (
        not isinstance(extractor_id, str)
        or _profile_import_extractor_manifest_version(extractor_id)
        != manifest_schema_version
    ):
        raise ValueError("profile import extractor must have a registered identifier")
    return extractor_id


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _profile_import_source_ref(source_sha256: str) -> str:
    if _SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise ValueError("source_sha256 must contain a lowercase SHA-256 digest")
    return f"sha256:{source_sha256}"


def _profile_import_source_artifact_id(source_sha256: str) -> str:
    if _SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise ValueError("source_sha256 must contain a lowercase SHA-256 digest")
    return f"profile-import-source:sha256:{source_sha256}"


def _profile_import_record_ids(workflow_id: str, index: int) -> tuple[str, str]:
    """Derive replay-verifiable record IDs from one workflow and proposal slot."""

    _require_text(workflow_id, "workflow_id")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("profile import record index must be a non-negative integer")
    prefix = (
        "urn:grounded-apply:profile-import-record:"
        f"v{PROFILE_IMPORT_RECORD_ID_SCHEMA_VERSION}:{workflow_id}:{index}"
    )
    return (
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}:claim")),
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}:evidence")),
    )


def _is_canonical_uuid(value: object, *, version: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and parsed.version == version


def _profile_import_source_digest(source_ref: object) -> str | None:
    if not isinstance(source_ref, str):
        return None
    match = _PROFILE_IMPORT_SOURCE_REF_PATTERN.fullmatch(source_ref)
    return None if match is None else match.group("digest")


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
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from None
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


def _require_profile_import_source_artifact_record(
    artifact: Record | None,
    *,
    source_sha256: str,
    expected_byte_size: int | None,
    expected_codepoint_size: int | None,
) -> tuple[Record, int]:
    """Validate a digest-only source record without exposing stored metadata."""

    if artifact is None:
        raise RepositoryError("Profile import source identity record is missing")
    try:
        metadata = _json_value(
            artifact.get("metadata_json"),
            field_name="artifact metadata_json",
        )
    except (TypeError, ValueError) as error:
        raise RepositoryError(
            "Profile import source identity record failed integrity checks"
        ) from error
    source_ref = _profile_import_source_ref(source_sha256)
    source_artifact_id = _profile_import_source_artifact_id(source_sha256)
    byte_size = artifact.get("byte_size")
    source_codepoint_size = (
        metadata.get("source_codepoint_size") if isinstance(metadata, dict) else None
    )
    byte_size_invalid = (
        isinstance(byte_size, bool)
        or not isinstance(byte_size, int)
        or byte_size <= 0
        or byte_size > PROFILE_IMPORT_MAX_SOURCE_BYTES
        or (expected_byte_size is not None and byte_size != expected_byte_size)
    )
    codepoint_size_invalid = (
        isinstance(source_codepoint_size, bool)
        or not isinstance(source_codepoint_size, int)
        or source_codepoint_size <= 0
        or (isinstance(byte_size, int) and source_codepoint_size > byte_size)
        or (
            expected_codepoint_size is not None
            and source_codepoint_size != expected_codepoint_size
        )
    )
    if (
        artifact.get("id") != source_artifact_id
        or artifact.get("artifact_type") != _PROFILE_IMPORT_ARTIFACT_TYPE
        or artifact.get("uri") != source_ref
        or artifact.get("local_path") is not None
        or artifact.get("original_name") is not None
        or artifact.get("media_type") != _PROFILE_IMPORT_ARTIFACT_MEDIA_TYPE
        or byte_size_invalid
        or artifact.get("content_sha256") != source_sha256
        or artifact.get("sensitivity") != Sensitivity.PERSONAL.value
        or codepoint_size_invalid
        or metadata != _profile_import_artifact_metadata(source_codepoint_size)
    ):
        raise RepositoryError(
            "Profile import source identity record failed integrity checks"
        )
    assert isinstance(source_codepoint_size, int)
    return artifact, source_codepoint_size


def _required_text(record: Record, field_name: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Stored {field_name} must be non-blank text")
    return value


def _required_enum(
    enum_type: type[_StoredEnum],
    record: Record,
    field_name: str,
) -> _StoredEnum:
    value = _required_text(record, field_name)
    try:
        return enum_type(value)
    except ValueError:
        raise ValueError(f"Stored {field_name} is invalid") from None


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
    source_text: str
    expected_source_sha256: str
    proposals: tuple[ProposedImportClaim, ...]
    source_sha256: str = field(init=False)
    source_ref: str = field(init=False)
    source_artifact_id: str = field(init=False)
    extractor_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_text(self.idempotency_key, "idempotency_key")
        _require_text(self.source_text, "source_text")
        _require_text(self.expected_source_sha256, "expected_source_sha256")
        if _IDEMPOTENCY_KEY_PATTERN.fullmatch(self.idempotency_key) is None:
            raise ValueError("idempotency_key must be an opaque identifier")
        if len(self.source_text.encode("utf-8")) > PROFILE_IMPORT_MAX_SOURCE_BYTES:
            raise ValueError("source_text must not exceed 16 MiB as UTF-8")
        if _SHA256_PATTERN.fullmatch(self.expected_source_sha256) is None:
            raise ValueError(
                "expected_source_sha256 must contain a lowercase SHA-256 digest"
            )
        source_sha256 = _text_sha256(self.source_text)
        if source_sha256 != self.expected_source_sha256:
            raise ValueError("profile import source digest does not match its manifest")
        extractor_id = _require_registered_profile_import_extractor(
            PROFILE_IMPORT_EXTRACTOR_ID
        )
        object.__setattr__(self, "source_sha256", source_sha256)
        object.__setattr__(self, "source_ref", _profile_import_source_ref(source_sha256))
        object.__setattr__(
            self,
            "source_artifact_id",
            _profile_import_source_artifact_id(source_sha256),
        )
        object.__setattr__(self, "extractor_id", extractor_id)
        if not isinstance(self.proposals, tuple):
            raise TypeError("proposals must be a tuple")
        if not self.proposals:
            raise ValueError("proposals must not be empty")
        if len(self.proposals) > _MAX_IMPORT_PROPOSALS:
            raise ValueError("proposals must not contain more than 1000 items")
        if any(not isinstance(item, ProposedImportClaim) for item in self.proposals):
            raise TypeError("proposals must contain only ProposedImportClaim values")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportProposalResult:
    """Persisted, review-only claims and their exact pending evidence."""

    workflow_run_id: str
    source_sha256: str
    source_ref: str
    source_artifact_id: str
    extractor_id: str
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        _require_text(self.workflow_run_id, "workflow_run_id")
        if (
            _SHA256_PATTERN.fullmatch(self.source_sha256) is None
            or self.source_ref != _profile_import_source_ref(self.source_sha256)
            or self.source_artifact_id
            != _profile_import_source_artifact_id(self.source_sha256)
        ):
            raise ValueError("import result requires application-owned source identity")
        _require_registered_profile_import_extractor(self.extractor_id)
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
            or claim.source_ref != self.source_ref
            for claim in self.claims
        ):
            raise ValueError("import result claims must remain pending user review")
        if any(
            item.confirmation_status is not EvidenceConfirmationStatus.PENDING
            or item.source_type is not SourceType.IMPORTED_RESUME
            or item.source_ref != self.source_ref
            or item.artifact_id != self.source_artifact_id
            or item.extraction_method != self.extractor_id
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
    source_ref: str
    source_artifact_id: str
    extractor_id: str
    proposal_count: int
    planned_claim_count: int
    planned_evidence_count: int
    review_required: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if (
            _SHA256_PATTERN.fullmatch(self.source_sha256) is None
            or self.source_ref != _profile_import_source_ref(self.source_sha256)
            or self.source_artifact_id
            != _profile_import_source_artifact_id(self.source_sha256)
        ):
            raise ValueError("import preview requires application-owned source identity")
        _require_registered_profile_import_extractor(self.extractor_id)
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


def _profile_import_evidence_has_valid_identity(
    evidence: Evidence,
    *,
    source_sha256: str,
) -> bool:
    source_text = evidence.source_text
    locator = evidence.locator
    if (
        not isinstance(source_text, str)
        or not source_text.strip()
        or not isinstance(locator, dict)
        or set(locator)
        != {
            "schema_version",
            "kind",
            "unit",
            "start",
            "end",
            "end_exclusive",
            "source_sha256",
        }
    ):
        return False
    start = locator.get("start")
    end = locator.get("end")
    return (
        locator.get("schema_version") == PROFILE_IMPORT_SPAN_LOCATOR_SCHEMA_VERSION
        and locator.get("kind") == "text_span"
        and locator.get("unit") == "unicode_codepoint"
        and locator.get("end_exclusive") is True
        and locator.get("source_sha256") == source_sha256
        and not isinstance(start, bool)
        and isinstance(start, int)
        and start >= 0
        and not isinstance(end, bool)
        and isinstance(end, int)
        and end > start
        and end - start == len(source_text)
        and evidence.checksum == _text_sha256(source_text)
    )


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
            len(self.evidence) != 1
            or self.claim.verified_at is not None
            or self.claim.verified_by is not None
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
        if self.claim.source_type is SourceType.IMPORTED_RESUME:
            source_sha256 = _profile_import_source_digest(self.claim.source_ref)
            if source_sha256 is None:
                raise ValueError(
                    "imported review source provenance is not application-owned"
                )
            if not _is_canonical_uuid(self.claim.id, version=5) or any(
                not _is_canonical_uuid(item.id, version=5)
                for item in self.evidence
            ):
                raise ValueError(
                    "imported review record identity is not application-owned"
                )
            source_artifact_id = _profile_import_source_artifact_id(source_sha256)
            if any(
                item.artifact_id != source_artifact_id
                or _profile_import_extractor_manifest_version(
                    item.extraction_method
                )
                is None
                or not _profile_import_evidence_has_valid_identity(
                    item,
                    source_sha256=source_sha256,
                )
                for item in self.evidence
            ):
                raise ValueError(
                    "imported review evidence provenance failed integrity checks"
                )


def _text_span_locator(span: TextSourceSpan, source_sha256: str) -> dict[str, JsonValue]:
    return {
        "schema_version": PROFILE_IMPORT_SPAN_LOCATOR_SCHEMA_VERSION,
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


def _import_workflow_input(
    request: CreateImportProposal,
    *,
    idempotency_key_sha256: str,
) -> dict[str, JsonValue]:
    proposals = to_jsonable(request.proposals)
    return {
        "request_schema_version": PROFILE_IMPORT_REQUEST_SCHEMA_VERSION,
        "manifest_schema_version": PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION,
        "result_manifest_schema_version": PROFILE_IMPORT_RESULT_MANIFEST_SCHEMA_VERSION,
        "record_id_schema_version": PROFILE_IMPORT_RECORD_ID_SCHEMA_VERSION,
        "source_identity_schema_version": PROFILE_IMPORT_SOURCE_IDENTITY_SCHEMA_VERSION,
        "span_locator_schema_version": PROFILE_IMPORT_SPAN_LOCATOR_SCHEMA_VERSION,
        "content_policy_version": PROFILE_IMPORT_CONTENT_POLICY_VERSION,
        "restricted_taxonomy_sha256": PROFILE_IMPORT_RESTRICTED_TAXONOMY_SHA256,
        "restricted_taxonomy_version": PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION,
        "value_schema_version": PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
        "extractor_id": request.extractor_id,
        "idempotency_key_sha256": idempotency_key_sha256,
        "proposal_count": len(request.proposals),
        "proposals_sha256": _text_sha256(_json_identity(proposals)),
        "source_artifact_id": request.source_artifact_id,
        "source_byte_size": len(request.source_text.encode("utf-8")),
        "source_codepoint_size": len(request.source_text),
        "source_ref": request.source_ref,
        "source_sha256": request.source_sha256,
    }


def _import_request_sha256(workflow_input: dict[str, JsonValue]) -> str:
    return _text_sha256(_json_identity(workflow_input))


def _validated_source_spans(request: CreateImportProposal) -> tuple[str, ...]:
    source_length = len(request.source_text)
    result: list[str] = []
    selected_spans: list[tuple[int, int]] = []
    persisted_metadata: list[str | None] = [
        request.source_ref,
        request.extractor_id,
        request.source_artifact_id,
    ]
    total_metadata_codepoints = len(request.proposals) * (
        (2 * len(request.source_ref))
        + len(request.extractor_id)
        + len(request.source_artifact_id)
    )
    total_selected_codepoints = 0
    validate_profile_import_metadata(
        (
            request.source_ref,
            request.extractor_id,
            request.source_artifact_id,
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
            preceding_lines = _LINE_BREAK_PATTERN.split(preceding_text)[:-1]
            preceding_line = next(
                (
                    line.strip()
                    for line in reversed(preceding_lines)
                    if line.strip()
                ),
                None,
            )
        else:
            if (
                lookbehind_start > 0
                and _LINE_BREAK_PATTERN.fullmatch(
                    request.source_text[lookbehind_start - 1 : lookbehind_start]
                )
                is None
            ):
                raise ValueError(
                    f"proposal {index} same-line context exceeds the safe limit"
                )
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
            evidence_prefix=request.source_text[current_line_start:span.start],
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
        if request.source_type is SourceType.IMPORTED_RESUME:
            raise ValueError(
                "Imported claims must be created by the profile import workflow"
            )
        return self._create_claim(request, now=now)

    def _create_claim(
        self,
        request: CreateClaim,
        *,
        now: datetime | None = None,
    ) -> Claim:
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
        if request.source_type is SourceType.IMPORTED_RESUME:
            raise ValueError(
                "Imported evidence must be created by the profile import workflow"
            )
        return self._create_evidence(request, now=now)

    def _create_evidence(
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
            created_at=_timestamp(evidence.captured_at),
        )
        return evidence

    def _require_profile_import_source_artifact(
        self,
        request: CreateImportProposal,
        *,
        create_if_missing: bool,
        created_at: str,
    ) -> Record:
        """Create or verify the digest-only source identity without repairing it."""

        artifact = self._repository.get_artifact(request.source_artifact_id)
        if artifact is None and create_if_missing:
            if self._repository.list_evidence(
                artifact_id=request.source_artifact_id,
                limit=1,
            ):
                raise RepositoryError(
                    "Profile import source identity record is missing"
                )
            artifact = self._repository.add_artifact(
                artifact_id=request.source_artifact_id,
                artifact_type=_PROFILE_IMPORT_ARTIFACT_TYPE,
                uri=request.source_ref,
                local_path=None,
                original_name=None,
                media_type=_PROFILE_IMPORT_ARTIFACT_MEDIA_TYPE,
                byte_size=len(request.source_text.encode("utf-8")),
                content_sha256=request.source_sha256,
                sensitivity=Sensitivity.PERSONAL.value,
                metadata=_profile_import_artifact_metadata(len(request.source_text)),
                captured_at=created_at,
                created_at=created_at,
            )
        validated_artifact, _ = _require_profile_import_source_artifact_record(
            artifact,
            source_sha256=request.source_sha256,
            expected_byte_size=len(request.source_text.encode("utf-8")),
            expected_codepoint_size=len(request.source_text),
        )
        return validated_artifact

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
        stored_idempotency_key = _text_sha256(request.idempotency_key)
        workflow_input = _import_workflow_input(
            request,
            idempotency_key_sha256=stored_idempotency_key,
        )
        request_sha256 = _import_request_sha256(workflow_input)

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
            self._require_profile_import_source_artifact(
                request,
                create_if_missing=existing is None,
                created_at=created_at_text,
            )
            if existing is not None:
                return self._import_result_from_workflow(
                    workflow,
                    request=request,
                    exact_spans=exact_spans,
                )

            workflow_id = _required_text(workflow, "id")
            claim_ids: list[str] = []
            evidence_items: list[Evidence] = []
            generated_records: list[dict[str, str]] = []
            for index, (proposal, exact_text) in enumerate(
                zip(request.proposals, exact_spans, strict=True)
            ):
                claim_id, evidence_id = _profile_import_record_ids(workflow_id, index)
                claim = self._create_claim(
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
                        claim_id=claim_id,
                    ),
                    now=created_at,
                )
                evidence = self._create_evidence(
                    CreateEvidence(
                        claim_id=claim.id,
                        source_type=SourceType.IMPORTED_RESUME,
                        source_ref=request.source_ref,
                        source_text=exact_text,
                        locator=_text_span_locator(
                            proposal.span,
                            request.source_sha256,
                        ),
                        extraction_method=request.extractor_id,
                        confirmation_status=EvidenceConfirmationStatus.PENDING,
                        artifact_id=request.source_artifact_id,
                        checksum=_text_sha256(exact_text),
                        evidence_id=evidence_id,
                    ),
                    now=created_at,
                )
                claim_ids.append(claim.id)
                evidence_items.append(evidence)
                generated_records.append(
                    {"claim_id": claim.id, "evidence_id": evidence.id}
                )

            self._repository.update_workflow_run(
                workflow_id,
                status="succeeded",
                current_step="awaiting_review",
                completed_steps=(
                    "validate_source_binding",
                    "validate_source_spans",
                    "persist_source_identity",
                    "persist_reviewable_claims",
                ),
                generated_artifacts={
                    "schema_version": PROFILE_IMPORT_RESULT_MANIFEST_SCHEMA_VERSION,
                    "source_artifact_id": request.source_artifact_id,
                    "records": generated_records,
                },
                finished_at=created_at_text,
            )
            claims = tuple(self.get_claim(claim_id) for claim_id in claim_ids)
            return ImportProposalResult(
                workflow_run_id=workflow_id,
                source_sha256=request.source_sha256,
                source_ref=request.source_ref,
                source_artifact_id=request.source_artifact_id,
                extractor_id=request.extractor_id,
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
            source_sha256=request.source_sha256,
            source_ref=request.source_ref,
            source_artifact_id=request.source_artifact_id,
            extractor_id=request.extractor_id,
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
        items: list[ProfileReviewItem] = []
        for claim in claims:
            item = ProfileReviewItem(
                claim=claim,
                evidence=self._evidence_for_claim(claim.id),
            )
            if claim.source_type is SourceType.IMPORTED_RESUME:
                source_sha256 = _profile_import_source_digest(claim.source_ref)
                assert source_sha256 is not None
                evidence = item.evidence[0]
                links = self._repository.list_claim_evidence(claim_id=claim.id)
                evidence_record = self._repository.get_evidence(evidence.id)
                if evidence_record is None:
                    raise RepositoryError(
                        "Profile import review provenance failed integrity checks"
                    )
                try:
                    evidence_metadata = _json_value(
                        evidence_record.get("metadata_json"),
                        field_name="evidence metadata_json",
                    )
                except (TypeError, ValueError) as error:
                    raise RepositoryError(
                        "Profile import review provenance failed integrity checks"
                    ) from error
                if (
                    claim.effective_from is not None
                    or claim.effective_to is not None
                    or claim.supersedes_id is not None
                    or len(links) != 1
                    or links[0].get("evidence_id") != evidence.id
                    or links[0].get("relationship") != "supports"
                    or links[0].get("strength") != 1.0
                    or links[0].get("note") is not None
                    or evidence_metadata != {}
                    or evidence_record.get("confirmed_at") is not None
                    or evidence_record.get("confirmed_by") is not None
                ):
                    raise RepositoryError(
                        "Profile import review provenance failed integrity checks"
                    )
                _, source_codepoint_size = _require_profile_import_source_artifact_record(
                    self._repository.get_artifact(
                        _profile_import_source_artifact_id(source_sha256)
                    ),
                    source_sha256=source_sha256,
                    expected_byte_size=None,
                    expected_codepoint_size=None,
                )
                if any(
                    not isinstance(evidence.locator, dict)
                    or not isinstance(evidence.locator.get("end"), int)
                    or isinstance(evidence.locator.get("end"), bool)
                    or evidence.locator["end"] > source_codepoint_size
                    for evidence in item.evidence
                ):
                    raise RepositoryError(
                        "Profile import review provenance failed integrity checks"
                    )
            items.append(item)
        return tuple(items)

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
    ) -> ImportProposalResult:
        workflow_id = _required_text(workflow, "id")
        if not _is_canonical_uuid(workflow_id, version=4):
            raise RepositoryError("Profile import proposal checkpoint identity is invalid")
        try:
            completed_steps = _json_value(
                workflow.get("completed_steps_json"),
                field_name="completed_steps_json",
            )
            outstanding_need_info = _json_value(
                workflow.get("outstanding_need_info_json"),
                field_name="outstanding_need_info_json",
            )
            retry_policy = _json_value(
                workflow.get("retry_policy_json"),
                field_name="retry_policy_json",
            )
        except (TypeError, ValueError) as error:
            raise RepositoryError(
                f"Profile import proposal checkpoint is malformed: {workflow_id}"
            ) from error
        if (
            workflow.get("status") != "succeeded"
            or workflow.get("current_step") != "awaiting_review"
            or completed_steps
            != [
                "validate_source_binding",
                "validate_source_spans",
                "persist_source_identity",
                "persist_reviewable_claims",
            ]
            or outstanding_need_info != []
            or retry_policy != {}
            or workflow.get("model_name") is not None
            or workflow.get("prompt_version") is not None
            or workflow.get("failure_code") is not None
            or workflow.get("failure_reason") is not None
            or workflow.get("started_at") != workflow.get("created_at")
            or workflow.get("finished_at") != workflow.get("created_at")
        ):
            raise RepositoryError(
                f"Profile import proposal is not safely retryable: {workflow_id}"
            )
        stored_manifest = _json_value(
            workflow.get("generated_artifacts_json"),
            field_name="generated_artifacts_json",
        )
        if (
            not isinstance(stored_manifest, dict)
            or set(stored_manifest)
            != {"schema_version", "source_artifact_id", "records"}
            or stored_manifest.get("schema_version")
            != PROFILE_IMPORT_RESULT_MANIFEST_SCHEMA_VERSION
            or stored_manifest.get("source_artifact_id")
            != request.source_artifact_id
            or not isinstance(stored_manifest.get("records"), list)
        ):
            raise RepositoryError(
                f"Profile import proposal result does not match its request: {workflow_id}"
            )
        stored_items = stored_manifest["records"]
        assert isinstance(stored_items, list)
        if len(stored_items) != len(request.proposals):
            raise RepositoryError(
                f"Profile import proposal result does not match its request: {workflow_id}"
            )

        claims: list[Claim] = []
        evidence_items: list[Evidence] = []
        seen_claim_ids: set[str] = set()
        seen_evidence_ids: set[str] = set()
        for index, (item, proposal, exact_text) in enumerate(
            zip(
                stored_items,
                request.proposals,
                exact_spans,
                strict=True,
            )
        ):
            if not isinstance(item, dict):
                raise RepositoryError(
                    f"Profile import proposal result is malformed: {workflow_id}"
                )
            if set(item) != {"claim_id", "evidence_id"}:
                raise RepositoryError(
                    f"Profile import proposal result is malformed: {workflow_id}"
                )
            claim_id = item.get("claim_id")
            evidence_id = item.get("evidence_id")
            if not isinstance(claim_id, str) or not isinstance(evidence_id, str):
                raise RepositoryError(
                    f"Profile import proposal result is malformed: {workflow_id}"
                )
            expected_claim_id, expected_evidence_id = _profile_import_record_ids(
                workflow_id,
                index,
            )
            if (claim_id, evidence_id) != (expected_claim_id, expected_evidence_id):
                raise RepositoryError(
                    "Profile import proposal record identities are invalid: "
                    f"{workflow_id}"
                )
            if claim_id in seen_claim_ids or evidence_id in seen_evidence_ids:
                raise RepositoryError(
                    f"Profile import proposal result contains duplicates: {workflow_id}"
                )
            seen_claim_ids.add(claim_id)
            seen_evidence_ids.add(evidence_id)
            claim = self.get_claim(claim_id)
            links = self._repository.list_claim_evidence(claim_id=claim_id)
            linked_evidence = self._evidence_for_claim(claim_id)
            if (
                len(links) != 1
                or links[0].get("claim_id") != claim_id
                or links[0].get("evidence_id") != evidence_id
                or links[0].get("relationship") != "supports"
                or links[0].get("strength") != 1.0
                or links[0].get("note") is not None
                or len(linked_evidence) != 1
                or linked_evidence[0].id != evidence_id
                or claim.evidence_ids != (evidence_id,)
            ):
                raise RepositoryError(
                    f"Profile import proposal evidence links are invalid: {workflow_id}"
                )
            evidence = linked_evidence[0]
            evidence_record = self._repository.get_evidence(evidence_id)
            assert evidence_record is not None
            try:
                evidence_metadata = _json_value(
                    evidence_record.get("metadata_json"),
                    field_name="evidence metadata_json",
                )
            except (TypeError, ValueError) as error:
                raise RepositoryError(
                    f"Profile import proposal evidence is malformed: {workflow_id}"
                ) from error
            expected_locator = _text_span_locator(
                proposal.span,
                request.source_sha256,
            )
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
                or claim.effective_from is not None
                or claim.effective_to is not None
                or claim.supersedes_id is not None
                or evidence.artifact_id != request.source_artifact_id
                or evidence.locator != expected_locator
                or evidence.source_text != exact_text
                or evidence.extraction_method != request.extractor_id
                or evidence.checksum != _text_sha256(exact_text)
                or evidence.source_ref != request.source_ref
                or evidence_metadata != {}
                or evidence_record.get("confirmed_at") is not None
                or evidence_record.get("confirmed_by") is not None
            ):
                raise RepositoryError(
                    f"Profile import proposal result failed integrity checks: {workflow_id}"
                )
            claims.append(claim)
            evidence_items.append(evidence)

        return ImportProposalResult(
            workflow_run_id=workflow_id,
            source_sha256=request.source_sha256,
            source_ref=request.source_ref,
            source_artifact_id=request.source_artifact_id,
            extractor_id=request.extractor_id,
            claims=tuple(claims),
            evidence=tuple(evidence_items),
        )

    def _claim_from_record(self, record: Record) -> Claim:
        status = _required_enum(ClaimStatus, record, "status")
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
            approval_status=_required_enum(ApprovalStatus, record, "approval_status"),
            confidence=float(confidence),
            sensitivity=_required_enum(Sensitivity, record, "sensitivity"),
            scope=Scope(
                type=_required_enum(ScopeType, record, "scope_type"),
                id=_optional_text(record, "scope_id"),
            ),
            effective_from=_parse_timestamp(
                record.get("effective_from"), field_name="effective_from"
            ),
            effective_to=_parse_timestamp(
                record.get("effective_to"), field_name="effective_to"
            ),
            source_type=_required_enum(SourceType, record, "source_type"),
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
                    source_type=_required_enum(SourceType, record, "source_type"),
                    source_ref=_required_text(record, "source_ref"),
                    artifact_id=_optional_text(record, "artifact_id"),
                    locator=locator,
                    source_text=_optional_text(record, "source_text"),
                    extraction_method=_optional_text(record, "extraction_method"),
                    captured_at=_required_timestamp(record, "captured_at"),
                    checksum=_optional_text(record, "checksum_sha256"),
                    confirmation_status=_required_enum(
                        EvidenceConfirmationStatus,
                        record,
                        "confirmation_status",
                    ),
                )
            )
        return tuple(result)
