"""Conservative local extraction of exact text spans into review proposals.

No model, network, or storage access. Every nonblank line is accounted for;
unknown lines require classification and explicit contact labels are required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from grounded_apply.services.profile import CreateImportProposal, ProposedImportClaim, TextSourceSpan
from grounded_apply.services.profile_import_validation import (
    PROFILE_IMPORT_COMPLETE_FACTS_POLICY_VERSION, PROFILE_IMPORT_CONTENT_POLICY_VERSION,
    PROFILE_IMPORT_MAX_SOURCE_BYTES, validate_profile_import_proposal,
    profile_value_schema_version, validate_profile_import_batch,
)

_SECTIONS = {
    "experience": "employment_description", "work experience": "employment_description",
    "professional experience": "employment_description", "employment": "employment_description",
    "projects": "portfolio_item", "education": "education", "skills": "skill_use",
    "technical skills": "skill_use", "certifications": "certification",
    "publications": "publication", "languages": "language", "achievements": "achievement",
}
_EXTENDED_SECTIONS = {
    **_SECTIONS,
    "research": "employment_description", "research experience": "employment_description",
    "research projects": "portfolio_item", "selected projects": "portfolio_item",
    "selected publications": "publication", "papers": "publication",
    "teaching": "employment_description", "teaching experience": "employment_description",
    "volunteering": "employment_description", "service": "employment_description",
    "awards": "achievement", "honors": "achievement", "honors and awards": "achievement",
    "summary": "employment_description", "professional summary": "employment_description",
}
_RESEARCH_SECTIONS = {
    **_EXTENDED_SECTIONS,
    "research": "research_description",
    "research experience": "research_description",
    "research projects": "research_description",
}
_NEUTRAL_SECTIONS = frozenset({
    "research interests", "academic research", "selected research", "professional memberships",
})
_LABELS = {"name": "candidate_name", "email": "contact_email", "phone": "contact_phone",
           "location": "contact_location", "website": "contact_url", "portfolio": "contact_url"}
_PROMPT = re.compile(r"\b(ignore|disregard|override)\b.*\b(instructions?|previous|system)\b|\b(system|assistant|developer)\s*:|\b(mark|approve)\b.*\b(verified|claims?|approved)\b", re.I)


@dataclass(frozen=True, slots=True)
class InventoryLine:
    """One source line; blocked content is located without echoing its value."""

    line_number: int
    start: int
    end: int
    text: str | None
    status: str
    reason: str
    proposal_index: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    source_sha256: str
    proposals: tuple[ProposedImportClaim, ...]
    skipped_lines: int
    inventory: tuple[InventoryLine, ...] = ()
    review_required: bool = True
    extractor: str = "grounded-apply.exact-resume-lines@2"
    extractor_version: int = 2
    content_trust: str = "untrusted"

    def selected_request(self, indexes: tuple[int, ...], source_text: str, idempotency_key: str,
                         *, retain_all_facts: bool = False) -> CreateImportProposal:
        if type(retain_all_facts) is not bool:
            raise ValueError("retain_all_facts must be an explicit boolean")
        if (
            not indexes or len(set(indexes)) != len(indexes)
            or any(type(i) is not int or i < 0 or i >= len(self.proposals) for i in indexes)
            or sha256(source_text.encode("utf-8")).hexdigest() != self.source_sha256
        ):
            raise ValueError("Select distinct displayed proposal indexes from the unchanged source")
        return CreateImportProposal(idempotency_key=idempotency_key, source_text=source_text,
            expected_source_sha256=self.source_sha256, proposals=tuple(self.proposals[i] for i in indexes),
            content_policy_version=(PROFILE_IMPORT_COMPLETE_FACTS_POLICY_VERSION if retain_all_facts
                                    else PROFILE_IMPORT_CONTENT_POLICY_VERSION))


def extract_resume(source_text: str, *, version: int = 2) -> ExtractionResult:
    if type(version) is not int or version not in {1, 2, 3, 4}:
        raise ValueError("Unsupported resume extractor version")
    if type(source_text) is not str or not source_text.strip() or len(source_text.encode("utf-8")) > PROFILE_IMPORT_MAX_SOURCE_BYTES:
        raise ValueError("Resume extraction requires bounded nonblank UTF-8 text")
    proposals: list[ProposedImportClaim] = []
    inventory: list[InventoryLine] = []
    section: str | None = None
    skipped = 0
    offset = 0
    previous_line: str | None = None
    for line_number, raw in enumerate(source_text.splitlines(keepends=True), start=1):
        line = raw.rstrip("\r\n")
        trimmed = line.strip()
        start = offset + len(line) - len(line.lstrip())
        offset += len(raw)
        heading = trimmed.rstrip(":").casefold()
        if not trimmed:
            continue
        if len(inventory) >= 10_000:
            raise ValueError("Resume extraction exceeds the source-line inventory limit")
        if version == 4 and heading in _NEUTRAL_SECTIONS:
            section = None
            inventory.append(InventoryLine(line_number, start, start + len(trimmed), trimmed,
                                           "heading", "Neutral section boundary; no fact classification"))
            previous_line = trimmed
            continue
        sections = (_SECTIONS if version == 1 else
                    _EXTENDED_SECTIONS if version == 2 else _RESEARCH_SECTIONS)
        if heading in sections:
            section = sections[heading]
            inventory.append(InventoryLine(line_number, start, start + len(trimmed), trimmed,
                                           "heading", "Recognized section"))
            previous_line = trimmed
            continue
        if _PROMPT.search(trimmed):
            skipped += 1
            inventory.append(InventoryLine(line_number, start, start + len(trimmed), None,
                                           "blocked", "Instruction-like source content; not a career fact"))
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
            # Check unknown lines too, so a sensitive line outside a recognized
            # section is not echoed as an ordinary classification question.
            try:
                validate_profile_import_proposal(claim_type="employment_description", value=value,
                    canonical_text=value, evidence_text=trimmed, evidence_context=trimmed,
                    evidence_prefix="", preceding_line=previous_line)
            except ValueError:
                inventory.append(InventoryLine(line_number, start, start + len(trimmed), None,
                                               "blocked", "Restricted content or unsupported line length/format"))
            else:
                inventory.append(InventoryLine(line_number, start, start + len(trimmed), trimmed,
                                               "unclassified", "Needs a fact type or an explicit exclusion"))
            previous_line = trimmed
            continue
        try:
            validate_profile_import_proposal(claim_type=claim_type, value=value, canonical_text=value,
                evidence_text=trimmed, evidence_context=trimmed, evidence_prefix="",
                preceding_line=previous_line,
                value_schema_version=profile_value_schema_version((claim_type,)))
            proposal = ProposedImportClaim(claim_type=claim_type, value=value, canonical_text=value,
                span=TextSourceSpan(start=start, end=start + len(trimmed), text=trimmed))
        except ValueError:
            skipped += 1
            inventory.append(InventoryLine(line_number, start, start + len(trimmed), None,
                                           "blocked", "Restricted content or unsupported line length/format"))
        else:
            inventory.append(InventoryLine(line_number, start, start + len(trimmed), trimmed,
                                           "proposed", "Pending user review", len(proposals)))
            proposals.append(proposal)
        previous_line = trimmed
        if len(proposals) > 1000:
            raise ValueError("Resume extraction exceeds the proposal limit")
    # Unknown lines are now visible. Apply the same cross-component sensitive
    # assignment guard as import before returning any inventory or proposals.
    try:
        validate_profile_import_batch(source_text=source_text, spans=(),
            proposals=tuple((line.text, line.text, line.text) for line in inventory if line.text is not None),
            metadata=(), content_policy_version=PROFILE_IMPORT_COMPLETE_FACTS_POLICY_VERSION)
    except ValueError:
        raise ValueError("Resume inventory contains fragmented restricted content; review the source before extraction") from None
    return ExtractionResult(sha256(source_text.encode("utf-8")).hexdigest(), tuple(proposals), skipped,
                            tuple(inventory), extractor=f"grounded-apply.exact-resume-lines@{version}",
                            extractor_version=version)
