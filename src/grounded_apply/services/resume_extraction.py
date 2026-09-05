"""Conservative local extraction of exact text spans into review proposals.

No model, network, or storage access. Unknown lines remain unselected; explicit
labels are required for identity/contact fields. Career prose is copied intact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from grounded_apply.services.profile import CreateImportProposal, ProposedImportClaim, TextSourceSpan
from grounded_apply.services.profile_import_validation import (
    PROFILE_IMPORT_MAX_SOURCE_BYTES, validate_profile_import_proposal,
)

_SECTIONS = {
    "experience": "employment_description", "work experience": "employment_description",
    "professional experience": "employment_description", "employment": "employment_description",
    "projects": "portfolio_item", "education": "education", "skills": "skill_use",
    "technical skills": "skill_use", "certifications": "certification",
    "publications": "publication", "languages": "language", "achievements": "achievement",
}
_LABELS = {"name": "candidate_name", "email": "contact_email", "phone": "contact_phone",
           "location": "contact_location", "website": "contact_url", "portfolio": "contact_url"}
_PROMPT = re.compile(r"\b(ignore|disregard|override)\b.*\b(instructions?|previous|system)\b|\b(system|assistant|developer)\s*:|\b(mark|approve)\b.*\b(verified|claims?|approved)\b", re.I)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    source_sha256: str
    proposals: tuple[ProposedImportClaim, ...]
    skipped_lines: int
    review_required: bool = True
    extractor: str = "grounded-apply.exact-resume-lines@1"
    content_trust: str = "untrusted"

    def selected_request(self, indexes: tuple[int, ...], source_text: str, idempotency_key: str) -> CreateImportProposal:
        if (
            not indexes or len(set(indexes)) != len(indexes)
            or any(type(i) is not int or i < 0 or i >= len(self.proposals) for i in indexes)
            or sha256(source_text.encode("utf-8")).hexdigest() != self.source_sha256
        ):
            raise ValueError("Select distinct displayed proposal indexes from the unchanged source")
        return CreateImportProposal(idempotency_key=idempotency_key, source_text=source_text,
            expected_source_sha256=self.source_sha256, proposals=tuple(self.proposals[i] for i in indexes))


def extract_resume(source_text: str) -> ExtractionResult:
    if type(source_text) is not str or not source_text.strip() or len(source_text.encode("utf-8")) > PROFILE_IMPORT_MAX_SOURCE_BYTES:
        raise ValueError("Resume extraction requires bounded nonblank UTF-8 text")
    proposals: list[ProposedImportClaim] = []
    section: str | None = None
    skipped = 0
    offset = 0
    previous_line: str | None = None
    for raw in source_text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        trimmed = line.strip()
        start = offset + len(line) - len(line.lstrip())
        offset += len(raw)
        heading = trimmed.rstrip(":").casefold()
        if not trimmed:
            continue
        if heading in _SECTIONS:
            section = _SECTIONS[heading]
            previous_line = trimmed
            continue
        if _PROMPT.search(trimmed):
            skipped += 1
            section = None
            previous_line = trimmed
            continue
        label = re.fullmatch(r"(Name|Email|Phone|Location|Website|Portfolio):\s*(.+)", trimmed, re.I)
        claim_type: str | None = None
        value = trimmed
        if label:
            claim_type = _LABELS[label[1].casefold()]
            value = label[2].strip()
        elif section is not None:
            if trimmed.isupper() and not re.match(r"^[-*•]\s", trimmed):
                section = None
            else:
                claim_type = section
                bullet = re.match(r"^[-*•]\s+", value)
                if bullet:
                    value = value[bullet.end():]
        if claim_type is None:
            skipped += 1
            previous_line = trimmed
            continue
        try:
            validate_profile_import_proposal(claim_type=claim_type, value=value, canonical_text=value,
                evidence_text=trimmed, evidence_context=trimmed, evidence_prefix="",
                preceding_line=previous_line)
            proposal = ProposedImportClaim(claim_type=claim_type, value=value, canonical_text=value,
                span=TextSourceSpan(start=start, end=start + len(trimmed), text=trimmed))
        except ValueError:
            skipped += 1
        else:
            proposals.append(proposal)
        previous_line = trimmed
        if len(proposals) > 1000:
            raise ValueError("Resume extraction exceeds the proposal limit")
    return ExtractionResult(sha256(source_text.encode("utf-8")).hexdigest(), tuple(proposals), skipped)
