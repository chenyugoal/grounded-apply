"""Read-only guidance for the existing bounded database snapshot contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES


@dataclass(frozen=True, slots=True)
class StorageCapacity:
    database_file_bytes: int
    snapshot_limit_bytes: int
    remaining_bytes: int
    status: Literal["available", "near_limit", "at_limit", "exceeded"]
    policy: str = "snapshot_capacity@1"


def storage_capacity(database_file_bytes: int) -> StorageCapacity:
    """Report file headroom, not reclaimable space or a backup validation result.

    Ninety percent is an advisory threshold only. It neither reserves room for
    a particular operation nor changes existing write/backup capacity policies.
    """
    if type(database_file_bytes) is not int or database_file_bytes < 0:
        raise ValueError("Database file size must be a nonnegative integer")
    status: Literal["available", "near_limit", "at_limit", "exceeded"]
    if database_file_bytes > MAX_SNAPSHOT_BYTES:
        status = "exceeded"
    elif database_file_bytes == MAX_SNAPSHOT_BYTES:
        status = "at_limit"
    elif database_file_bytes * 10 >= MAX_SNAPSHOT_BYTES * 9:
        status = "near_limit"
    else:
        status = "available"
    return StorageCapacity(database_file_bytes, MAX_SNAPSHOT_BYTES,
        max(0, MAX_SNAPSHOT_BYTES - database_file_bytes), status)
