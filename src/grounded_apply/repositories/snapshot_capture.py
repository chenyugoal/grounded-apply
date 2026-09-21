"""Guarded, bounded read-only capture without activating historical schemas.

The registered-profile entry point is preparation for explicit conversion, not
conversion admission: it verifies SQL structure and filesystem custody only.
Historical factual/material custody and active-workflow policy remain separate.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from grounded_apply.config import (
    RuntimePaths, prepare_sqlite_path, require_initialized_profile_storage,
    require_safe_sqlite_path,
)
from grounded_apply.repositories.snapshots import (
    _RESTORABLE_SCHEMA_VERSIONS, validate_profile_snapshot,
)
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES, SNAPSHOT_WORK_SECONDS


class SnapshotCaptureError(RuntimeError):
    """Capture refused without exposing source paths, SQL or profile content."""


@contextmanager
def _closing_connection(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Close owned connections without masking an original interruption."""
    try:
        yield connection
    except BaseException:
        try:
            connection.close()
        except Exception:
            pass
        raise
    else:
        connection.close()


def _capture_read_only_snapshot(
    connection: sqlite3.Connection,
    *,
    max_bytes: int,
    revalidate_storage: Callable[[], None],
) -> bytes:
    """Pin and copy a caller-owned query-only connection into private memory.

    The caller owns source opening, schema policy and filesystem checks. This
    helper owns only its destination and the read transaction it starts; it
    neither migrates nor reads application records. Deadlines are cooperative.
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise SnapshotCaptureError("Snapshot size limit must be a positive integer")
    try:
        if connection.in_transaction:
            raise SnapshotCaptureError("Snapshots require an idle read-only connection")
        revalidate_storage()
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise SnapshotCaptureError("Snapshots require an idle read-only connection")
        deadline = time.monotonic() + SNAPSHOT_WORK_SECONDS
        with _closing_connection(sqlite3.connect(":memory:", isolation_level=None)) as destination:
            started = False
            try:
                destination.execute("PRAGMA temp_store = MEMORY")
                started = True
                connection.execute("BEGIN")
                connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
                page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
                page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
                if page_size * page_count > max_bytes:
                    raise SnapshotCaptureError("Snapshot exceeds its supported size")

                def progress(status: int, remaining: int, total: int) -> None:
                    del status, remaining
                    if total * page_size > max_bytes or time.monotonic() > deadline:
                        raise SnapshotCaptureError("Snapshot exceeded its size or time budget")

                connection.backup(destination, pages=128, progress=progress, sleep=0.01)
                snapshot = destination.serialize()
                if len(snapshot) > max_bytes:
                    raise SnapshotCaptureError("Snapshot exceeds its supported size")
                if time.monotonic() > deadline:
                    raise SnapshotCaptureError("Snapshot exceeded its size or time budget")
                revalidate_storage()
                return snapshot
            finally:
                original_error = sys.exception()
                try:
                    if started and connection.in_transaction:
                        connection.execute("ROLLBACK")
                except Exception:
                    if original_error is None:
                        raise
    except SnapshotCaptureError:
        raise
    except Exception:
        raise SnapshotCaptureError("Profile snapshot capture failed") from None


def capture_registered_profile_snapshot(
    paths: RuntimePaths, *, allowed_source_versions: frozenset[int],
) -> bytes:
    """Capture an explicitly named supported profile without migration or writes.

    Exact registered SQL is checked on the captured image before application
    records are interpreted. No default runtime is resolved and no live profile
    is initialized. Repeated identity checks are sampled, not a same-UID lock.
    """
    if (type(allowed_source_versions) is not frozenset or not allowed_source_versions
            or any(type(version) is not int for version in allowed_source_versions)
            or not allowed_source_versions <= _RESTORABLE_SCHEMA_VERSIONS):
        raise SnapshotCaptureError("Unsupported snapshot source-version policy")
    if type(paths) is not RuntimePaths:
        raise SnapshotCaptureError("Explicit runtime paths are required for snapshot capture")
    try:
        deadline = time.monotonic() + SNAPSHOT_WORK_SECONDS
        roots = (paths.config_dir, paths.data_dir, paths.cache_dir, paths.state_dir)
        if paths.portable_root is not None:
            roots += (paths.portable_root,)
        if not all(path.is_absolute() for path in roots):
            raise SnapshotCaptureError("Explicit absolute runtime paths are required")
        require_initialized_profile_storage(paths, read_only=True)
        identity = prepare_sqlite_path(paths.database, existing_only=True, read_only=True)

        def revalidate_storage() -> None:
            require_initialized_profile_storage(paths, read_only=True)
            if require_safe_sqlite_path(paths.database, read_only=True) != identity:
                raise SnapshotCaptureError("Source database identity changed during capture")

        # Preserve lexical path meaning; resolving a symlink/.. path here would
        # change the target after the filesystem guards inspected it.
        target = f"{paths.database.as_uri()}?mode=ro"
        with _closing_connection(sqlite3.connect(target, isolation_level=None, uri=True)) as source:
            revalidate_storage()
            source.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            source.execute("PRAGMA trusted_schema = OFF")
            source.execute("PRAGMA query_only = ON")
            source.execute("PRAGMA temp_store = MEMORY")
            snapshot = _capture_read_only_snapshot(
                source, max_bytes=MAX_SNAPSHOT_BYTES, revalidate_storage=revalidate_storage,
            )
        validate_profile_snapshot(snapshot)
        if int.from_bytes(snapshot[60:64], "big") not in allowed_source_versions:
            raise SnapshotCaptureError("Captured schema is outside the source-version policy")
        revalidate_storage()
        if time.monotonic() > deadline:
            raise SnapshotCaptureError("Snapshot capture and validation exceeded their time budget")
        return snapshot
    except SnapshotCaptureError:
        raise
    except Exception:
        raise SnapshotCaptureError("Profile snapshot capture failed") from None
