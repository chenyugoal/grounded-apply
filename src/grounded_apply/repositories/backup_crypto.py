"""Optional maintained cryptography recipe; no plaintext fallback."""

from __future__ import annotations

import base64
import binascii
import os

from grounded_apply.services.backup import (
    BackupError, MAX_ARCHIVE_BYTES, MAX_SNAPSHOT_BYTES, validate_passphrase,
)


_MAGIC = b"GAPPLY-BACKUP\x00\x01"
_SCOPE = b"profile_database\x00"


class FernetBackupCipher:
    """Format 1: Argon2id with fixed bounded parameters and Fernet."""

    def __init__(self) -> None:
        try:
            from cryptography.fernet import Fernet, InvalidToken
            from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        except ImportError:
            raise BackupError(
                "Encrypted backups require the optional grounded-apply[backup] dependency"
            ) from None
        self._fernet = Fernet
        self._invalid_token = InvalidToken
        self._argon2id = Argon2id

    def _key(self, passphrase: bytes, salt: bytes) -> bytes:
        validate_passphrase(passphrase)
        return base64.urlsafe_b64encode(self._argon2id(
            salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536,
        ).derive(passphrase))

    def encrypt(self, snapshot: bytes, passphrase: bytes) -> bytes:
        if type(snapshot) is not bytes or not 0 < len(snapshot) <= MAX_SNAPSHOT_BYTES:
            raise BackupError("Profile snapshot exceeds the supported size")
        salt = os.urandom(16)
        header = _MAGIC + salt
        token = self._fernet(self._key(passphrase, salt)).encrypt(header + _SCOPE + snapshot)
        return header + token

    def decrypt(self, archive: bytes, passphrase: bytes) -> bytes:
        if (
            type(archive) is not bytes or len(archive) > MAX_ARCHIVE_BYTES
            or not archive.startswith(_MAGIC) or len(archive) < len(_MAGIC) + 16 + 100
        ):
            raise BackupError("Unsupported or invalid encrypted archive")
        header_size = len(_MAGIC) + 16
        header, token = archive[:header_size], archive[header_size:]
        try:
            raw = base64.b64decode(token, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw) != token:
                raise ValueError("noncanonical encoding")
            payload = self._fernet(self._key(passphrase, header[len(_MAGIC):])).decrypt(token)
        except (self._invalid_token, ValueError, binascii.Error):
            raise BackupError("Archive authentication failed or passphrase is incorrect") from None
        prefix = header + _SCOPE
        if not payload.startswith(prefix) or not 0 < len(payload) - len(prefix) <= MAX_SNAPSHOT_BYTES:
            raise BackupError("Unsupported authenticated archive payload")
        return payload[len(prefix):]
