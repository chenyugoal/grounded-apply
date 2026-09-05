"""Evidence retrieval with explicit uncertainty; no private hiring score."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from grounded_apply.domain import ClaimUsePolicy, Resolved, Scope, ScopeType, Sensitivity, to_jsonable
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.profile import ProfileService

_STOP = frozenset("a an and are as at be by candidate candidates experience for from have in is of on or our required requirements skills the to with work years year you your preferred ability will we must strong knowledge demonstrated using use".split())


def terms(text: str) -> frozenset[str]:
    return frozenset(t.casefold() for t in re.findall(r"[\w][\w+#.-]*", text) if len(t) > 1 and t.casefold() not in _STOP)


def job_policy(job_id: str, *, now: datetime | None = None) -> ClaimUsePolicy:
    return ClaimUsePolicy(as_of=now or datetime.now(UTC), requested_scope=Scope(type=ScopeType.JOB, id=job_id),
        allowed_sensitivities=frozenset({Sensitivity.PERSONAL, Sensitivity.PUBLIC}), require_confirmed_evidence=True)


class MatchingService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def assess(self, job_id: str) -> dict[str, Any]:
        with self._repository.read_transaction():
            job = JobService(self._repository).get(job_id)
            profile = ProfileService(self._repository)
            claims, _ = profile.validated_profile()
            policy = job_policy(job_id)
            eligible = []
            blocked = []
            for claim in claims:
                if claim.claim_type.startswith("contact_") or claim.claim_type == "candidate_name":
                    continue
                outcome = profile.packet_for_claim(claim.id, policy=policy)
                if isinstance(outcome, Resolved):
                    eligible.append(claim)
                else:
                    blocked.append({"claim_id": claim.id, "outcome": to_jsonable(outcome)})
            rows = []
            for requirement in job.requirements:
                query = terms(requirement.quote)
                evidence = []
                for claim in eligible:
                    overlap = sorted(query & terms(claim.canonical_text))
                    if overlap:
                        evidence.append({"claim_id": claim.id, "quote": claim.canonical_text,
                            "shared_terms": overlap, "evidence_ids": list(claim.evidence_ids),
                            "verified_at": to_jsonable(claim.verified_at), "requirement_met": None})
                rows.append({"requirement": to_jsonable(requirement), "candidate_evidence": evidence,
                    "status": "review_evidence" if evidence else "need_info",
                    "question": "Does this evidence satisfy the full requirement, including dates, scope, and ownership?"
                    if evidence else "What approved experience supports this requirement, or is it a gap?",
                    "certainty": "unresolved", "requirement_met": None})
            return {"job_id": job.id, "source_sha256": job.source_sha256, "rows": rows,
                "blocked_claims": blocked, "suspicious_job_lines": job.suspicious_lines,
                "hiring_probability": None, "overall_recommendation": "human_review_required",
                "method": "approved_evidence_token_retrieval@1",
                "limitation": "Shared terms retrieve evidence; they do not establish fit, duration, ownership, or an employer ranking.",
                "need_info": [] if rows else [{"kind": "need_info", "reason": "missing_requirements",
                    "question": "Supply the job's explicit requirements section before assessing fit."}]}
