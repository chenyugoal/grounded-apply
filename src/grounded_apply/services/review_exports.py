"""Immutable values for an explicitly requested, private search review copy.

These values describe one validated read snapshot. They grant no approval and
are not a cache of current readiness for later application actions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReviewMaterial:
    """A current bundle; JSON carries its metadata and the PDF stays binary."""

    job_id: str
    material_json: str
    pdf_bytes: bytes


@dataclass(frozen=True, slots=True)
class SearchReviewSnapshot:
    """Canonical index and at most fifty bundles from the same read snapshot."""

    run_id: str
    review_json: str
    materials: tuple[ReviewMaterial, ...]
