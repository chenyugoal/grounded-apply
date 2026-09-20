"""Read-only posting exclusions and validated submission-history checks.

An explicit snapshot is useful while selecting a bounded source result. It is
not continuing authority: refresh after network/render work and before commits.
No candidate facts, answers, aliases, or inferred job closures enter decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.discovery import (
    DiscoveredJob, Provider, canonical_job_url, validate_discovered_job,
)
from grounded_apply.services.jobs import JobService, JobSnapshot, validate_job_input
from grounded_apply.services.materials import MaterialService


MAX_EXCLUDED_IDENTITIES = 500
MAX_POLICY_APPLICATIONS = 1000
MAX_POLICY_APPLICATION_EVENTS = 10000
EligibilityReason = Literal["explicitly_excluded", "already_applied"]


class SearchPolicyLimitError(RepositoryError):
    """History cannot be partially scanned and reported as eligible."""


@dataclass(frozen=True, slots=True)
class PostingIdentity:
    kind: Literal["discovery", "url"]
    provider: str | None = None
    board: str | None = None
    external_id: str | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "discovery":
            if self.source_url is not None:
                raise ValueError("Discovery identity cannot contain an alternate URL")
            canonical_job_url(cast(Provider, self.provider),
                              cast(str, self.board), cast(str, self.external_id))
        elif self.kind == "url":
            if any(value is not None for value in (self.provider, self.board, self.external_id)):
                raise ValueError("URL identity cannot contain discovery fields")
            validate_job_input(cast(str, self.source_url), "Posting identity validation")
        else:
            raise ValueError("Unsupported posting identity")

    def to_dict(self) -> dict[str, str]:
        if self.kind == "url":
            return {"kind": "url", "source_url": cast(str, self.source_url)}
        return {"kind": "discovery", "provider": cast(str, self.provider),
                "board": cast(str, self.board), "external_id": cast(str, self.external_id)}

    def exact_url(self) -> str:
        if self.kind == "url":
            return cast(str, self.source_url)
        return canonical_job_url(cast(Provider, self.provider),
                                 cast(str, self.board), cast(str, self.external_id))


def validate_excluded_identities(value: object) -> tuple[PostingIdentity, ...]:
    """Parse closed, exact exclusions; URL identity uses manual capture rules."""
    if type(value) is not list or len(value) > MAX_EXCLUDED_IDENTITIES:
        raise ValueError("Exclusions require a list of at most five hundred identities")
    identities: list[PostingIdentity] = []
    for item in value:
        if type(item) is not dict:
            raise ValueError("Excluded identity must be an object")
        fields = ({"kind", "source_url"} if item.get("kind") == "url"
                  else {"kind", "provider", "board", "external_id"})
        if set(item) != fields or any(type(part) is not str for part in item.values()):
            raise ValueError("Excluded identity fields are invalid")
        identities.append(PostingIdentity(**item))
    if len(set(identities)) != len(identities):
        raise ValueError("Excluded identities must be distinct")
    return tuple(identities)


def _discovered_identity(job: DiscoveredJob) -> PostingIdentity:
    validate_discovered_job(job)
    return PostingIdentity("discovery", job.provider, job.board, job.external_id)


def posting_identity(job: JobSnapshot) -> PostingIdentity:
    """Return a stable version-independent identity without URL normalization."""
    if type(job) is not JobSnapshot:
        raise ValueError("Posting identity requires a saved job snapshot")
    validate_job_input(job.source_url, job.source_text)
    if job.discovery is None:
        return PostingIdentity("url", source_url=job.source_url)
    if type(job.discovery) is not dict or set(job.discovery) != {
        "provider", "board", "external_id", "title", "location",
        "content_sha256", "normalizer_version",
    }:
        raise ValueError("Saved discovery identity is invalid")
    return _discovered_identity(DiscoveredJob(**job.discovery,
        source_url=job.source_url, source_text=job.source_text))


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    identity: PostingIdentity
    source_url: str
    eligible: bool
    reason: EligibilityReason | None = None


@dataclass(frozen=True, slots=True)
class EligibilitySnapshot:
    """A private point-in-time index; callers must refresh before later work."""

    excluded_identities: frozenset[PostingIdentity]
    excluded_urls: frozenset[str]
    applied_identities: frozenset[PostingIdentity]
    applied_urls: frozenset[str]

    def _assess(self, identity: PostingIdentity, source_url: str) -> EligibilityDecision:
        reason: EligibilityReason | None = None
        if identity in self.excluded_identities or source_url in self.excluded_urls:
            reason = "explicitly_excluded"
        elif identity in self.applied_identities or source_url in self.applied_urls:
            reason = "already_applied"
        return EligibilityDecision(identity, source_url, reason is None, reason)

    def assess(self, job: JobSnapshot) -> EligibilityDecision:
        return self._assess(posting_identity(job), job.source_url)

    def assess_discovered(self, job: DiscoveredJob) -> EligibilityDecision:
        return self._assess(_discovered_identity(job), job.source_url)


class SearchEligibilityPolicy:
    def __init__(self, repository: SQLiteRepository, materials: MaterialService, *,
                 excluded_identities: tuple[PostingIdentity, ...] = ()) -> None:
        if (type(excluded_identities) is not tuple
            or any(type(identity) is not PostingIdentity for identity in excluded_identities)):
            raise ValueError("Policy exclusions require validated posting identities")
        self._excluded = validate_excluded_identities([identity.to_dict() for identity in excluded_identities])
        self._repository = repository
        self._materials = materials

    def snapshot(self) -> EligibilitySnapshot:
        """Validate every history, including historical approval and submission.

        An ever-applied posting stays excluded after rejection or withdrawal.
        Retired evidence cannot invalidate the fact of a past submission; the
        application service distinguishes historical validation from readiness.
        Integrity/dependency errors propagate, never becoming an eligible result.
        """
        with self._repository.read_transaction():
            counts = self._repository.support_counts()
            if (counts["applications"] > MAX_POLICY_APPLICATIONS
                or counts["application_events"] > MAX_POLICY_APPLICATION_EVENTS):
                raise SearchPolicyLimitError("Application history exceeds search policy review limits")
            applications = ApplicationService(self._repository, self._materials)
            jobs = JobService(self._repository)
            applied: set[PostingIdentity] = set()
            applied_urls: set[str] = set()
            for record in self._repository.list_applications():
                history = applications.get(record["id"])
                if any(event["state"] == "applied" for event in history["events"]):
                    job = jobs.get(history["job_id"])
                    applied.add(posting_identity(job))
                    applied_urls.add(job.source_url)
            return EligibilitySnapshot(frozenset(self._excluded),
                frozenset(identity.exact_url() for identity in self._excluded),
                frozenset(applied), frozenset(applied_urls))

    def assess(self, job_id: str) -> EligibilityDecision:
        """Refresh history and the saved job in the same consistent read view."""
        with self._repository.read_transaction():
            snapshot = self.snapshot()
            return snapshot.assess(JobService(self._repository).get(job_id))

    def assess_discovered(self, job: DiscoveredJob) -> EligibilityDecision:
        """Assess a validated source version before capture using fresh history."""
        _discovered_identity(job)
        return self.snapshot().assess_discovered(job)
