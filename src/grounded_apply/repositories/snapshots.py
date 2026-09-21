"""SQLite image validation before exporting or materializing a profile."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing

from grounded_apply.repositories._schema import (
    LATEST_SCHEMA_VERSION, SCHEMA_OBJECTS_QUERY, default_migrations_directory,
    reference_schema_objects, validate_schema,
)
from grounded_apply.services.backup import BackupError, MAX_SNAPSHOT_BYTES, SNAPSHOT_WORK_SECONDS


_SCHEMA_QUERY = SCHEMA_OBJECTS_QUERY
_RESTORABLE_SCHEMA_VERSIONS = frozenset({4, 5, 6, 7})


def validate_profile_snapshot(snapshot: bytes) -> None:
    """Refuse incompatible SQL structures, corruption, or omitted file artifacts.

    Authentication of an archive does not grant its SQL schema execution
    authority. Deserialize only into a defensive in-memory connection, compare
    the schema to local migrations before reading application tables, and never
    migrate or promote candidate claims as part of restore.
    """

    if (
        type(snapshot) is not bytes or not 100 <= len(snapshot) <= MAX_SNAPSHOT_BYTES
        or snapshot[:16] != b"SQLite format 3\x00" or snapshot[18:20] != b"\x01\x01"
    ):
        raise BackupError("Unsupported profile database image")
    schema_version = int.from_bytes(snapshot[60:64], "big")
    page_size = int.from_bytes(snapshot[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    if (
        page_size < 512 or page_size > 65536 or page_size & (page_size - 1)
        or int.from_bytes(snapshot[28:32], "big") * page_size != len(snapshot)
        or schema_version not in _RESTORABLE_SCHEMA_VERSIONS
        or schema_version > LATEST_SCHEMA_VERSION
    ):
        raise BackupError("Unsupported profile database size or schema version")
    deadline = time.monotonic() + SNAPSHOT_WORK_SECONDS
    try:
        expected = reference_schema_objects(default_migrations_directory(), target_version=schema_version)
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as database:
            database.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            database.execute("PRAGMA trusted_schema = OFF")
            database.execute("PRAGMA temp_store = MEMORY")
            database.deserialize(snapshot)
            database.execute("PRAGMA query_only = ON")
            database.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if tuple(database.execute(_SCHEMA_QUERY).fetchall()) != expected:
                raise BackupError("Snapshot SQL schema does not match registered migrations")
            if validate_schema(database, default_migrations_directory()) != schema_version:
                raise BackupError("Snapshot schema version is unsupported")
            if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("Snapshot integrity check failed")
            if database.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupError("Snapshot contains broken record references")
            unsupported = database.execute(
                """SELECT 1 FROM artifacts WHERE
                artifact_type != 'profile_import_source_digest'
                OR local_path IS NOT NULL OR original_name IS NOT NULL
                OR content_sha256 IS NULL OR uri IS NULL
                OR uri != 'sha256:' || content_sha256 LIMIT 1"""
            ).fetchone()
            if unsupported is not None:
                raise BackupError("Profile backup cannot omit referenced filesystem artifacts")
            if time.monotonic() > deadline:
                raise BackupError("Profile snapshot validation exceeded its time budget")
    except BackupError:
        raise
    except Exception:
        raise BackupError("Profile snapshot validation failed") from None
