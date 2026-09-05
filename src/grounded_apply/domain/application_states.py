"""Deterministic manual application transitions."""

from enum import StrEnum


class ApplicationState(StrEnum):
    DISCOVERED = "discovered"
    SHORTLISTED = "shortlisted"
    PREPARING = "preparing"
    READY_FOR_REVIEW = "ready_for_review"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    RECRUITER_SCREEN = "recruiter_screen"
    INTERVIEW = "interview"
    OFFER = "offer"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


_NEXT = {
    "discovered": {"shortlisted", "archived"},
    "shortlisted": {"preparing", "archived"},
    "preparing": {"ready_for_review", "archived"},
    "ready_for_review": {"applied", "preparing", "archived"},
    "applied": {"assessment", "recruiter_screen", "interview", "rejected", "withdrawn"},
    "assessment": {"interview", "rejected", "withdrawn"},
    "recruiter_screen": {"assessment", "interview", "rejected", "withdrawn"},
    "interview": {"offer", "rejected", "withdrawn"},
    "offer": {"accepted", "declined"},
}


def require_transition(current: ApplicationState, target: ApplicationState) -> None:
    if type(current) is not ApplicationState or type(target) is not ApplicationState or target.value not in _NEXT.get(current.value, set()):
        raise ValueError("Invalid application state transition")
