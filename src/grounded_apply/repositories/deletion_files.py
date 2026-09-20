"""Allowlisted, descriptor-relative portable runtime removal."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from grounded_apply.config import require_initialized_profile_storage, resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.backup_files import (
    _identity, _private_directory, _private_file, _safe_path, read_private_file,
)
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
from grounded_apply.services.deletion import (
    RECOVERY, DeletionError, DeletionInventory, DeletionOutcomeUnknownError,
    DeletionResult, InventoryEntry,
)


_MAX_AUXILIARY_BYTES = 16 * 1024 * 1024
_MAX_INVENTORY_BYTES = MAX_SNAPSHOT_BYTES + 2 * _MAX_AUXILIARY_BYTES
_HASH_CHUNK_BYTES = 64 * 1024


def _paths(target: Path, receipt: Path) -> tuple[Path, Path]:
    _safe_path(target)
    _safe_path(receipt)
    if target.is_symlink() or receipt.is_symlink():
        raise DeletionError("Deletion paths cannot be symlinks")
    target = target.parent.resolve() / target.name
    receipt = receipt.parent.resolve() / receipt.name
    if receipt.is_relative_to(target):
        raise DeletionError("The receipt must remain outside the target")
    return target, receipt


def _target_digest(target: Path, receipt: Path) -> str:
    return sha256(json.dumps([str(target), str(receipt)]).encode()).hexdigest()


def _file_digest(path: Path, *, limit: int) -> tuple[str, os.stat_result]:
    """Hash bounded private input without retaining its content in memory."""

    _safe_path(path)
    before = path.lstat()
    _private_file(before)
    if before.st_size > limit:
        raise DeletionError("Deletion input exceeds its size limit")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise DeletionError("Platform does not support safe deletion file opens")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        _private_file(opened)
        if _identity(before) != _identity(opened):
            raise DeletionError("Deletion input changed while opening")
        digest = sha256()
        size = 0
        while size <= limit:
            block = os.read(descriptor, min(_HASH_CHUNK_BYTES, limit + 1 - size))
            if not block:
                break
            size += len(block)
            digest.update(block)
        after = os.fstat(descriptor)
        current = path.lstat()
        _private_file(after)
        _private_file(current)
        _safe_path(path)
        if (
            size != before.st_size or size > limit
            or _identity(before) != _identity(after) or _identity(after) != _identity(current)
        ):
            raise DeletionError("Deletion input changed during inventory")
        return digest.hexdigest(), after
    finally:
        os.close(descriptor)


def _entry(
    path: Path, root: Path, kind: str, *, max_bytes: int = _MAX_AUXILIARY_BYTES,
) -> InventoryEntry:
    if kind == "directory":
        _private_directory(path)
        digest = None
        metadata = path.lstat()
    else:
        digest, metadata = _file_digest(path, limit=max_bytes)
    identity = (*_identity(metadata), metadata.st_mode, metadata.st_uid, metadata.st_nlink)
    return InventoryEntry(str(path.relative_to(root)), kind, identity, digest)


class LocalDeletionStorage:
    def completed(self, target: Path, receipt: Path, token: str) -> DeletionResult | None:
        target, receipt = _paths(target, receipt)
        content = read_private_file(receipt, limit=8192)
        if content is None:
            return None
        try:
            records = [json.loads(line) for line in content.splitlines()]
            keys = {"schema_version", "operation_id", "at", "target_sha256", "preview_token", "files", "directories", "phase"}
            if len(records) != 2 or any(set(record) != keys for record in records):
                raise ValueError
            start, finish = records
            if (
                start["phase"] != "started" or finish["phase"] != "completed"
                or {**start, "phase": "completed"} != finish
                or start["schema_version"] != 1
                or start["preview_token"] != token
                or start["target_sha256"] != _target_digest(target, receipt)
                or any(type(start[k]) is not int or not 0 < start[k] < 100 for k in ("files", "directories"))
                or target.exists() or target.is_symlink()
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise DeletionError("The receipt is incomplete, changed, or belongs to another operation") from None
        return DeletionResult(token, start["files"], start["directories"], False, True, True)

    def inventory(self, target: Path, receipt: Path) -> DeletionInventory:
        try:
            return self._inventory(target, receipt)
        except DeletionError:
            raise
        except Exception:
            raise DeletionError("Deletion requires a safe initialized portable home and a new external receipt") from None

    def _inventory(self, target: Path, receipt: Path) -> DeletionInventory:
        target, receipt = _paths(target, receipt)
        if receipt.exists():
            raise DeletionError("The receipt destination already exists")
        paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(target)})
        require_initialized_profile_storage(paths, read_only=True)
        directories = set(paths.private_directories())
        files = {paths.config_file, paths.database}
        restore_receipt = target / "restore-receipt.json"
        if restore_receipt.exists() or restore_receipt.is_symlink():
            files.add(restore_receipt)
        limits = {path: MAX_SNAPSHOT_BYTES if path == paths.database else _MAX_AUXILIARY_BYTES for path in files}
        expected = directories | files
        entries: list[InventoryEntry] = []
        for directory in sorted(directories):
            entries.append(_entry(directory, target, "directory"))
            if set(directory.iterdir()) != {p for p in expected if p.parent == directory}:
                raise DeletionError("Unknown or missing runtime entries prevent deletion")
        for path in sorted(files):
            entries.append(_entry(path, target, "file", max_bytes=limits[path]))
        if sum(entry.identity[2] for entry in entries if entry.kind == "file") > _MAX_INVENTORY_BYTES:
            raise DeletionError("Deletion inventory exceeds its size limit")
        # The normal public adapter verifies the current schema and read-only
        # SQLite safety. No database is opened by the destructive operation.
        with SQLiteRepository(paths.database, read_only=True):
            pass
        for entry in entries:
            path = target / entry.relative_path
            if _entry(path, target, entry.kind, max_bytes=limits.get(path, _MAX_AUXILIARY_BYTES)) != entry:
                raise DeletionError("The target changed during inventory")
        return DeletionInventory(str(target), str(receipt), tuple(entries))

    def remove(self, inventory: DeletionInventory, *, confirm: bool, token: str) -> None:
        if confirm is not True or type(inventory) is not DeletionInventory or token != inventory.token:
            raise DeletionError("Removal requires explicit matching preview confirmation")
        target, receipt = Path(inventory.target), Path(inventory.receipt)
        if self.inventory(target, receipt) != inventory:
            raise DeletionError("Target changed after preview")
        database = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(target)}).database
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise DeletionError("Platform lacks safe directory descriptors")
        descriptors: dict[str, int] = {}
        receipt_fd: int | None = None
        started = False
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptors[".."] = os.open(target.parent, flags)
            dirs = sorted((e for e in inventory.entries if e.kind == "directory"), key=lambda e: (len(Path(e.relative_path).parts), e.relative_path))
            for entry in dirs:
                relative = Path(entry.relative_path)
                parent = ".." if entry.relative_path == "." else str(relative.parent)
                name = target.name if entry.relative_path == "." else relative.name
                fd = os.open(name, flags, dir_fd=descriptors[parent])
                descriptors[entry.relative_path] = fd
                if _identity(os.fstat(fd)) != entry.identity[:5]:
                    raise DeletionError("Directory identity changed before removal")
            if self.inventory(target, receipt) != inventory:
                raise DeletionError("Target changed before removal")
            record = {
                "schema_version": 1, "operation_id": str(uuid4()),
                "at": datetime.now(UTC).isoformat(),
                "target_sha256": _target_digest(target, receipt), "preview_token": token,
                "files": sum(e.kind == "file" for e in inventory.entries),
                "directories": len(dirs), "phase": "started",
            }
            receipt_fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            started = True
            def append(phase: str) -> None:
                assert receipt_fd is not None
                metadata = os.fstat(receipt_fd)
                _private_file(metadata)
                if _identity(metadata) != _identity(receipt.lstat()):
                    raise DeletionError("Receipt identity changed")
                data = (json.dumps({**record, "phase": phase}, sort_keys=True) + "\n").encode()
                if os.write(receipt_fd, data) != len(data):
                    raise DeletionError("Receipt write was incomplete")
                os.fsync(receipt_fd)
            append("started")
            parent_fd = os.open(receipt.parent, flags)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            for entry in (e for e in inventory.entries if e.kind == "file"):
                self._verify_directories(target, dirs, descriptors)
                path = target / entry.relative_path
                maximum = MAX_SNAPSHOT_BYTES if path == database else _MAX_AUXILIARY_BYTES
                if _entry(path, target, "file", max_bytes=maximum) != entry:
                    raise DeletionError("File identity changed during removal")
                relative = Path(entry.relative_path)
                parent_fd = descriptors[str(relative.parent)]
                os.unlink(relative.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            remaining = list(dirs)
            for entry in reversed(dirs):
                self._verify_directories(target, remaining, descriptors)
                relative = Path(entry.relative_path)
                parent = ".." if entry.relative_path == "." else str(relative.parent)
                name = target.name if entry.relative_path == "." else relative.name
                os.rmdir(name, dir_fd=descriptors[parent])
                os.fsync(descriptors[parent])
                remaining.remove(entry)
            if target.exists() or target.is_symlink():
                raise DeletionError("Target reappeared during removal")
            append("completed")
        except Exception:
            if started:
                raise DeletionOutcomeUnknownError(RECOVERY) from None
            raise DeletionError("Deletion validation failed before removal") from None
        finally:
            if receipt_fd is not None:
                os.close(receipt_fd)
            for fd in reversed(tuple(descriptors.values())):
                os.close(fd)

    @staticmethod
    def _verify_directories(target: Path, entries: list[InventoryEntry], descriptors: dict[str, int]) -> None:
        for entry in entries:
            path = target / entry.relative_path
            _private_directory(path)
            current = path.lstat()
            opened = os.fstat(descriptors[entry.relative_path])
            if (current.st_dev, current.st_ino) != entry.identity[:2] or (opened.st_dev, opened.st_ino) != entry.identity[:2]:
                raise DeletionError("Directory identity changed during removal")
