"""Pure source ordering for bounded discovery rounds.

The search service owns scope validation, lease acquisition and atomic ledger
reservation. This helper retains no authority or mutable cross-call state.
Automatic sources rotate by reservation generation; manual gaps follow them.
An absent reservation preserves the original source order for legacy runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


SOURCE_ROTATION_POLICY = "source_rotation@1"
_MAX_COUNTER = 1_000_000_000
_MAX_SOURCES = 32


@dataclass(frozen=True, slots=True)
class SourceRotation:
    generation: int
    reserved_epoch: int

    def __post_init__(self) -> None:
        if (type(self.generation) is not int or type(self.reserved_epoch) is not int
            or not 1 <= self.generation <= self.reserved_epoch <= _MAX_COUNTER):
            raise ValueError("Source rotation counters are invalid")

    def to_dict(self) -> dict[str, int | str]:
        return {"schema_version": 1, "policy": SOURCE_ROTATION_POLICY,
            "generation": self.generation, "reserved_epoch": self.reserved_epoch}


def validate_source_rotation(value: object) -> SourceRotation:
    fields = {"schema_version", "policy", "generation", "reserved_epoch"}
    if (type(value) is not dict or set(value) != fields or type(value["schema_version"]) is not int
        or value["schema_version"] != 1 or value["policy"] != SOURCE_ROTATION_POLICY):
        raise ValueError("Source rotation checkpoint is invalid")
    return SourceRotation(value["generation"], value["reserved_epoch"])


def source_indices(providers: tuple[str, ...], reservation: SourceRotation | None) -> tuple[int, ...]:
    """Derive fetch, capture-quota and final round-robin order together.

    Provider registry validation remains with the search manifest service; only
    the literal manual provider changes ordering here. No provider name becomes
    a fetch destination. A retry uses its existing immutable reservation.
    """
    if (type(providers) is not tuple or not 1 <= len(providers) <= _MAX_SOURCES
        or any(type(provider) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", provider) is None
               for provider in providers)
        or reservation is not None and type(reservation) is not SourceRotation):
        raise ValueError("Source rotation requires bounded validated provider names and a reservation")
    original = tuple(range(len(providers)))
    if reservation is None:
        return original
    automatic = tuple(index for index in original if providers[index] != "manual")
    if not automatic:
        return original
    manual = tuple(index for index in original if providers[index] == "manual")
    start = (reservation.generation - 1) % len(automatic)
    return (*automatic[start:], *automatic[:start], *manual)


def validate_rotation_ledger(reservations: tuple[SourceRotation, ...], *, lease_epoch: int) -> tuple[SourceRotation, ...]:
    """Validate one saved scope's complete lease-ordered reservation sequence.

    The caller validates the scope association and immutable event histories
    before passing one reservation per run. Retry lease epochs may leave gaps;
    reservation generations may not. Later completion cannot change ordering.
    """
    if (type(reservations) is not tuple or any(type(value) is not SourceRotation for value in reservations)
        or type(lease_epoch) is not int or not 0 <= lease_epoch <= _MAX_COUNTER):
        raise ValueError("Source rotation ledger is invalid")
    ordered = tuple(sorted(reservations, key=lambda value: value.generation))
    previous_epoch = 0
    for generation, reservation in enumerate(ordered, 1):
        if (reservation.generation != generation
            or not previous_epoch < reservation.reserved_epoch <= lease_epoch):
            raise ValueError("Source rotation generation custody is invalid")
        previous_epoch = reservation.reserved_epoch
    return ordered
