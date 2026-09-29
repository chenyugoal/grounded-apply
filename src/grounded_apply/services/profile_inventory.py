"""Bounded, read-only views of retained facts, without assessing their usability."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from grounded_apply.domain import (
    ApprovalStatus, Claim, ClaimStatus, Scope, Sensitivity, SourceType,
)
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.profile import ProfileService


PROFILE_INVENTORY_TOPICS: tuple[str, ...] = (
    "contact", "education", "experience", "research", "projects", "skills",
    "publications", "achievements", "other",
)
_TOPIC_TYPES = MappingProxyType({
    "contact": ("candidate_name", "contact_email", "contact_phone", "contact_location", "contact_url"),
    "education": ("education", "education_degree", "education_field"),
    "experience": ("employment_dates", "employment_description", "employment_title"),
    "research": ("research_description",),
    "projects": ("portfolio_item", "project_contribution", "project_outcome"),
    "skills": ("skill_use", "language"),
    "publications": ("publication",),
    "achievements": ("achievement", "certification"),
})
_TYPE_TOPICS = MappingProxyType({
    claim_type: topic for topic, claim_types in _TOPIC_TYPES.items()
    for claim_type in claim_types
})
_ANCHOR_ERROR = (
    "Inventory anchor claim does not exist in the selected topic. "
    "Restart profile inventory without a continuation anchor."
)


def validate_profile_inventory_request(
    *, topic: str | None = None, limit: int | None = None,
    after_claim_id: str | None = None,
) -> None:
    """Validate request syntax without touching paths, storage or private data.

    Existing claim IDs are opaque text, including long or nonprinting IDs. Only
    blank, non-string or non-UTF-8 text is rejected before the exact lookup.
    """
    if topic is not None and (type(topic) is not str or topic not in PROFILE_INVENTORY_TOPICS):
        raise ValueError("Inventory topic must be a supported topic.")
    if topic is None and (limit is not None or after_claim_id is not None):
        raise ValueError("Inventory pagination requires a selected topic.")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 50):
        raise ValueError("Inventory page limit must be an integer from 1 through 50.")
    if after_claim_id is not None:
        if type(after_claim_id) is not str or not after_claim_id.strip():
            raise ValueError("Inventory anchor must be nonblank valid Unicode text.")
        try:
            after_claim_id.encode("utf-8")
        except UnicodeError:
            raise ValueError("Inventory anchor must be nonblank valid Unicode text.") from None


class ProfileInventoryIntegrityError(RepositoryError):
    """The complete retained profile could not be validated for this read."""


class _UnknownInventoryAnchor(ValueError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileInventoryStateCount:
    status: ClaimStatus
    approval_status: ApprovalStatus
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileInventoryTopic:
    topic: str
    claim_count: int
    states: tuple[ProfileInventoryStateCount, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileInventoryItem:
    id: str
    claim_type: str
    canonical_text: str
    status: ClaimStatus
    approval_status: ApprovalStatus
    source_type: SourceType
    scope: Scope
    sensitivity: Sensitivity


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileInventoryPage:
    limit: int
    total_count: int
    returned_count: int
    before_count: int
    after_count: int
    next_after: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileInventoryReport:
    total_claim_count: int
    topics: tuple[ProfileInventoryTopic, ...]
    selected_topic: str | None
    items: tuple[ProfileInventoryItem, ...]
    page: ProfileInventoryPage | None
    schema_version: Literal[1] = field(default=1, init=False)
    read_only: Literal[True] = field(default=True, init=False)
    content_trust: Literal["untrusted"] = field(default="untrusted", init=False)
    usability_assessed: Literal[False] = field(default=False, init=False)
    completeness_assessed: Literal[False] = field(default=False, init=False)
    interview_progress_assessed: Literal[False] = field(default=False, init=False)


def _inventory(
    claims: tuple[Claim, ...], *, topic: str | None,
    limit: int | None, after_claim_id: str | None,
) -> ProfileInventoryReport:
    grouped: dict[str, list[Claim]] = {name: [] for name in PROFILE_INVENTORY_TOPICS}
    for claim in claims:
        grouped[_TYPE_TOPICS.get(claim.claim_type, "other")].append(claim)
    topics: list[ProfileInventoryTopic] = []
    for name, members in grouped.items():
        counts = Counter((claim.status, claim.approval_status) for claim in members)
        states = tuple(
            ProfileInventoryStateCount(status=status, approval_status=approval, count=counts[status, approval])
            for status in ClaimStatus for approval in ApprovalStatus
            if counts[status, approval]
        )
        topics.append(ProfileInventoryTopic(topic=name, claim_count=len(members), states=states))
    items: tuple[ProfileInventoryItem, ...] = ()
    page = None
    if topic is not None:
        selected = grouped[topic]
        before = 0
        if after_claim_id is not None:
            position = next((index for index, claim in enumerate(selected)
                             if claim.id == after_claim_id), None)
            if position is None:
                raise _UnknownInventoryAnchor
            before = position + 1
        page_limit = 20 if limit is None else limit
        items = tuple(ProfileInventoryItem(
            id=claim.id, claim_type=claim.claim_type, canonical_text=claim.canonical_text,
            status=claim.status, approval_status=claim.approval_status,
            source_type=claim.source_type, scope=claim.scope, sensitivity=claim.sensitivity,
        ) for claim in selected[before:before + page_limit])
        after = len(selected) - before - len(items)
        page = ProfileInventoryPage(
            limit=page_limit, total_count=len(selected), returned_count=len(items),
            before_count=before, after_count=after,
            next_after=items[-1].id if after else None,
        )
    return ProfileInventoryReport(
        total_claim_count=len(claims), topics=tuple(topics), selected_topic=topic,
        items=items, page=page,
    )


class ProfileInventoryService:
    """Read all provenance once, then expose a minimal summary or one topic page."""

    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def read(
        self, *, topic: str | None = None, limit: int | None = None,
        after_claim_id: str | None = None,
    ) -> ProfileInventoryReport:
        validate_profile_inventory_request(topic=topic, limit=limit, after_claim_id=after_claim_id)
        try:
            with self._repository.read_transaction():
                claims, _ = ProfileService(self._repository).validated_profile()
                return _inventory(claims, topic=topic, limit=limit, after_claim_id=after_claim_id)
        except _UnknownInventoryAnchor:
            raise ValueError(_ANCHOR_ERROR) from None
        except MemoryError:
            raise
        except Exception:
            # Storage/parser messages can contain private IDs or field values.
            # Interrupts (BaseException) retain their identity and transaction rules.
            raise ProfileInventoryIntegrityError(
                "Retained profile inventory failed integrity validation."
            ) from None
