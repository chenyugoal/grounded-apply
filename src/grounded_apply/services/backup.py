"""Typed orchestration for authenticated, bounded profile backups."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol


# Supported profile capacity. Keep archive headroom for the format-1 envelope
# and Fernet's base64 expansion; neither value changes ingress/HTTP limits.
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 384 * 1024 * 1024
SNAPSHOT_WORK_SECONDS = 30.0


class BackupError(RuntimeError):
    """A backup failed closed; messages must never contain input content."""


class BackupOutcomeUnknownError(BackupError):
    """A filesystem operation may have completed before its result was lost."""


class BackupCipher(Protocol):
    def encrypt(self, snapshot: bytes, passphrase: bytes) -> bytes: ...

    def decrypt(self, archive: bytes, passphrase: bytes) -> bytes: ...


class BackupStorage(Protocol):
    def capture_profile(self) -> bytes: ...

    def validate_snapshot(self, snapshot: bytes) -> None: ...

    def validate_destination(self, path: Path) -> None: ...

    def read_archive(self, path: Path) -> bytes | None: ...

    def write_archive(self, path: Path, archive: bytes) -> None: ...

    def restore_profile(
        self, target: Path, snapshot: bytes, archive_sha256: str, *, confirm: bool,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class BackupResult:
    archive_sha256: str | None
    snapshot_sha256: str
    snapshot_bytes: int
    dry_run: bool
    replayed: bool
    scope: str = "profile_database"


class BackupService:
    """Keep approval and retry policy above filesystem and crypto adapters."""

    def __init__(self, storage: BackupStorage, cipher: BackupCipher) -> None:
        self._storage = storage
        self._cipher = cipher

    def create(
        self, destination: Path, passphrase: bytes, *, dry_run: bool = False,
    ) -> BackupResult:
        if type(dry_run) is not bool:
            raise BackupError("Backup preview flag must be boolean")
        validate_passphrase(passphrase)
        self._storage.validate_destination(destination)
        snapshot = self._storage.capture_profile()
        self._storage.validate_snapshot(snapshot)
        digest = sha256(snapshot).hexdigest()
        previous = self._storage.read_archive(destination)
        if previous is not None:
            if self._cipher.decrypt(previous, passphrase) != snapshot:
                raise BackupError("Backup destination already contains a different snapshot")
            return BackupResult(sha256(previous).hexdigest(), digest, len(snapshot), dry_run, True)
        if dry_run:
            return BackupResult(None, digest, len(snapshot), True, False)
        archive = self._cipher.encrypt(snapshot, passphrase)
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise BackupError("Encrypted archive exceeds the supported size")
        self._storage.write_archive(destination, archive)
        return BackupResult(sha256(archive).hexdigest(), digest, len(snapshot), False, False)

    def restore(
        self, archive_path: Path, target: Path, passphrase: bytes,
        *, confirm: bool = False, expected_archive_sha256: str | None = None,
    ) -> BackupResult:
        if type(confirm) is not bool:
            raise BackupError("Restore confirmation must be boolean")
        if confirm and (
            type(expected_archive_sha256) is not str
            or len(expected_archive_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_archive_sha256)
        ):
            raise BackupError("Confirmed restore requires the archive SHA-256 from preview")
        validate_passphrase(passphrase)
        archive = self._storage.read_archive(archive_path)
        if archive is None:
            raise BackupError("An existing encrypted archive is required")
        archive_digest = sha256(archive).hexdigest()
        if expected_archive_sha256 is not None and expected_archive_sha256 != archive_digest:
            raise BackupError("Archive changed since the restore preview")
        snapshot = self._cipher.decrypt(archive, passphrase)
        self._storage.validate_snapshot(snapshot)
        replayed = self._storage.restore_profile(
            target, snapshot, archive_digest, confirm=confirm,
        )
        return BackupResult(
            archive_digest, sha256(snapshot).hexdigest(), len(snapshot), not confirm, replayed,
        )


def validate_passphrase(passphrase: bytes) -> None:
    """Accept bounded UTF-8 passphrases without silently trimming whitespace."""

    if type(passphrase) is not bytes or not 12 <= len(passphrase) <= 1024:
        raise BackupError("Passphrase must contain 12 to 1024 UTF-8 bytes")
    try:
        text = passphrase.decode("utf-8")
    except UnicodeError:
        raise BackupError("Passphrase must contain valid UTF-8") from None
    if not text.strip() or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise BackupError("Passphrase must not be blank or contain control characters")
