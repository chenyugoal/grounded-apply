"""Private POSIX files and new-home restore for the backup service."""

from __future__ import annotations

import json
import os
import stat
from hashlib import sha256
from pathlib import Path

from grounded_apply.config import (
    DEFAULT_CONFIG, RuntimePaths, require_initialized_profile_storage,
    require_runtime_outside_repository, resolve_runtime_paths, source_checkout_root,
)
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.backup import BackupError, MAX_ARCHIVE_BYTES, MAX_SNAPSHOT_BYTES


def _private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_uid != os.getuid()
    ):
        raise BackupError("Backup directories must be private direct directories owned by the user")
    resolved = path.resolve()
    checkout = source_checkout_root()
    if checkout is not None and resolved.is_relative_to(checkout):
        raise BackupError("Backup paths must be outside Git worktrees")
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise BackupError("Backup paths must be outside Git worktrees")


def _safe_path(path: Path) -> None:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise BackupError("Backup paths must be explicit absolute paths")
    _private_directory(path.parent)


def _private_file(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_uid != os.getuid()
    ):
        raise BackupError("Backup files must be private direct single-link files owned by the user")


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def read_private_file(path: Path, *, limit: int) -> bytes | None:
    """Read through a no-follow descriptor with bounded stable identity checks."""

    _safe_path(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    _private_file(before)
    if before.st_size > limit:
        raise BackupError("Backup input exceeds its size limit")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise BackupError("Platform does not support safe backup file opens")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        _private_file(opened)
        if _identity(before) != _identity(opened):
            raise BackupError("Backup input changed while opening")
        blocks: list[bytes] = []
        size = 0
        while size <= limit:
            block = os.read(descriptor, min(65536, limit + 1 - size))
            if not block:
                break
            size += len(block)
            blocks.append(block)
        after = os.fstat(descriptor)
        current = path.lstat()
        _private_file(after)
        _private_file(current)
        _safe_path(path)
        if (
            size != before.st_size or size > limit
            or _identity(before) != _identity(after) or _identity(after) != _identity(current)
        ):
            raise BackupError("Backup input changed during capture")
        return b"".join(blocks)
    finally:
        os.close(descriptor)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private_file(path: Path, content: bytes) -> None:
    """Exclusive output only; never replace, truncate, or repair an existing file."""

    _safe_path(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise BackupError("Platform does not support safe backup file creation")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise BackupError("Private output write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        current = path.lstat()
        _private_file(after)
        _private_file(current)
        _safe_path(path)
        if (
            (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or _identity(current) != _identity(after) or after.st_size != len(content)
        ):
            raise BackupError("Private output changed during creation")
    finally:
        os.close(descriptor)
    _sync_directory(path.parent)


class LocalBackupStorage:
    """Adapters enforce guards even when invoked without the typed CLI."""

    def __init__(self, paths: RuntimePaths | None = None) -> None:
        self._paths = paths

    def capture_profile(self) -> bytes:
        if self._paths is None:
            raise BackupError("A source runtime is required for backup")
        paths = self._paths
        require_initialized_profile_storage(paths, read_only=True)
        before = paths.database.lstat()
        with SQLiteRepository(paths.database, read_only=True) as repository:
            require_initialized_profile_storage(paths, read_only=True)
            snapshot = repository.snapshot_bytes(max_bytes=MAX_SNAPSHOT_BYTES)
            require_initialized_profile_storage(paths, read_only=True)
            after = paths.database.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise BackupError("Source database identity changed during backup")
        return snapshot

    def validate_snapshot(self, snapshot: bytes) -> None:
        validate_profile_snapshot(snapshot)

    def validate_destination(self, path: Path) -> None:
        _safe_path(path)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        _private_file(metadata)

    def read_archive(self, path: Path) -> bytes | None:
        return read_private_file(path, limit=MAX_ARCHIVE_BYTES)

    def write_archive(self, path: Path, archive: bytes) -> None:
        if type(archive) is not bytes or not 0 < len(archive) <= MAX_ARCHIVE_BYTES:
            raise BackupError("Archive exceeds the supported size")
        write_private_file(path, archive)

    def restore_profile(
        self, target: Path, snapshot: bytes, archive_sha256: str, *, confirm: bool,
    ) -> bool:
        if type(confirm) is not bool:
            raise BackupError("Restore confirmation must be boolean")
        if (
            type(archive_sha256) is not str or len(archive_sha256) != 64
            or any(char not in "0123456789abcdef" for char in archive_sha256)
        ):
            raise BackupError("A valid archive identity is required")
        _safe_path(target)
        # Resolve the parent, never the final component: a target symlink must
        # fail instead of becoming authority to restore somewhere else.
        target = target.parent.resolve() / target.name
        paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(target)})
        if target.is_symlink():
            raise BackupError("Restore target cannot be a symlink")
        require_runtime_outside_repository(paths)
        validate_profile_snapshot(snapshot)
        receipt = json.dumps({
            "schema_version": 1, "scope": "profile_database",
            "archive_sha256": archive_sha256, "snapshot_sha256": sha256(snapshot).hexdigest(),
        }, sort_keys=True).encode("ascii")
        if target.exists():
            self._verify_restored_target(paths, snapshot, receipt)
            return True
        if not confirm:
            return False
        # mkdir is an exclusive reservation. Failure leaves a private incomplete
        # target; no retry adopts, overwrites, or deletes partial state.
        target.mkdir(mode=0o700)
        _private_directory(target)
        for directory in paths.private_directories():
            if directory != target:
                directory.mkdir(mode=0o700)
                _private_directory(directory)
        require_runtime_outside_repository(paths)
        write_private_file(paths.config_file, DEFAULT_CONFIG.encode("utf-8"))
        write_private_file(paths.database, snapshot)
        require_initialized_profile_storage(paths, read_only=True)
        write_private_file(target / "restore-receipt.json", receipt)
        self._verify_restored_target(paths, snapshot, receipt)
        _sync_directory(target.parent)
        return False

    def _verify_restored_target(
        self, paths: RuntimePaths, snapshot: bytes, receipt: bytes,
    ) -> None:
        root = paths.portable_root
        if root is None:
            raise BackupError("Restore requires an explicit target home")
        require_initialized_profile_storage(paths, read_only=True)
        expected_files = {
            paths.database: snapshot,
            paths.config_file: DEFAULT_CONFIG.encode("utf-8"),
            root / "restore-receipt.json": receipt,
        }
        expected_paths = set(paths.private_directories()) | set(expected_files)
        for directory in paths.private_directories():
            _private_directory(directory)
            if set(directory.iterdir()) != {path for path in expected_paths if path.parent == directory}:
                raise BackupError("Restore target is incomplete or has changed; use a new target")
        for path, expected in expected_files.items():
            if read_private_file(path, limit=len(expected)) != expected:
                raise BackupError("Restore target is incomplete or has changed; use a new target")
