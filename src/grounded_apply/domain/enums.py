"""Enumerations used by the Grounded Apply domain model.

All enums inherit from :class:`enum.StrEnum` so their values can be emitted to
JSON without maintaining a second mapping layer.
"""

from __future__ import annotations

from enum import StrEnum


class ClaimStatus(StrEnum):
    """Lifecycle and trust state of an atomic candidate claim."""

    VERIFIED = "verified"
    DERIVED = "derived"
    NEEDS_REVIEW = "needs_review"
    CONTRADICTED = "contradicted"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class ApprovalStatus(StrEnum):
    """Whether a user has authorized a record for use."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Sensitivity(StrEnum):
    """Sensitivity classification for claims and answers."""

    PUBLIC = "public"
    PERSONAL = "personal"
    CONFIDENTIAL = "confidential"
    HIGHLY_SENSITIVE = "highly_sensitive"


class ScopeType(StrEnum):
    """Contexts in which a claim or answer may be reused."""

    GLOBAL = "global"
    ROLE_FAMILY = "role_family"
    GEOGRAPHY = "geography"
    COMPANY = "company"
    JOB = "job"
    APPLICATION = "application"
    ONE_TIME = "one_time"


class SourceType(StrEnum):
    """Origin of a claim or evidence record."""

    USER_STATEMENT = "user_statement"
    IMPORTED_RESUME = "imported_resume"
    TRANSCRIPT = "transcript"
    PORTFOLIO = "portfolio"
    DERIVATION = "derivation"
    APPLICATION_ANSWER = "application_answer"
    OTHER = "other"


class EvidenceConfirmationStatus(StrEnum):
    """User-confirmation state of an evidence item."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ReusePolicy(StrEnum):
    """How an approved answer may be reused."""

    REUSE = "reuse"
    CONFIRM = "confirm"
    ASK_EVERY_TIME = "ask_every_time"
    NEVER_STORE = "never_store"


class RetentionPolicy(StrEnum):
    """How long proposed memory may be retained."""

    NEVER = "never"
    SESSION = "session"
    UNTIL_EXPIRY = "until_expiry"
    DURABLE = "durable"


class MemoryProposalStatus(StrEnum):
    """Review state of a proposed memory update."""

    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


class NeedInfoReason(StrEnum):
    """Why resolution stopped instead of returning a value."""

    MISSING = "missing"
    STALE = "stale"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    SENSITIVE = "sensitive"
    UNAPPROVED = "unapproved"
    OUT_OF_SCOPE = "out_of_scope"
    UNSUPPORTED = "unsupported"


class NeedInfoAction(StrEnum):
    """Actions a caller may offer in response to a NeedInfo result."""

    ANSWER_ONCE = "answer_once"
    ANSWER_AND_REMEMBER = "answer_and_remember"
    CONFIRM = "confirm"
    SKIP = "skip"


class ClaimRejectionReason(StrEnum):
    """Reason a claim was excluded by the deterministic use policy."""

    TYPE_MISMATCH = "type_mismatch"
    OUT_OF_SCOPE = "out_of_scope"
    INELIGIBLE_STATUS = "ineligible_status"
    UNAPPROVED = "unapproved"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    STALE = "stale"
    SENSITIVE = "sensitive"
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_DERIVATION = "invalid_derivation"


class ResolutionKind(StrEnum):
    """Discriminator for the claim-resolution outcome union."""

    RESOLVED = "resolved"
    NEED_INFO = "need_info"
    CONTRADICTION = "contradiction"
