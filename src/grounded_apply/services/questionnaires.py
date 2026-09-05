"""Copy-paste career answers from approved packets; sensitive questions stop."""

from __future__ import annotations

import re
from typing import Any

from grounded_apply.domain import Resolved, to_jsonable
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.matching import job_policy
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.profile_import_validation import validate_profile_import_metadata
from grounded_apply.services.workflow import opaque

_HUMAN_GATE = re.compile(r"\b(authoriz|sponsor|visa|citizenship|nationality|clearance|criminal|convict|legal|attest|certify|signature|signing|disab|veteran|gender|race|ethnicity|religion|birth|conflict|non.?compete|consent|agree|password|token|salary|compensation|relocat|available|availability)\w*", re.I)
_CAREER = re.compile(r"\b(experience|project|achievement|skill|education|degree|certification|built|contribut|accomplish)\w*", re.I)
_PROMPT = re.compile(r"\b(ignore|override|disregard|instructions|system prompt|developer message)\b", re.I)


def validate_question_specs(specs: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(specs, (tuple, list)) or len(specs) > 50:
        raise ValueError("Questionnaire requires at most fifty question objects")
    result = []
    seen = set()
    for spec in specs:
        if not isinstance(spec, dict) or set(spec) != {"id", "text", "claim_ids", "required"}:
            raise ValueError("Each question requires id, text, claim_ids, and required")
        opaque(spec["id"])
        if spec["id"] in seen:
            raise ValueError("Question IDs must be distinct")
        seen.add(spec["id"])
        text = spec["text"]
        if type(text) is not str or not text.strip() or len(text) > 2048 or any(ord(c) < 32 for c in text):
            raise ValueError("Question text must be a bounded nonblank line")
        ids = spec["claim_ids"]
        if type(ids) is not list or len(ids) > 10 or len(set(ids)) != len(ids) or type(spec["required"]) is not bool:
            raise ValueError("Question claims must be a short distinct list and required must be boolean")
        for claim_id in ids:
            opaque(claim_id)
        result.append({"id": spec["id"], "text": text, "claim_ids": list(ids), "required": spec["required"]})
    return tuple(result)


class QuestionnaireService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def prepare(self, job_id: str, specs: object) -> tuple[dict[str, Any], ...]:
        questions = validate_question_specs(specs)
        with self._repository.read_transaction():
            JobService(self._repository).get(job_id)
            service = ProfileService(self._repository)
            claims, _ = service.validated_profile()
            by_id = {c.id: c for c in claims}
            results = []
            policy = job_policy(job_id)
            for q in questions:
                text = q["text"]
                gated = bool(_HUMAN_GATE.search(text) or _PROMPT.search(text))
                try:
                    validate_profile_import_metadata((text,))
                except ValueError:
                    gated = True
                parts = []
                mappings = []
                issues: list[Any] = []
                if gated or not _CAREER.search(text):
                    issues.append({"kind": "need_info", "reason": "human_answer_required",
                        "question": "Answer this question yourself. No sensitive answer is inferred or stored."})
                elif not q["claim_ids"]:
                    issues.append({"kind": "need_info", "reason": "missing_evidence",
                        "question": "Select approved career facts that answer this question."})
                else:
                    for claim_id in q["claim_ids"]:
                        outcome = service.packet_for_claim(claim_id, policy=policy)
                        if not isinstance(outcome, Resolved):
                            issues.append(to_jsonable(outcome))
                        elif by_id[claim_id].claim_type.startswith("contact_") or by_id[claim_id].claim_type == "candidate_name":
                            issues.append({"kind": "need_info", "reason": "out_of_scope", "question": "Select career evidence for this career question."})
                        else:
                            parts.append(by_id[claim_id].canonical_text)
                            mappings.append({"text": by_id[claim_id].canonical_text, "claim_ids": list(outcome.packet.claim_ids),
                                             "evidence_ids": [e.id for e in outcome.packet.evidence]})
                results.append({"question_id": q["id"], "question": text, "required": q["required"],
                    "status": "need_info" if issues else "draft", "answer": None if issues else "\n".join(parts),
                    "factual_units": [] if issues else mappings, "need_info": issues,
                    "human_review_required": True, "external_action_taken": False})
            return tuple(results)
