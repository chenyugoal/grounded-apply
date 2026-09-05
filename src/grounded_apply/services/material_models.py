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
    transformation: str = "approved_text_selection@1"


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
