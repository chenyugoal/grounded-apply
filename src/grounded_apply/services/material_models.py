"""Typed structures and ports for deterministic factual material rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


TRANSFORMATIONS = {"approved_text_selection@1", "approved_text_selection@2"}
HEADING_TYPES = {"employment_description", "employment_title", "education", "education_degree", "portfolio_item"}


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


def heading_fields(unit: FactualUnit) -> tuple[str, ...]:
    """Version 2 may lay out exactly four existing pipe-delimited header fields."""
    fields = tuple(unit.text.split(" | "))
    if unit.presentation == "heading" and unit.claim_type in HEADING_TYPES and len(fields) == 4 and all(fields):
        return fields
    return (unit.text,)


def presented_text(structure: ResumeStructure, unit: FactualUnit) -> str:
    return " ".join(heading_fields(unit)) if structure.transformation == "approved_text_selection@2" else unit.text
