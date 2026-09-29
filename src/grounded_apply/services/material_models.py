"""Typed structures and ports for deterministic factual material rendering."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from grounded_apply.domain import Claim, ClaimPacket


@dataclass(frozen=True, slots=True)
class FactualUnit:
    claim_id: str
    claim_type: str
    text: str
    packet_claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    claim_sha256: str
    presentation: str = "bullet"


@dataclass(frozen=True, slots=True)
class ResumeStructure:
    job_id: str
    units: tuple[FactualUnit, ...]
    schema_version: int = 1
    transformation: str = "approved_text_selection@2"


@dataclass(frozen=True, slots=True)
class RenderedResume:
    pdf: bytes
    latex: str
    extracted_text: str
    page_count: int
    renderer: str


class ResumeRenderer(Protocol):
    def render(self, structure: ResumeStructure) -> RenderedResume: ...
    def validate(self, structure: ResumeStructure, rendered: RenderedResume) -> None: ...


class MaterialValidationError(ValueError):
    """A material could not pass a deterministic factual or rendering gate."""


PRESENTATION_TRANSFORMATIONS = frozenset({"approved_text_selection@2", "approved_text_selection@3"})
TRANSFORMATIONS = frozenset({"approved_text_selection@1"}) | PRESENTATION_TRANSFORMATIONS
HEADING_TYPES = frozenset({"employment_description", "employment_title", "education", "education_degree", "portfolio_item"})


def heading_types(transformation: str) -> frozenset[str]:
    """Keep historical heading eligibility separate from the research extension."""
    if transformation not in TRANSFORMATIONS:
        raise MaterialValidationError("Unsupported material transformation")
    return HEADING_TYPES | {"research_description"} if transformation == "approved_text_selection@3" else HEADING_TYPES


def selected_presentation(claim: Claim, packet: ClaimPacket, transformation: str,
                          styles: dict[str, str]) -> str:
    """Registered presentation rule shared by preparation and historical audit."""
    if transformation not in TRANSFORMATIONS:
        raise MaterialValidationError("Unsupported material transformation")
    if claim.claim_type == "research_description" and transformation != "approved_text_selection@3":
        raise MaterialValidationError("Research facts require material transformation version 3")
    presentation = "bullet" if any(re.match(r"^\s*[-*•]\s+", e.source_text or "") for e in packet.evidence) else "paragraph"
    if transformation == "approved_text_selection@1":
        if styles:
            raise MaterialValidationError("Legacy materials cannot change presentation on replay")
        return presentation
    if any(re.match(r"^\s*\\resumeItem\s*\{", e.source_text or "") for e in packet.evidence):
        presentation = "bullet"
    allowed_headings = heading_types(transformation)
    if claim.claim_type in allowed_headings and any(re.match(r"^\s*\\resumeSubheading\b", e.source_text or "") for e in packet.evidence):
        presentation = "heading"
    presentation = styles.get(claim.id, presentation)
    if ((claim.claim_type == "candidate_name" or claim.claim_type.startswith("contact_")) and claim.id in styles
        or presentation == "heading" and claim.claim_type not in allowed_headings):
        raise MaterialValidationError("Presentation is incompatible with the selected claim type")
    return presentation


def validate_layout(layout: object, claim_ids: tuple[str, ...]) -> dict[str, str]:
    """Closed presentation-only contract; never accepts prose or executable markup."""
    if layout is None:
        return {}
    if (type(layout) is not dict or set(layout) != {"schema_version", "presentations"}
        or type(layout["schema_version"]) is not int or layout["schema_version"] != 1
        or type(layout["presentations"]) is not dict):
        raise MaterialValidationError("Layout requires schema_version 1 and a presentations object")
    styles = layout["presentations"]
    if any(type(key) is not str or key not in claim_ids or type(value) is not str
           or value not in {"heading", "bullet", "paragraph"} for key, value in styles.items()):
        raise MaterialValidationError("Layout must map selected claim IDs to heading, bullet, or paragraph")
    return dict(styles)


def heading_fields(unit: FactualUnit, transformation: str = "approved_text_selection@2") -> tuple[str, ...]:
    """Modern layouts retain four existing fields in their original order."""
    fields = tuple(unit.text.split(" | "))
    if unit.presentation == "heading" and unit.claim_type in heading_types(transformation) and len(fields) == 4 and all(fields):
        return fields
    return (unit.text,)


def presented_text(structure: ResumeStructure, unit: FactualUnit) -> str:
    return " ".join(heading_fields(unit, structure.transformation)) if structure.transformation in PRESENTATION_TRANSFORMATIONS else unit.text
