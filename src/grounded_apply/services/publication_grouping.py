"""Explicitly join selected publication fragments without reading or storing data.

The source is re-extracted with the caller's displayed version. Only whitespace
between consecutive publication proposals may be combined; every other proposal
and every original inventory row remains visible for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from hashlib import sha256

from grounded_apply.domain import JsonValue
from grounded_apply.services.profile import (
    CreateImportProposal, ProfileService, ProposedImportClaim, TextSourceSpan,
)
from grounded_apply.services.profile_import_validation import (
    PROFILE_IMPORT_COMPLETE_FACTS_POLICY_VERSION, PROFILE_IMPORT_MAX_SOURCE_BYTES,
)
from grounded_apply.services.resume_extraction import InventoryLine, extract_resume


class PublicationGroupingError(ValueError):
    """A fixed, content-free refusal safe to display at the CLI boundary."""


@dataclass(frozen=True, slots=True)
class PublicationManifestProposal:
    """Only the fields accepted by the existing closed import manifest."""

    claim_type: str
    value: str
    canonical_text: str
    span: TextSourceSpan
    confidence: float


@dataclass(frozen=True, slots=True)
class PublicationImportManifest:
    source_sha256: str
    proposals: tuple[PublicationManifestProposal, ...]
    schema_version: int = field(default=2, init=False)
    span_index_base: int = field(default=0, init=False)
    span_unit: str = field(default="unicode_codepoint", init=False)
    span_end: str = field(default="exclusive", init=False)


@dataclass(frozen=True, slots=True)
class PublicationProposalIndexMap:
    original_index: int
    manifest_index: int


def _project_manifest(manifest: PublicationImportManifest) -> dict[str, JsonValue]:
    proposals: list[JsonValue] = []
    for proposal in manifest.proposals:
        proposals.append({
            "claim_type": proposal.claim_type,
            "value": proposal.value,
            "canonical_text": proposal.canonical_text,
            "span": {"start": proposal.span.start, "end": proposal.span.end,
                     "text": proposal.span.text},
            "confidence": proposal.confidence,
        })
    return {
        "schema_version": manifest.schema_version,
        "source_sha256": manifest.source_sha256,
        "span_index_base": manifest.span_index_base,
        "span_unit": manifest.span_unit,
        "span_end": manifest.span_end,
        "proposals": proposals,
    }


@dataclass(frozen=True, slots=True)
class PublicationGroupResult:
    source_sha256: str
    extractor: str
    extractor_version: int
    grouped_indexes: tuple[int, ...]
    grouped_manifest_index: int
    original_proposal_count: int
    manifest_proposal_count: int
    index_mapping: tuple[PublicationProposalIndexMap, ...]
    inventory: tuple[InventoryLine, ...]
    skipped_lines: int
    unclassified_count: int
    blocked_count: int
    manifest: PublicationImportManifest
    schema_version: int = field(default=1, init=False)
    read_only: bool = field(default=True, init=False)
    content_trust: str = field(default="untrusted", init=False)
    review_required: bool = field(default=True, init=False)
    storage_changed: bool = field(default=False, init=False)
    profile_read: bool = field(default=False, init=False)
    network_requests: int = field(default=0, init=False)

    def to_manifest(self) -> dict[str, JsonValue]:
        """Project a fresh closed manifest, excluding report and domain metadata."""

        return _project_manifest(self.manifest)


@dataclass(frozen=True, slots=True)
class PublicationGroupSelection:
    indexes: tuple[int, ...]
    manifest_index: int


@dataclass(frozen=True, slots=True)
class PublicationGroupsResult:
    source_sha256: str
    extractor: str
    extractor_version: int
    groups: tuple[PublicationGroupSelection, ...]
    original_proposal_count: int
    manifest_proposal_count: int
    index_mapping: tuple[PublicationProposalIndexMap, ...]
    inventory: tuple[InventoryLine, ...]
    skipped_lines: int
    unclassified_count: int
    blocked_count: int
    manifest: PublicationImportManifest
    schema_version: int = field(default=2, init=False)
    read_only: bool = field(default=True, init=False)
    content_trust: str = field(default="untrusted", init=False)
    review_required: bool = field(default=True, init=False)
    storage_changed: bool = field(default=False, init=False)
    profile_read: bool = field(default=False, init=False)
    network_requests: int = field(default=0, init=False)

    def to_manifest(self) -> dict[str, JsonValue]:
        """Project the same closed import schema used by singular grouping."""

        return _project_manifest(self.manifest)


def validate_publication_group_request(
    *, expected_source_sha256: str, extractor_version: int, indexes: tuple[int, ...],
) -> None:
    """Validate public arguments before a caller opens the source document."""

    if (type(expected_source_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256) is None):
        raise PublicationGroupingError("Publication grouping requires a lowercase SHA-256 source digest")
    if type(extractor_version) is not int or extractor_version not in {1, 2, 3, 4}:
        raise PublicationGroupingError("Publication grouping requires an explicit supported extractor version")
    if (type(indexes) is not tuple or not 2 <= len(indexes) <= 1000
            or any(type(index) is not int or not 0 <= index < 1000 for index in indexes)
            or any(right != left + 1 for left, right in zip(indexes, indexes[1:]))):
        raise PublicationGroupingError("Select at least two ascending consecutive displayed publication indexes")


def build_publication_group(
    source_text: str, *, expected_source_sha256: str, extractor_version: int,
    indexes: tuple[int, ...],
) -> PublicationGroupResult:
    """Return an importable review manifest for one explicitly selected group.

    This proves only exact-span, whitespace-only combination, not that selected
    fragments describe the same publication. No claim is approved or persisted.
    The complete resulting batch must satisfy current complete-fact import rules.
    """

    validate_publication_group_request(expected_source_sha256=expected_source_sha256,
                                       extractor_version=extractor_version, indexes=indexes)
    result = _build_validated_publication_groups(source_text,
        expected_source_sha256=expected_source_sha256, extractor_version=extractor_version,
        groups=(indexes,))
    return PublicationGroupResult(
        source_sha256=result.source_sha256, extractor=result.extractor,
        extractor_version=result.extractor_version, grouped_indexes=indexes,
        grouped_manifest_index=result.groups[0].manifest_index,
        original_proposal_count=result.original_proposal_count,
        manifest_proposal_count=result.manifest_proposal_count, index_mapping=result.index_mapping,
        inventory=result.inventory, skipped_lines=result.skipped_lines,
        unclassified_count=result.unclassified_count, blocked_count=result.blocked_count,
        manifest=result.manifest,
    )


def validate_publication_groups_request(
    *, expected_source_sha256: str, extractor_version: int, groups: tuple[tuple[int, ...], ...],
) -> None:
    """Validate disjoint explicit selections before any source is accessed."""

    if type(groups) is not tuple or not 1 <= len(groups) <= 500:
        raise PublicationGroupingError("Select from one to 500 explicit publication groups")
    members: set[int] = set()
    total = 0
    for indexes in groups:
        validate_publication_group_request(expected_source_sha256=expected_source_sha256,
                                           extractor_version=extractor_version, indexes=indexes)
        total += len(indexes)
        if total > 1000:
            raise PublicationGroupingError("Publication groups must select at most 1000 original proposals")
        if members.intersection(indexes):
            raise PublicationGroupingError("Publication groups must not overlap or repeat selected indexes")
        members.update(indexes)


def build_publication_groups(
    source_text: str, *, expected_source_sha256: str, extractor_version: int,
    groups: tuple[tuple[int, ...], ...],
) -> PublicationGroupsResult:
    """Combine disjoint explicit groups into one complete review manifest.

    Group reports preserve request order; the manifest and index map preserve
    source order. Adjacent selections remain separate. All groups and untouched
    facts pass one final import preview before any result is returned.
    """

    validate_publication_groups_request(expected_source_sha256=expected_source_sha256,
                                        extractor_version=extractor_version, groups=groups)
    return _build_validated_publication_groups(source_text,
        expected_source_sha256=expected_source_sha256, extractor_version=extractor_version,
        groups=groups)


def _build_validated_publication_groups(
    source_text: str, *, expected_source_sha256: str, extractor_version: int,
    groups: tuple[tuple[int, ...], ...],
) -> PublicationGroupsResult:
    if type(source_text) is not str or not source_text.strip():
        raise PublicationGroupingError("Publication grouping requires bounded nonblank UTF-8 source text")
    try:
        encoded_source = source_text.encode("utf-8")
    except UnicodeError:
        raise PublicationGroupingError("Publication grouping requires bounded nonblank UTF-8 source text") from None
    if len(encoded_source) > PROFILE_IMPORT_MAX_SOURCE_BYTES:
        raise PublicationGroupingError("Publication grouping requires bounded nonblank UTF-8 source text")
    if sha256(encoded_source).hexdigest() != expected_source_sha256:
        raise PublicationGroupingError("Publication grouping source changed; extract and select again")
    try:
        extraction = extract_resume(source_text, version=extractor_version)
    except (TypeError, ValueError):
        raise PublicationGroupingError("Publication source could not be safely extracted; review the source") from None
    combined: dict[int, ProposedImportClaim] = {}
    member_anchors: dict[int, int] = {}
    for indexes in groups:
        if indexes[-1] >= len(extraction.proposals):
            raise PublicationGroupingError("Select displayed publication indexes from the unchanged extraction")
        selected = tuple(extraction.proposals[index] for index in indexes)
        if any(proposal.claim_type != "publication" for proposal in selected):
            raise PublicationGroupingError("Publication grouping accepts only publication proposals")
        for left, right in zip(selected, selected[1:]):
            if (left.span.end > right.span.start
                    or source_text[left.span.end:right.span.start].strip()):
                raise PublicationGroupingError("Publication fragments must be separated only by whitespace")
        first, last = selected[0], selected[-1]
        exact_text = source_text[first.span.start:last.span.end]
        canonical_text = " ".join(exact_text.split())
        combined[indexes[0]] = replace(first, value=canonical_text, canonical_text=canonical_text,
            span=TextSourceSpan(start=first.span.start, end=last.span.end, text=exact_text))
        member_anchors.update((index, indexes[0]) for index in indexes)
    proposed: list[ProposedImportClaim] = []
    mapping: list[PublicationProposalIndexMap] = []
    destinations: dict[int, int] = {}
    for index, proposal in enumerate(extraction.proposals):
        if index in combined:
            destinations[index] = len(proposed)
            proposed.append(combined[index])
        elif index not in member_anchors:
            proposed.append(proposal)
        destination = (destinations[member_anchors[index]] if index in member_anchors
                       else len(proposed) - 1)
        mapping.append(PublicationProposalIndexMap(index, destination))
    proposals = tuple(proposed)
    try:
        request = CreateImportProposal(
            idempotency_key="publication-group-preview", source_text=source_text,
            expected_source_sha256=expected_source_sha256, proposals=proposals,
            content_policy_version=PROFILE_IMPORT_COMPLETE_FACTS_POLICY_VERSION,
        )
        ProfileService.preview_import_proposal(request)
    except (TypeError, ValueError):
        raise PublicationGroupingError("Grouped manifest fails import content or size limits; review the selection") from None
    # Exact-line extraction produces scalar text, never mutable structured values.
    if any(type(proposal.value) is not str for proposal in proposals):
        raise PublicationGroupingError("Publication extraction contains unsupported proposal values")
    manifest = PublicationImportManifest(source_sha256=expected_source_sha256, proposals=tuple(
        PublicationManifestProposal(proposal.claim_type, proposal.value, proposal.canonical_text,
                                    proposal.span, proposal.confidence)
        for proposal in proposals
    ))
    return PublicationGroupsResult(
        source_sha256=expected_source_sha256, extractor=extraction.extractor,
        extractor_version=extraction.extractor_version,
        groups=tuple(PublicationGroupSelection(indexes, destinations[indexes[0]]) for indexes in groups),
        original_proposal_count=len(extraction.proposals),
        manifest_proposal_count=len(proposals), index_mapping=tuple(mapping),
        inventory=extraction.inventory, skipped_lines=extraction.skipped_lines,
        unclassified_count=sum(row.status == "unclassified" for row in extraction.inventory),
        blocked_count=sum(row.status == "blocked" for row in extraction.inventory), manifest=manifest,
    )
