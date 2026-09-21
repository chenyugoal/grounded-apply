"""Historical answer facts and closed blocker shapes, without new resolution.

The caller owns original-profile provenance and historical packet authority.
Unanswered results retain their recorded causes: checking their shape does not
reconstruct an earlier approval state or authorize filling the answer now.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from grounded_apply.domain import (
    ApprovalStatus, Claim, ClaimPacket, ClaimStatus, Contradiction, Derivation,
    NeedInfo, NeedInfoAction, NeedInfoReason, ReusePolicy, Scope, ScopeType,
    Sensitivity, SourceType, to_jsonable,
)
from grounded_apply.services.questionnaires import (
    _legacy_career_question_is_eligible, is_eligible_career_question, validate_question_specs,
)
from grounded_apply.services.workflow import opaque

_ERROR = "Historical questionnaire failed factual custody checks"
_ANSWER_FIELDS = frozenset({"question_id", "question", "required", "status", "answer",
    "factual_units", "need_info", "human_review_required", "external_action_taken"})
_CLAIM_FIELDS = frozenset({"id", "claim_type", "value_json", "canonical_text", "subject_type",
    "subject_id", "status", "approval_status", "confidence", "sensitivity", "scope", "effective_from",
    "effective_to", "source_type", "source_ref", "evidence_ids", "verified_at", "verified_by",
    "derivation", "supersedes_id", "created_at", "updated_at"})
_SHORT_BLOCKERS = {
    "human_answer_required": "Answer this question yourself. No sensitive answer is inferred or stored.",
    "missing_evidence": "Select approved career facts that answer this question.",
    "out_of_scope": "Select career evidence for this career question.",
}
_Enum = TypeVar("_Enum", bound=StrEnum)


def _object(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value) or set(value) != fields:
        raise ValueError(_ERROR)
    return value


def _text(value: object, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str:
        raise ValueError(_ERROR)
    return value


def _enum(kind: type[_Enum], value: object) -> _Enum:
    if type(value) is not str:
        raise ValueError(_ERROR)
    return kind(value)


def _ids(value: object, *, duplicates: bool = False) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValueError(_ERROR)
    result = tuple(opaque(item) for item in value)
    if not duplicates and len(set(result)) != len(result):
        raise ValueError(_ERROR)
    return result


def _time(value: object, *, optional: bool = False) -> datetime | None:
    if optional and value is None:
        return None
    if type(value) is not str:
        raise ValueError(_ERROR)
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(_ERROR)
    return result


def _scope(value: object) -> Scope:
    item = _object(value, frozenset({"type", "id"}))
    return Scope(type=_enum(ScopeType, item["type"]), id=_text(item["id"], optional=True))


def _json(value: object) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json(item)
        return
    raise ValueError(_ERROR)


def _claim(value: object) -> Claim:
    item = _object(value, _CLAIM_FIELDS)
    _json(item["value_json"])
    if type(item["confidence"]) not in {float, int}:
        raise ValueError(_ERROR)
    derivation = None
    if item["derivation"] is not None:
        entry = _object(item["derivation"], frozenset({"rule_name", "rule_version",
            "input_claim_ids", "staleness_policy", "calculated_at"}))
        derivation = Derivation(rule_name=_text(entry["rule_name"]), rule_version=_text(entry["rule_version"]),
            input_claim_ids=_ids(entry["input_claim_ids"]), staleness_policy=_text(entry["staleness_policy"]),
            calculated_at=_time(entry["calculated_at"]))
    return Claim(id=opaque(item["id"]), claim_type=_text(item["claim_type"]),
        value_json=item["value_json"], canonical_text=_text(item["canonical_text"]),
        subject_type=_text(item["subject_type"]), subject_id=_text(item["subject_id"], optional=True),
        status=_enum(ClaimStatus, item["status"]), approval_status=_enum(ApprovalStatus, item["approval_status"]),
        confidence=item["confidence"], sensitivity=_enum(Sensitivity, item["sensitivity"]), scope=_scope(item["scope"]),
        effective_from=_time(item["effective_from"], optional=True), effective_to=_time(item["effective_to"], optional=True),
        source_type=_enum(SourceType, item["source_type"]), source_ref=_text(item["source_ref"], optional=True),
        evidence_ids=_ids(item["evidence_ids"]), verified_at=_time(item["verified_at"], optional=True),
        verified_by=_text(item["verified_by"], optional=True), derivation=derivation,
        supersedes_id=_text(item["supersedes_id"], optional=True),
        created_at=_time(item["created_at"]), updated_at=_time(item["updated_at"]))


def _blocker(value: object) -> None:
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or type(value.get("kind")) is not str):
        raise ValueError(_ERROR)
    if set(value) == {"kind", "reason", "question"}:
        if (value["kind"] != "need_info" or type(value["reason"]) is not str
                or value["reason"] not in _SHORT_BLOCKERS or type(value["question"]) is not str
                or value["question"] != _SHORT_BLOCKERS.get(value["reason"])):
            raise ValueError(_ERROR)
        return
    if value.get("kind") == "need_info":
        item = _object(value, frozenset({"intent", "question", "reason", "sensitivity", "requested_scope",
            "reuse_policy", "allowed_actions", "related_claim_ids", "detail", "kind"}))
        if type(item["allowed_actions"]) is not list:
            raise ValueError(_ERROR)
        outcome = NeedInfo(intent=_text(item["intent"]), question=_text(item["question"]),
            reason=_enum(NeedInfoReason, item["reason"]), sensitivity=_enum(Sensitivity, item["sensitivity"]),
            requested_scope=_scope(item["requested_scope"]), reuse_policy=_enum(ReusePolicy, item["reuse_policy"]),
            allowed_actions=tuple(_enum(NeedInfoAction, action) for action in item["allowed_actions"]),
            related_claim_ids=_ids(item["related_claim_ids"]), detail=_text(item["detail"], optional=True))
    elif value.get("kind") == "contradiction":
        item = _object(value, frozenset({"intent", "question", "requested_scope", "conflicting_claims", "detail", "kind"}))
        if type(item["conflicting_claims"]) is not list:
            raise ValueError(_ERROR)
        outcome = Contradiction(intent=_text(item["intent"]), question=_text(item["question"]),
            requested_scope=_scope(item["requested_scope"]),
            conflicting_claims=tuple(_claim(claim) for claim in item["conflicting_claims"]),
            detail=_text(item["detail"], optional=True))
        if len({claim.id for claim in outcome.conflicting_claims}) != len(outcome.conflicting_claims):
            raise ValueError(_ERROR)
    else:
        raise ValueError(_ERROR)
    if to_jsonable(outcome) != value:
        raise ValueError(_ERROR)


def validate_historical_answers(
    specs: object, answers: object,
    resolve_packet: Callable[[str, tuple[str, ...]], ClaimPacket],
) -> None:
    """Verify recorded emitted facts without selecting facts or filling blockers.

    The callback reconstructs only the supplied ordered packet membership from
    provenance-checked original claims at material creation time. Blocker causes
    are checked structurally; their earlier resolution decision is not replayed.
    """
    try:
        questions = validate_question_specs(specs)
        if (type(specs) not in {list, tuple} or any(type(spec) is not dict
                or any(type(key) is not str for key in spec) for spec in specs)
                or type(answers) not in {list, tuple} or len(answers) != len(questions) or not callable(resolve_packet)):
            raise ValueError(_ERROR)
        for question, answer in zip(questions, answers, strict=True):
            item = _object(answer, _ANSWER_FIELDS)
            if (type(item["question_id"]) is not str or type(item["question"]) is not str
                    or type(item["status"]) is not str
                    or item["question_id"] != question["id"] or item["question"] != question["text"]
                    or type(item["required"]) is not bool or item["required"] != question["required"]
                    or item["human_review_required"] is not True or item["external_action_taken"] is not False
                    or type(item["factual_units"]) is not list or type(item["need_info"]) is not list):
                raise ValueError(_ERROR)
            if item["status"] == "need_info":
                if item["answer"] is not None or item["factual_units"] or not item["need_info"]:
                    raise ValueError(_ERROR)
                for blocker in item["need_info"]:
                    _blocker(blocker)
                eligible = is_eligible_career_question(question["text"])
                known_reasons = (("human_answer_required",) if not eligible
                                 else ("missing_evidence",) if not question["claim_ids"] else ())
                if not eligible and _legacy_career_question_is_eligible(question["text"]):
                    # A formerly eligible signing question can retain its old
                    # unanswered blocker. This compatibility never admits a
                    # draft, emits facts, or replays the current resolver.
                    known_reasons = (() if question["claim_ids"]
                                     else ("missing_evidence", "human_answer_required"))
                if known_reasons and all(item["need_info"] != [{"kind": "need_info",
                        "reason": reason, "question": _SHORT_BLOCKERS[reason]}] for reason in known_reasons):
                    raise ValueError(_ERROR)
                continue
            if (item["status"] != "draft" or not is_eligible_career_question(question["text"])
                    or not question["claim_ids"] or item["need_info"]
                    or len(item["factual_units"]) != len(question["claim_ids"])):
                raise ValueError(_ERROR)
            parts = []
            for selected_id, value in zip(question["claim_ids"], item["factual_units"], strict=True):
                unit = _object(value, frozenset({"text", "claim_ids", "evidence_ids"}))
                claim_ids = _ids(unit["claim_ids"])
                evidence_ids = _ids(unit["evidence_ids"], duplicates=True)
                packet = resolve_packet(selected_id, claim_ids)
                if (type(packet) is not ClaimPacket or packet.claim_ids != claim_ids or selected_id not in claim_ids
                        or tuple(evidence.id for evidence in packet.evidence) != evidence_ids):
                    raise ValueError(_ERROR)
                selected = next(claim for claim in packet.claims if claim.id == selected_id)
                if (selected.claim_type == "candidate_name" or selected.claim_type.startswith("contact_")
                        or type(unit["text"]) is not str or unit["text"] != selected.canonical_text):
                    raise ValueError(_ERROR)
                parts.append(selected.canonical_text)
            if type(item["answer"]) is not str or item["answer"] != "\n".join(parts):
                raise ValueError(_ERROR)
    except Exception:
        raise ValueError(_ERROR) from None
