"""Explicit, preview-bound logical deletion of a portable runtime."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol


RECOVERY = "Deletion may be incomplete or complete. Inspect the external receipt and target; do not reuse a partial operation."


class DeletionError(ValueError):
    """A content-free refusal before deletion."""


class DeletionOutcomeUnknownError(DeletionError):
    """The receipt must be inspected before another operation."""


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    relative_path: str
    kind: str
    identity: tuple[int, ...]
    sha256: str | None


@dataclass(frozen=True, slots=True)
class DeletionInventory:
    target: str
    receipt: str
    entries: tuple[InventoryEntry, ...]

    @property
    def token(self) -> str:
        payload = {"schema_version": 1, **asdict(self)}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DeletionResult:
    preview_token: str
    files: int
    directories: int
    dry_run: bool
    deleted: bool
    replayed: bool
    retained: tuple[str, ...] = ("external_receipt", "external_backups", "external_source_files", "external_exports")
    secure_erasure: bool = False


class DeletionStorage(Protocol):
    def completed(self, target: Path, receipt: Path, token: str) -> DeletionResult | None: ...
    def inventory(self, target: Path, receipt: Path) -> DeletionInventory: ...
    def remove(self, inventory: DeletionInventory, *, confirm: bool, token: str) -> None: ...


class DeletionService:
    def __init__(self, storage: DeletionStorage) -> None:
        self._storage = storage

    def delete(
        self, target: Path, receipt: Path, *, confirm: bool = False,
        preview_token: str | None = None,
    ) -> DeletionResult:
        if type(confirm) is not bool:
            raise DeletionError("Confirmation must be boolean")
        if preview_token is not None and (
            type(preview_token) is not str or len(preview_token) != 64
            or any(c not in "0123456789abcdef" for c in preview_token)
        ):
            raise DeletionError("A valid preview token is required")
        if confirm and preview_token is None:
            raise DeletionError("Confirmed deletion requires the token from preview")
        try:
            if confirm and preview_token is not None:
                replay = self._storage.completed(target, receipt, preview_token)
                if replay is not None:
                    return replay
            inventory = self._storage.inventory(target, receipt)
            if preview_token is not None and inventory.token != preview_token:
                raise DeletionError("Target or receipt destination changed; preview again")
            result = DeletionResult(
                inventory.token,
                sum(e.kind == "file" for e in inventory.entries),
                sum(e.kind == "directory" for e in inventory.entries),
                not confirm, confirm, False,
            )
            if confirm:
                self._storage.remove(inventory, confirm=True, token=inventory.token)
            return result
        except DeletionError:
            raise
        except Exception:
            raise DeletionError("Deletion validation failed; inspect the target and receipt") from None
