"""Minimal, read-only job-search briefing over validated application services."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from grounded_apply.domain import ApprovalStatus, ClaimStatus
from grounded_apply.domain.application_states import ApplicationState
from grounded_apply.domain.search_actions import next_action
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.applications import ApplicationService
from grounded_apply.services.jobs import JobService
from grounded_apply.services.matching import MatchingService
from grounded_apply.services.materials import MaterialService
from grounded_apply.services.profile import ProfileService


class BriefingService:
    def __init__(self, repository: SQLiteRepository, materials: MaterialService) -> None:
        self._repository = repository
        self._materials = materials

    def brief(self, *, job_id: str | None = None, follow_up_days: int = 7,
              now: datetime | None = None) -> dict[str, Any]:
        if type(follow_up_days) is not int or not 1 <= follow_up_days <= 90:
            raise ValueError("Response-check interval must be from 1 to 90 days")
        at = now if now is not None else datetime.now(UTC)
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Briefing time must include a timezone")
        at = at.astimezone(UTC)
        with self._repository.read_transaction():
            profile = ProfileService(self._repository)
            claims, _ = profile.validated_profile()
            pending = len(profile.list_review_items())
            approved = sum(c.status == ClaimStatus.VERIFIED and c.approval_status == ApprovalStatus.APPROVED for c in claims)
            jobs = JobService(self._repository)
            selected_jobs = (jobs.get(job_id),) if job_id is not None else jobs.list()
            applications = ApplicationService(self._repository, self._materials)
            # Filter IDs before loading history so a job-specific briefing does
            # not expose unrelated applications or material contents.
            grouped: dict[str, list[dict[str, Any]]] = {j.id: [] for j in selected_jobs}
            for record in self._repository.list_applications():
                if record["job_id"] in grouped:
                    grouped[record["job_id"]].append(applications.get(record["id"]))
            rows = []
            for job in selected_jobs:
                matrix = MatchingService(self._repository).assess(job.id)
                summaries = self._materials.list(job.id)
                latest = max(summaries, key=lambda m: (m["created_at"], m["material_id"]), default=None)
                required_unanswered = 0
                if latest is not None:
                    material = self._materials.get(latest["material_id"], require_current=False)
                    required_unanswered = sum(a["required"] and a["status"] != "draft" for a in material["manifest"]["answers"])
                for application in grouped[job.id] or [None]:
                    state = None if application is None else ApplicationState(application["state"])
                    updated = job.captured_at if application is None else application["events"][-1]["at"]
                    try:
                        updated_at = datetime.fromisoformat(updated)
                        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
                            raise ValueError
                    except (ValueError, TypeError):
                        raise RepositoryError("Briefing source timestamp is invalid") from None
                    elapsed = max(0, (at - updated_at).days)
                    action = next_action(state,
                        currently_ready=False if application is None else application["currently_ready"],
                        material_status="needs_review" if required_unanswered else None if latest is None else latest["status"],
                        elapsed_days=elapsed, follow_up_days=follow_up_days)
                    response_check = updated_at + timedelta(days=follow_up_days) if state == ApplicationState.APPLIED else None
                    rows.append({
                        "job_id": job.id, "source_url": job.source_url,
                        "captured_at": job.captured_at, "live_page_verified": False,
                        "application_id": None if application is None else application["application_id"],
                        "application_material_id": None if application is None else next(
                            (e["payload"]["material_id"] for e in reversed(application["events"])
                             if e["state"] in {"ready_for_review", "applied"}), None),
                        "state": "saved" if state is None else state.value,
                        "updated_at": updated, "days_since_update": elapsed,
                        "response_check_at": None if response_check is None else response_check.astimezone(UTC).isoformat(),
                        "currently_ready": False if application is None else application["currently_ready"],
                        "latest_material": latest,
                        "required_unanswered_count": required_unanswered,
                        "requirement_count": len(matrix["rows"]),
                        "requirements_with_retrieved_evidence": sum(bool(r["candidate_evidence"]) for r in matrix["rows"]),
                        "requirements_without_retrieved_evidence": sum(not r["candidate_evidence"] for r in matrix["rows"]),
                        "requirements_missing": not bool(matrix["rows"]),
                        "suspicious_job_lines": job.suspicious_lines,
                        "next_action": asdict(action),
                    })
            rows.sort(key=lambda r: (r["next_action"]["priority"], r["updated_at"], r["job_id"], r["application_id"] or ""))
            return {
                "schema_version": 1, "policy": "grounded-apply.search-actions@1", "as_of": at.isoformat(),
                "read_only": True, "external_action_taken": False, "content_trust": "untrusted",
                "profile": {"approved_claim_count": approved, "pending_review_count": pending,
                    "next_action": "review_pending_facts" if pending else "onboard_profile" if not approved else "select_job_evidence"},
                "job_count": len(selected_jobs), "application_count": sum(len(v) for v in grouped.values()),
                "follow_up_days": follow_up_days, "items": rows,
                "limitations": [
                    "Action order reflects recorded workflow stage, not fit, hiring probability, deadlines, or job quality.",
                    "Retrieved evidence counts are keyword retrieval only; they do not prove that a requirement is met.",
                    "Response checks are on-demand suggestions, not scheduled reminders or messages.",
                    "Job pages and employer responses are not fetched. Review saved text and verify the live opening yourself.",
                    "Approved claim counts do not establish current scope, freshness, or permission for a particular use.",
                ],
            }
