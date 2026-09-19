"""Explainable workflow actions; no job-fit or hiring prediction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from grounded_apply.domain.application_states import ApplicationState


class SearchAction(StrEnum):
    REVIEW_OFFER = "review_offer"
    PREPARE_INTERVIEW = "prepare_interview"
    PREPARE_ASSESSMENT = "prepare_assessment"
    PREPARE_SCREEN = "prepare_screen"
    REPAIR_MATERIAL = "repair_material"
    REVIEW_MATERIAL = "review_material"
    MANUAL_SUBMISSION = "manual_submission"
    PREPARE_MATERIAL = "prepare_material"
    ASSESS_JOB = "assess_job"
    CHECK_RESPONSE = "check_response"
    WAIT_RESPONSE = "wait_response"
    NO_ACTION = "no_action"


@dataclass(frozen=True, slots=True)
class NextAction:
    kind: SearchAction
    priority: int
    reason: str


def next_action(
    state: ApplicationState | None,
    *,
    currently_ready: bool,
    material_status: str | None,
    elapsed_days: int,
    follow_up_days: int,
) -> NextAction:
    """Order work by recorded stage, never by presumed candidate suitability."""
    if state is not None and type(state) is not ApplicationState:
        raise ValueError("Unknown application state")
    if type(currently_ready) is not bool or material_status not in {None, "draft", "approved", "needs_review"}:
        raise ValueError("Invalid material readiness")
    if type(elapsed_days) is not int or elapsed_days < 0 or type(follow_up_days) is not int or not 1 <= follow_up_days <= 90:
        raise ValueError("Invalid response-check interval")
    active = {
        ApplicationState.OFFER: (SearchAction.REVIEW_OFFER, 10, "Review the recorded offer and your priorities."),
        ApplicationState.INTERVIEW: (SearchAction.PREPARE_INTERVIEW, 20, "Prepare evidence-backed research and engineering examples for the recorded interview stage."),
        ApplicationState.ASSESSMENT: (SearchAction.PREPARE_ASSESSMENT, 20, "Review the assessment instructions and any user-confirmed deadline."),
        ApplicationState.RECRUITER_SCREEN: (SearchAction.PREPARE_SCREEN, 20, "Prepare a concise career summary and questions for the recorded recruiter screen."),
    }
    if state in active:
        return NextAction(*active[state])
    if state == ApplicationState.APPLIED:
        if elapsed_days >= follow_up_days:
            return NextAction(SearchAction.CHECK_RESPONSE, 40, "Check for a response; the configured interval has elapsed since the recorded submission. No message will be sent.")
        return NextAction(SearchAction.WAIT_RESPONSE, 90, "Await a response or record a user-reported update; no employer response time is known.")
    if state in {ApplicationState.ACCEPTED, ApplicationState.DECLINED, ApplicationState.REJECTED,
                 ApplicationState.WITHDRAWN, ApplicationState.ARCHIVED}:
        return NextAction(SearchAction.NO_ACTION, 100, "The recorded application is closed; its history is preserved.")
    if state == ApplicationState.READY_FOR_REVIEW:
        if currently_ready:
            return NextAction(SearchAction.MANUAL_SUBMISSION, 30, "Review the live application and submit yourself, then explicitly confirm submission for tracking.")
        return NextAction(SearchAction.REPAIR_MATERIAL, 25, "The previously approved material is no longer ready. Return to preparing, resolve the evidence, and review a new version.")
    if state in {None, ApplicationState.DISCOVERED, ApplicationState.SHORTLISTED, ApplicationState.PREPARING}:
        if material_status == "needs_review":
            return NextAction(SearchAction.REPAIR_MATERIAL, 25, "Resolve missing or retired evidence and required answers before preparing a new version.")
        if material_status in {"draft", "approved"}:
            return NextAction(SearchAction.REVIEW_MATERIAL, 30, "Review the exact saved bundle and record its approval and ready-for-review stage before submission.")
        if state in {ApplicationState.SHORTLISTED, ApplicationState.PREPARING}:
            return NextAction(SearchAction.PREPARE_MATERIAL, 50, "Select approved career evidence and prepare a job-specific resume and answers.")
    return NextAction(SearchAction.ASSESS_JOB, 60, "Compare the quoted requirements with approved evidence and decide whether to shortlist this role.")
