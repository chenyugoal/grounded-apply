"""Dependency-free SQLite persistence for the first Grounded Apply slice.

The repository intentionally returns plain dictionaries whose keys match SQL
column names.  It does not import domain models: application-layer adapters can
decode ``*_json`` fields, enums, and timestamps without making persistence the
owner of truth policy.  Evidence-to-claim membership is normalized through
``claim_evidence``; an adapter that constructs the current one-claim domain
``Evidence`` record should join each evidence row with its claim link.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from hashlib import sha256
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from grounded_apply.config import (
    UnsafeRuntimePathError,
    prepare_sqlite_path,
    require_safe_sqlite_path,
)

from ._schema import (
    LATEST_SCHEMA_VERSION,
    FutureSchemaError,
    MigrationError,
    SchemaError,
    default_migrations_directory,
    initialize_schema,
    read_schema_version,
    utc_now,
    validate_schema,
)


type Record = dict[str, object]

_TERMINAL_WORKFLOW_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_STARTED_WORKFLOW_STATUSES = frozenset(
    {"running", "waiting_for_input", "succeeded", "failed"}
)
_DECIDED_MEMORY_STATUSES = frozenset({"approved", "rejected"})
_PROFILE_IMPORT_REVIEW_DECISIONS = frozenset({"approved", "rejected"})


class RepositoryError(RuntimeError):
    """Base error for repository lifecycle failures."""


class RepositoryClosedError(RepositoryError):
    """An operation was attempted after the repository was closed."""


class RepositoryNotInitializedError(RepositoryError):
    """A data operation was attempted before schema initialization."""


class RecordNotFoundError(RepositoryError, LookupError):
    """A requested repository record does not exist."""


class SQLiteRepository:
    """Own one SQLite connection and the transaction boundary around it.

    Call :meth:`initialize` before data access, or use the repository as a
    context manager.  Nested :meth:`transaction` blocks use savepoints.  All
    convenience mutation methods join an existing transaction, which makes
    multi-record operations such as a claim plus its evidence links atomic.
    """

    def __init__(
        self,
        database: str | os.PathLike[str],
        *,
        migrations_dir: str | os.PathLike[str] | None = None,
        timeout: float = 5.0,
        read_only: bool = False,
        existing_only: bool = False,
    ) -> None:
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        if not isinstance(read_only, bool):
            raise TypeError("read_only must be a boolean")
        if not isinstance(existing_only, bool):
            raise TypeError("existing_only must be a boolean")

        self._database = os.fspath(database)
        self._migrations_dir = Path(migrations_dir) if migrations_dir is not None else None
        self._read_only = read_only
        self._existing_only = existing_only or read_only
        if not self._database or self._database.startswith("file:"):
            raise ValueError("Repositories require a filesystem path or explicit :memory:")
        if self._existing_only and self._database == ":memory:":
            raise ValueError("existing repositories require a filesystem database path")
        self._database_path = (
            None if self._database == ":memory:" else Path(self._database).absolute()
        )
        self._database_identity: tuple[int, int] | None = None
        connection_target = self._database
        if self._database_path is not None:
            try:
                self._database_identity = prepare_sqlite_path(
                    self._database_path, existing_only=self._existing_only, read_only=read_only,
                )
            except FileNotFoundError as error:
                raise sqlite3.OperationalError("unable to open existing database") from error
            mode = "ro" if read_only else "rw"
            connection_target = f"{self._database_path.as_uri()}?mode={mode}"
        self._connection = sqlite3.connect(
            connection_target,
            timeout=timeout,
            isolation_level=None,
            uri=self._database_path is not None,
        )
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        self._initialized = False
        self._transaction_depth = 0
        self._savepoint_counter = 0

        try:
            self._validate_storage()
            timeout_ms = min(round(timeout * 1000), 2_147_483_647)
            self._connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
            self._connection.execute("PRAGMA foreign_keys = ON")
            foreign_keys = self._connection.execute("PRAGMA foreign_keys").fetchone()
            if foreign_keys is None or int(foreign_keys[0]) != 1:
                raise RepositoryError("SQLite foreign-key enforcement could not be enabled")
            if read_only:
                self._connection.execute("PRAGMA query_only = ON")
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def _validate_storage(self) -> None:
        if self._database_path is not None:
            identity = require_safe_sqlite_path(self._database_path, read_only=self._read_only)
            if identity != self._database_identity:
                raise UnsafeRuntimePathError("Database identity changed while SQLite was opening")

    @property
    def database(self) -> str:
        """Return the database target supplied at construction."""

        return self._database

    @property
    def schema_version(self) -> int:
        """Return the database's explicit schema version without mutating it."""

        self._ensure_open()
        return read_schema_version(self._connection)

    def initialize(self) -> Self:
        """Validate the schema, migrating only when creation is permitted.

        Databases from newer code and databases with inconsistent or edited
        migration history are rejected before migrations run. Read-only and
        existing-only repositories require the current version and never apply
        migrations.
        """

        from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES

        self._ensure_open()
        self._validate_storage()
        migrations_dir = self._migrations_dir or default_migrations_directory()
        version = (
            validate_schema(self._connection, migrations_dir)
            if self._existing_only
            else initialize_schema(self._connection, migrations_dir,
                                   max_database_bytes=MAX_SNAPSHOT_BYTES)
        )
        if version != LATEST_SCHEMA_VERSION:
            raise SchemaError(
                f"Expected schema version {LATEST_SCHEMA_VERSION}, found {version}"
            )
        self._validate_storage()
        self._initialized = True
        return self

    def close(self) -> None:
        """Roll back unfinished work and close the owned connection."""

        if self._closed:
            return
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")
        self._transaction_depth = 0
        self._connection.close()
        self._closed = True
        self._initialized = False

    def snapshot_bytes(self, *, max_bytes: int) -> bytes:
        """Capture a bounded consistent image without a plaintext staging file.

        Only a guarded read-only connection outside any transaction may capture
        a snapshot. A pinned read transaction keeps the size check and backup
        on the same database revision. Busy retries have a fixed deadline.
        """

        self._require_initialized()
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("Snapshot size limit must be a positive integer")
        if not self._read_only or self._connection.in_transaction:
            raise RepositoryError("Snapshots require an idle read-only repository")
        self._validate_storage()
        destination = sqlite3.connect(":memory:", isolation_level=None)
        deadline = time.monotonic() + 5.0

        def progress(status: int, remaining: int, total: int) -> None:
            del status, remaining
            if total * page_size > max_bytes or time.monotonic() > deadline:
                raise RepositoryError("Snapshot exceeded its size or time budget")

        try:
            destination.execute("PRAGMA temp_store = MEMORY")
            self._connection.execute("BEGIN")
            self._connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
            page_size = int(self._connection.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(self._connection.execute("PRAGMA page_count").fetchone()[0])
            if page_size * page_count > max_bytes:
                raise RepositoryError("Snapshot exceeds its supported size")
            self._connection.backup(destination, pages=128, progress=progress, sleep=0.01)
            snapshot = destination.serialize()
            if len(snapshot) > max_bytes:
                raise RepositoryError("Snapshot exceeds its supported size")
            self._validate_storage()
            return snapshot
        finally:
            try:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
            finally:
                destination.close()

    def __enter__(self) -> Self:
        try:
            return self.initialize()
        except BaseException:
            self.close()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[Self]:
        """Run mutations atomically, nesting with SQLite savepoints."""

        self._require_initialized()
        if self._read_only:
            raise RepositoryError("Cannot start a mutation transaction in read-only mode")
        outermost = self._transaction_depth == 0
        savepoint: str | None = None
        if outermost:
            self._connection.execute("BEGIN IMMEDIATE")
        else:
            self._savepoint_counter += 1
            savepoint = f"grounded_apply_{self._savepoint_counter}"
            self._connection.execute(f"SAVEPOINT {savepoint}")
        self._transaction_depth += 1

        try:
            yield self
        except BaseException:
            self._transaction_depth -= 1
            if outermost:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
            else:
                assert savepoint is not None
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                self._connection.execute("COMMIT")
            else:
                assert savepoint is not None
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    def add_artifact(
        self,
        *,
        artifact_type: str,
        uri: str | None = None,
        local_path: str | os.PathLike[str] | None = None,
        original_name: str | None = None,
        media_type: str | None = None,
        byte_size: int | None = None,
        content_sha256: str | None = None,
        sensitivity: str = "personal",
        metadata: object | None = None,
        captured_at: str | None = None,
        artifact_id: str | None = None,
        created_at: str | None = None,
    ) -> Record:
        """Insert and return an artifact metadata record."""

        now = created_at or utc_now()
        identifier = artifact_id or _new_id()
        return self._insert(
            "artifacts",
            {
                "id": identifier,
                "artifact_type": artifact_type,
                "uri": uri,
                "local_path": os.fspath(local_path) if local_path is not None else None,
                "original_name": original_name,
                "media_type": media_type,
                "byte_size": byte_size,
                "content_sha256": _normalize_hash(content_sha256),
                "sensitivity": sensitivity,
                "metadata_json": _json_text({} if metadata is None else metadata),
                "captured_at": captured_at or now,
                "created_at": now,
                "updated_at": now,
            },
        )

    def get_artifact(self, artifact_id: str) -> Record | None:
        return self._get_by_id("artifacts", artifact_id)

    def list_artifacts(
        self,
        *,
        artifact_type: str | None = None,
        sensitivity: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        filters = _without_none(
            {"artifact_type": artifact_type, "sensitivity": sensitivity}
        )
        return self._list(
            "artifacts",
            filters=filters,
            order_by="captured_at DESC, id",
            limit=limit,
            offset=offset,
        )

    def add_evidence(
        self,
        *,
        source_type: str,
        source_ref: str,
        artifact_id: str | None = None,
        locator: object | None = None,
        source_text: str | None = None,
        extraction_method: str = "manual",
        captured_at: str | None = None,
        checksum_sha256: str | None = None,
        confirmation_status: str = "pending",
        confirmed_at: str | None = None,
        confirmed_by: str | None = None,
        metadata: object | None = None,
        evidence_id: str | None = None,
        claim_id: str | None = None,
        relationship: str = "supports",
        strength: float = 1.0,
        created_at: str | None = None,
    ) -> Record:
        """Insert evidence and optionally link it to one claim atomically."""

        now = created_at or utc_now()
        identifier = evidence_id or _new_id()
        confirmation_time = confirmed_at
        if confirmation_status == "confirmed" and confirmation_time is None:
            confirmation_time = now

        with self.transaction():
            record = self._insert(
                "evidence",
                {
                    "id": identifier,
                    "artifact_id": artifact_id,
                    "locator_json": _json_text({} if locator is None else locator),
                    "source_text": source_text,
                    "extraction_method": extraction_method,
                    "captured_at": captured_at or now,
                    "checksum_sha256": _normalize_hash(checksum_sha256),
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "confirmation_status": confirmation_status,
                    "confirmed_at": confirmation_time,
                    "confirmed_by": confirmed_by,
                    "metadata_json": _json_text({} if metadata is None else metadata),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if claim_id is not None:
                self.link_claim_evidence(
                    claim_id,
                    identifier,
                    relationship=relationship,
                    strength=strength,
                    created_at=now,
                )
        return record

    def get_evidence(self, evidence_id: str) -> Record | None:
        return self._get_by_id("evidence", evidence_id)

    def list_evidence(
        self,
        *,
        artifact_id: str | None = None,
        source_type: str | None = None,
        confirmation_status: str | None = None,
        claim_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        if claim_id is None:
            filters = _without_none(
                {
                    "artifact_id": artifact_id,
                    "source_type": source_type,
                    "confirmation_status": confirmation_status,
                }
            )
            return self._list(
                "evidence",
                filters=filters,
                order_by="captured_at DESC, id",
                limit=limit,
                offset=offset,
            )

        self._require_initialized()
        conditions = ["ce.claim_id = ?"]
        parameters: list[object] = [claim_id]
        for column, value in (
            ("e.artifact_id", artifact_id),
            ("e.source_type", source_type),
            ("e.confirmation_status", confirmation_status),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        pagination, pagination_parameters = _pagination(limit, offset)
        rows = self._connection.execute(
            f"""
            SELECT e.*
            FROM evidence AS e
            JOIN claim_evidence AS ce ON ce.evidence_id = e.id
            WHERE {' AND '.join(conditions)}
            ORDER BY e.captured_at DESC, e.id
            {pagination}
            """,
            (*parameters, *pagination_parameters),
        ).fetchall()
        return [_row_record(row) for row in rows]

    def add_claim(
        self,
        *,
        claim_type: str,
        value: object,
        canonical_text: str,
        subject_type: str = "person",
        subject_id: str | None = None,
        status: str = "needs_review",
        approval_status: str = "pending",
        confidence: float = 1.0,
        sensitivity: str = "personal",
        scope_type: str = "global",
        scope_id: str | None = None,
        effective_from: str | None = None,
        effective_to: str | None = None,
        source_type: str = "other",
        source_ref: str | None = None,
        verified_at: str | None = None,
        verified_by: str | None = None,
        derivation_rule_name: str | None = None,
        derivation_rule_version: str | None = None,
        derivation_input_claim_ids: Sequence[str] = (),
        derivation_staleness_policy: str | None = None,
        derivation_calculated_at: str | None = None,
        supersedes_id: str | None = None,
        claim_id: str | None = None,
        evidence_ids: Sequence[str] = (),
        created_at: str | None = None,
    ) -> Record:
        """Insert one atomic claim, derivation inputs, and evidence links.

        A derived claim must supply a rule name, rule version, at least one
        existing input claim ID, and a staleness policy.  Non-derived claims
        must not supply derivation fields.
        """

        now = created_at or utc_now()
        identifier = claim_id or _new_id()
        if status == "verified" and verified_at is None:
            raise ValueError("verified claims require an explicit verified_at timestamp")

        derivation_values = (
            derivation_rule_name,
            derivation_rule_version,
            derivation_staleness_policy,
            derivation_calculated_at,
        )
        if status == "derived":
            if (
                not derivation_rule_name
                or not derivation_rule_version
                or not derivation_staleness_policy
                or not derivation_input_claim_ids
            ):
                raise ValueError(
                    "derived claims require rule name, rule version, staleness policy, "
                    "and input claim ids"
                )
        elif any(value is not None for value in derivation_values) or derivation_input_claim_ids:
            raise ValueError("derivation provenance is only valid for derived claims")

        input_claim_ids = tuple(derivation_input_claim_ids)
        if len(set(input_claim_ids)) != len(input_claim_ids):
            raise ValueError("derivation_input_claim_ids must be unique")
        linked_evidence_ids = tuple(evidence_ids)
        if len(set(linked_evidence_ids)) != len(linked_evidence_ids):
            raise ValueError("evidence_ids must be unique")

        with self.transaction():
            record = self._insert(
                "claims",
                {
                    "id": identifier,
                    "claim_type": claim_type,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "value_json": _json_text(value),
                    "canonical_text": canonical_text,
                    "status": status,
                    "approval_status": approval_status,
                    "confidence": confidence,
                    "sensitivity": sensitivity,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "verified_at": verified_at,
                    "verified_by": verified_by,
                    "derivation_rule_name": derivation_rule_name,
                    "derivation_rule_version": derivation_rule_version,
                    "derivation_staleness_policy": derivation_staleness_policy,
                    "derivation_calculated_at": (
                        derivation_calculated_at or now if status == "derived" else None
                    ),
                    "supersedes_id": supersedes_id,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            for input_order, input_claim_id in enumerate(input_claim_ids):
                self._connection.execute(
                    """
                    INSERT INTO claim_derivation_inputs (
                        derived_claim_id,
                        input_claim_id,
                        input_order
                    ) VALUES (?, ?, ?)
                    """,
                    (identifier, input_claim_id, input_order),
                )
            for evidence_id in linked_evidence_ids:
                self.link_claim_evidence(identifier, evidence_id, created_at=now)
        return record

    def get_claim(self, claim_id: str) -> Record | None:
        return self._get_by_id("claims", claim_id)

    def list_claims(
        self,
        *,
        claim_type: str | None = None,
        status: str | None = None,
        approval_status: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        filters = _without_none(
            {
                "claim_type": claim_type,
                "status": status,
                "approval_status": approval_status,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
            }
        )
        return self._list(
            "claims",
            filters=filters,
            order_by="created_at DESC, id",
            limit=limit,
            offset=offset,
        )

    def list_derivation_input_ids(self, claim_id: str) -> list[str]:
        """Return a derived claim's input IDs in deterministic rule order."""

        self._require_initialized()
        rows = self._connection.execute(
            """
            SELECT input_claim_id
            FROM claim_derivation_inputs
            WHERE derived_claim_id = ?
            ORDER BY input_order
            """,
            (claim_id,),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def link_claim_evidence(
        self,
        claim_id: str,
        evidence_id: str,
        *,
        relationship: str = "supports",
        strength: float = 1.0,
        note: str | None = None,
        created_at: str | None = None,
    ) -> Record:
        """Create or idempotently refresh one claim/evidence relationship."""

        now = created_at or utc_now()
        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO claim_evidence (
                    claim_id,
                    evidence_id,
                    relationship,
                    strength,
                    note,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (claim_id, evidence_id, relationship)
                DO UPDATE SET strength = excluded.strength, note = excluded.note
                """,
                (claim_id, evidence_id, relationship, strength, note, now),
            )
        record = self.get_claim_evidence(claim_id, evidence_id, relationship=relationship)
        assert record is not None
        return record

    def get_claim_evidence(
        self,
        claim_id: str,
        evidence_id: str,
        *,
        relationship: str = "supports",
    ) -> Record | None:
        self._require_initialized()
        row = self._connection.execute(
            """
            SELECT *
            FROM claim_evidence
            WHERE claim_id = ? AND evidence_id = ? AND relationship = ?
            """,
            (claim_id, evidence_id, relationship),
        ).fetchone()
        return None if row is None else _row_record(row)

    def list_claim_evidence(
        self,
        *,
        claim_id: str | None = None,
        evidence_id: str | None = None,
        relationship: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        filters = _without_none(
            {
                "claim_id": claim_id,
                "evidence_id": evidence_id,
                "relationship": relationship,
            }
        )
        return self._list(
            "claim_evidence",
            filters=filters,
            order_by="created_at, claim_id, evidence_id",
            limit=limit,
            offset=offset,
        )

    def add_profile_import_review_item(
        self,
        *,
        import_workflow_run_id: str,
        proposal_index: int,
        claim_id: str,
        evidence_id: str,
        record_sha256: str,
        created_at: str | None = None,
    ) -> Record:
        """Bind one imported claim/evidence pair to its immutable review slot.

        Repeating the exact association is safe, including after it has been
        decided. A conflicting slot, claim, evidence, or record digest fails
        without changing the existing association.
        """

        if isinstance(proposal_index, bool) or not isinstance(proposal_index, int):
            raise TypeError("proposal_index must be an integer")
        if proposal_index < 0:
            raise ValueError("proposal_index must not be negative")
        if not isinstance(record_sha256, str):
            raise TypeError("record_sha256 must be text")
        if (
            len(record_sha256) != 64
            or record_sha256 != record_sha256.lower()
            or any(character not in "0123456789abcdef" for character in record_sha256)
        ):
            raise ValueError("record_sha256 must be a lowercase SHA-256 digest")
        now = created_at or utc_now()

        with self.transaction():
            existing = self.get_profile_import_review_item(claim_id)
            if existing is not None:
                if (
                    existing["import_workflow_run_id"] == import_workflow_run_id
                    and existing["proposal_index"] == proposal_index
                    and existing["evidence_id"] == evidence_id
                    and existing["record_sha256"] == record_sha256
                ):
                    if existing["decision"] is None:
                        self._require_pending_profile_import_review_item(existing)
                        self._require_pending_profile_import_review_projection(
                            claim_id,
                            evidence_id,
                        )
                    else:
                        self._require_decided_profile_import_review_projection(existing)
                    return existing
                raise RepositoryError(
                    "Profile import review claim is already bound to another record"
                )

            occupied_slot = self._get_profile_import_review_slot(
                import_workflow_run_id,
                proposal_index,
            )
            if occupied_slot is not None:
                raise RepositoryError("Profile import review slot is already occupied")
            evidence_binding = self._connection.execute(
                """
                SELECT 1
                FROM profile_import_review_items
                WHERE evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
            if evidence_binding is not None:
                raise RepositoryError(
                    "Profile import review evidence is already bound to another record"
                )

            self._require_pending_profile_import_review_projection(claim_id, evidence_id)
            self._connection.execute(
                """
                INSERT INTO profile_import_review_items (
                    import_workflow_run_id,
                    proposal_index,
                    claim_id,
                    evidence_id,
                    record_sha256,
                    decision,
                    decision_workflow_run_id,
                    decided_by,
                    decided_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    import_workflow_run_id,
                    proposal_index,
                    claim_id,
                    evidence_id,
                    record_sha256,
                    now,
                    now,
                ),
            )

        record = self.get_profile_import_review_item(claim_id)
        assert record is not None
        return record

    def get_profile_import_review_item(self, claim_id: str) -> Record | None:
        """Return the durable import-review association for one claim."""

        self._require_initialized()
        row = self._connection.execute(
            """
            SELECT *
            FROM profile_import_review_items
            WHERE claim_id = ?
            """,
            (claim_id,),
        ).fetchone()
        return None if row is None else _row_record(row)

    def list_profile_import_review_items(
        self,
        *,
        import_workflow_run_id: str | None = None,
        decision: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        """List review associations in stable import/proposal order."""

        if decision is not None and not isinstance(decision, str):
            raise TypeError("decision must be text or null")
        if decision is not None and decision not in _PROFILE_IMPORT_REVIEW_DECISIONS:
            expected = ", ".join(sorted(_PROFILE_IMPORT_REVIEW_DECISIONS))
            raise ValueError(f"decision must be one of: {expected}")
        filters = _without_none(
            {
                "import_workflow_run_id": import_workflow_run_id,
                "decision": decision,
            }
        )
        return self._list(
            "profile_import_review_items",
            filters=filters,
            order_by="created_at, import_workflow_run_id, proposal_index",
            limit=limit,
            offset=offset,
        )

    def decide_profile_import_review_item(
        self,
        claim_id: str,
        *,
        decision: str,
        decision_workflow_run_id: str,
        decided_by: str,
        decided_at: str,
    ) -> Record:
        """Atomically apply one terminal import-review trust transition.

        The method is intentionally narrower than generic claim/evidence update
        APIs. It accepts only the two legal pending transitions and uses guarded
        updates so concurrent or malformed state fails closed.
        """

        if not isinstance(decision, str):
            raise TypeError("decision must be text")
        if decision not in _PROFILE_IMPORT_REVIEW_DECISIONS:
            expected = ", ".join(sorted(_PROFILE_IMPORT_REVIEW_DECISIONS))
            raise ValueError(f"decision must be one of: {expected}")
        for field_name, value in (
            ("decision_workflow_run_id", decision_workflow_run_id),
            ("decided_by", decided_by),
            ("decided_at", decided_at),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be text")
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")

        with self.transaction():
            review_item = self.get_profile_import_review_item(claim_id)
            if review_item is None:
                raise RecordNotFoundError(
                    f"Profile import review item does not exist: {claim_id}"
                )
            current_decision = review_item["decision"]
            if current_decision is not None:
                if (
                    current_decision == decision
                    and review_item["decision_workflow_run_id"]
                    == decision_workflow_run_id
                    and review_item["decided_by"] == decided_by
                    and review_item["decided_at"] == decided_at
                ):
                    self._require_decided_profile_import_review_projection(review_item)
                    return review_item
                raise RepositoryError(
                    f"Profile import review item is already decided: {claim_id}"
                )

            evidence_id = review_item["evidence_id"]
            if not isinstance(evidence_id, str):
                raise RepositoryError("Profile import review association is malformed")
            self._require_pending_profile_import_review_item(review_item)
            self._require_pending_profile_import_review_projection(claim_id, evidence_id)

            if decision == "approved":
                claim_status = "verified"
                approval_status = "approved"
                evidence_status = "confirmed"
                verified_at: str | None = decided_at
                verified_by: str | None = decided_by
                confirmed_at: str | None = decided_at
                confirmed_by: str | None = decided_by
            else:
                claim_status = "withdrawn"
                approval_status = "rejected"
                evidence_status = "rejected"
                verified_at = None
                verified_by = None
                confirmed_at = None
                confirmed_by = None

            claim_cursor = self._connection.execute(
                """
                UPDATE claims
                SET status = ?,
                    approval_status = ?,
                    verified_at = ?,
                    verified_by = ?,
                    updated_at = ?
                WHERE id = ?
                  AND source_type = 'imported_resume'
                  AND status = 'needs_review'
                  AND approval_status = 'pending'
                  AND verified_at IS NULL
                  AND verified_by IS NULL
                """,
                (
                    claim_status,
                    approval_status,
                    verified_at,
                    verified_by,
                    decided_at,
                    claim_id,
                ),
            )
            if claim_cursor.rowcount != 1:
                raise RepositoryError("Profile import claim is not pending review")

            evidence_cursor = self._connection.execute(
                """
                UPDATE evidence
                SET confirmation_status = ?,
                    confirmed_at = ?,
                    confirmed_by = ?,
                    updated_at = ?
                WHERE id = ?
                  AND source_type = 'imported_resume'
                  AND confirmation_status = 'pending'
                  AND confirmed_at IS NULL
                  AND confirmed_by IS NULL
                """,
                (
                    evidence_status,
                    confirmed_at,
                    confirmed_by,
                    decided_at,
                    evidence_id,
                ),
            )
            if evidence_cursor.rowcount != 1:
                raise RepositoryError("Profile import evidence is not pending review")

            review_cursor = self._connection.execute(
                """
                UPDATE profile_import_review_items
                SET decision = ?,
                    decision_workflow_run_id = ?,
                    decided_by = ?,
                    decided_at = ?,
                    updated_at = ?
                WHERE claim_id = ?
                  AND decision IS NULL
                  AND decision_workflow_run_id IS NULL
                  AND decided_by IS NULL
                  AND decided_at IS NULL
                """,
                (
                    decision,
                    decision_workflow_run_id,
                    decided_by,
                    decided_at,
                    decided_at,
                    claim_id,
                ),
            )
            if review_cursor.rowcount != 1:
                raise RepositoryError("Profile import review item is not pending")

            decided = self.get_profile_import_review_item(claim_id)
            assert decided is not None
            self._require_decided_profile_import_review_projection(decided)
            return decided

    def _get_profile_import_review_slot(
        self,
        import_workflow_run_id: str,
        proposal_index: int,
    ) -> Record | None:
        self._require_initialized()
        row = self._connection.execute(
            """
            SELECT *
            FROM profile_import_review_items
            WHERE import_workflow_run_id = ? AND proposal_index = ?
            """,
            (import_workflow_run_id, proposal_index),
        ).fetchone()
        return None if row is None else _row_record(row)

    def _require_profile_import_review_support_link(
        self,
        claim_id: str,
        evidence_id: str,
    ) -> None:
        claim_links = self.list_claim_evidence(claim_id=claim_id)
        evidence_links = self.list_claim_evidence(evidence_id=evidence_id)
        expected = {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "relationship": "supports",
            "strength": 1.0,
            "note": None,
        }
        if len(claim_links) != 1 or len(evidence_links) != 1:
            raise RepositoryError("Profile import review support link is invalid")
        for link in (claim_links[0], evidence_links[0]):
            if any(link.get(field) != value for field, value in expected.items()):
                raise RepositoryError("Profile import review support link is invalid")

    def _require_pending_profile_import_review_projection(
        self,
        claim_id: str,
        evidence_id: str,
    ) -> None:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise RecordNotFoundError(f"Claim does not exist: {claim_id}")
        evidence = self.get_evidence(evidence_id)
        if evidence is None:
            raise RecordNotFoundError(f"Evidence does not exist: {evidence_id}")
        if (
            claim.get("source_type") != "imported_resume"
            or claim.get("status") != "needs_review"
            or claim.get("approval_status") != "pending"
            or claim.get("verified_at") is not None
            or claim.get("verified_by") is not None
            or claim.get("updated_at") != claim.get("created_at")
        ):
            raise RepositoryError("Profile import claim is not pending review")
        if (
            evidence.get("source_type") != "imported_resume"
            or evidence.get("confirmation_status") != "pending"
            or evidence.get("confirmed_at") is not None
            or evidence.get("confirmed_by") is not None
            or evidence.get("updated_at") != evidence.get("created_at")
        ):
            raise RepositoryError("Profile import evidence is not pending review")
        self._require_profile_import_review_support_link(claim_id, evidence_id)

    @staticmethod
    def _require_pending_profile_import_review_item(review_item: Record) -> None:
        if (
            review_item.get("decision") is not None
            or review_item.get("decision_workflow_run_id") is not None
            or review_item.get("decided_by") is not None
            or review_item.get("decided_at") is not None
            or review_item.get("updated_at") != review_item.get("created_at")
        ):
            raise RepositoryError("Profile import review association is not pending")

    def _require_decided_profile_import_review_projection(
        self,
        review_item: Record,
    ) -> None:
        claim_id = review_item.get("claim_id")
        evidence_id = review_item.get("evidence_id")
        decision = review_item.get("decision")
        decision_workflow_run_id = review_item.get("decision_workflow_run_id")
        decided_by = review_item.get("decided_by")
        decided_at = review_item.get("decided_at")
        if (
            not isinstance(claim_id, str)
            or not isinstance(evidence_id, str)
            or decision not in _PROFILE_IMPORT_REVIEW_DECISIONS
            or not isinstance(decision_workflow_run_id, str)
            or not decision_workflow_run_id.strip()
            or not isinstance(decided_by, str)
            or not decided_by.strip()
            or not isinstance(decided_at, str)
            or not decided_at.strip()
            or review_item.get("updated_at") != decided_at
        ):
            raise RepositoryError("Profile import review decision is malformed")
        claim = self.get_claim(claim_id)
        evidence = self.get_evidence(evidence_id)
        if claim is None or evidence is None:
            raise RepositoryError("Profile import review decision projection is missing")
        if decision == "approved":
            claim_projection = (
                claim.get("status") == "verified"
                and claim.get("approval_status") == "approved"
                and claim.get("verified_at") == decided_at
                and claim.get("verified_by") == decided_by
                and claim.get("updated_at") == decided_at
            )
            evidence_projection = (
                evidence.get("confirmation_status") == "confirmed"
                and evidence.get("confirmed_at") == decided_at
                and evidence.get("confirmed_by") == decided_by
                and evidence.get("updated_at") == decided_at
            )
        else:
            claim_projection = (
                claim.get("status") == "withdrawn"
                and claim.get("approval_status") == "rejected"
                and claim.get("verified_at") is None
                and claim.get("verified_by") is None
                and claim.get("updated_at") == decided_at
            )
            evidence_projection = (
                evidence.get("confirmation_status") == "rejected"
                and evidence.get("confirmed_at") is None
                and evidence.get("confirmed_by") is None
                and evidence.get("updated_at") == decided_at
            )
        if not claim_projection or not evidence_projection:
            raise RepositoryError("Profile import review decision projection is inconsistent")
        self._require_profile_import_review_support_link(claim_id, evidence_id)

    def add_memory_proposal(
        self,
        *,
        proposal_type: str,
        proposal: object,
        status: str = "pending",
        question_intent: str | None = None,
        verbatim_answer: str | None = None,
        proposed_scope_type: str | None = None,
        proposed_scope_id: str | None = None,
        proposed_sensitivity: str | None = None,
        retention_policy: str | None = None,
        reuse_preview: str | None = None,
        contradictions: object | None = None,
        source_workflow_run_id: str | None = None,
        source_evidence_id: str | None = None,
        resulting_claim_id: str | None = None,
        decision_note: str | None = None,
        decided_at: str | None = None,
        proposal_id: str | None = None,
        created_at: str | None = None,
    ) -> Record:
        """Insert one reviewable memory proposal as a lossless JSON payload."""

        now = created_at or utc_now()
        decision_time = decided_at
        if status in _DECIDED_MEMORY_STATUSES and decision_time is None:
            decision_time = now
        return self._insert(
            "memory_proposals",
            {
                "id": proposal_id or _new_id(),
                "proposal_type": proposal_type,
                "status": status,
                "question_intent": question_intent,
                "verbatim_answer": verbatim_answer,
                "proposal_json": _json_text(proposal),
                "proposed_scope_type": proposed_scope_type,
                "proposed_scope_id": proposed_scope_id,
                "proposed_sensitivity": proposed_sensitivity,
                "retention_policy": retention_policy,
                "reuse_preview": reuse_preview,
                "contradictions_json": _json_text(
                    [] if contradictions is None else contradictions
                ),
                "source_workflow_run_id": source_workflow_run_id,
                "source_evidence_id": source_evidence_id,
                "resulting_claim_id": resulting_claim_id,
                "decision_note": decision_note,
                "decided_at": decision_time,
                "created_at": now,
                "updated_at": now,
            },
        )

    def get_memory_proposal(self, proposal_id: str) -> Record | None:
        return self._get_by_id("memory_proposals", proposal_id)

    def list_memory_proposals(
        self,
        *,
        status: str | None = None,
        proposal_type: str | None = None,
        source_workflow_run_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        filters = _without_none(
            {
                "status": status,
                "proposal_type": proposal_type,
                "source_workflow_run_id": source_workflow_run_id,
            }
        )
        return self._list(
            "memory_proposals",
            filters=filters,
            order_by="created_at DESC, id",
            limit=limit,
            offset=offset,
        )

    def decide_memory_proposal(
        self,
        proposal_id: str,
        *,
        status: str,
        resulting_claim_id: str | None = None,
        decision_note: str | None = None,
        decided_at: str | None = None,
    ) -> Record:
        """Record an approve/reject decision; repeated identical calls are safe."""

        if status not in _DECIDED_MEMORY_STATUSES:
            expected = ", ".join(sorted(_DECIDED_MEMORY_STATUSES))
            raise ValueError(f"decision status must be one of: {expected}")
        current = self.get_memory_proposal(proposal_id)
        if current is None:
            raise RecordNotFoundError(f"Memory proposal does not exist: {proposal_id}")
        if current["status"] in _DECIDED_MEMORY_STATUSES:
            if (
                current["status"] == status
                and current["resulting_claim_id"] == resulting_claim_id
                and current["decision_note"] == decision_note
            ):
                return current
            raise RepositoryError(f"Memory proposal is already decided: {proposal_id}")

        now = utc_now()
        values: dict[str, object] = {
            "status": status,
            "resulting_claim_id": resulting_claim_id if status == "approved" else None,
            "decision_note": decision_note,
            "decided_at": decided_at or now,
            "updated_at": now,
        }
        self._update_by_id("memory_proposals", proposal_id, values)
        record = self.get_memory_proposal(proposal_id)
        assert record is not None
        return record

    def add_workflow_run(
        self,
        *,
        workflow_type: str,
        status: str = "queued",
        idempotency_key: str | None = None,
        input_hash_sha256: str | None = None,
        input_data: object | None = None,
        current_step: str | None = None,
        completed_steps: object | None = None,
        generated_artifacts: object | None = None,
        outstanding_need_info: object | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
        retry_policy: object | None = None,
        failure_code: str | None = None,
        failure_reason: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        run_id: str | None = None,
        created_at: str | None = None,
    ) -> Record:
        """Insert one resumable workflow run."""

        now = created_at or utc_now()
        normalized_input = _json_text({} if input_data is None else input_data)
        normalized_hash = _normalize_hash(input_hash_sha256) or sha256(
            normalized_input.encode("utf-8")
        ).hexdigest()
        start_time = started_at
        finish_time = finished_at
        if status in _STARTED_WORKFLOW_STATUSES and start_time is None:
            start_time = now
        if status in _TERMINAL_WORKFLOW_STATUSES and finish_time is None:
            finish_time = now
        with self.transaction():
            if idempotency_key is not None:
                existing = self.get_workflow_run_by_idempotency_key(
                    workflow_type,
                    idempotency_key,
                )
                if existing is not None:
                    if (
                        existing["input_hash_sha256"] == normalized_hash
                        and existing["input_json"] == normalized_input
                    ):
                        return existing
                    raise RepositoryError(
                        "Workflow idempotency key was already used with different input: "
                        f"{workflow_type}/{idempotency_key}"
                    )

            return self._insert(
                "workflow_runs",
                {
                    "id": run_id or _new_id(),
                    "workflow_type": workflow_type,
                    "status": status,
                    "idempotency_key": idempotency_key,
                    "input_hash_sha256": normalized_hash,
                    "input_json": normalized_input,
                    "current_step": current_step,
                    "completed_steps_json": _json_text(
                        [] if completed_steps is None else completed_steps
                    ),
                    "generated_artifacts_json": _json_text(
                        [] if generated_artifacts is None else generated_artifacts
                    ),
                    "outstanding_need_info_json": _json_text(
                        [] if outstanding_need_info is None else outstanding_need_info
                    ),
                    "model_name": model_name,
                    "prompt_version": prompt_version,
                    "retry_policy_json": _json_text(
                        {} if retry_policy is None else retry_policy
                    ),
                    "failure_code": failure_code,
                    "failure_reason": failure_reason,
                    "started_at": start_time,
                    "finished_at": finish_time,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    def get_workflow_run(self, run_id: str) -> Record | None:
        return self._get_by_id("workflow_runs", run_id)

    def get_workflow_run_by_idempotency_key(
        self,
        workflow_type: str,
        idempotency_key: str,
    ) -> Record | None:
        self._require_initialized()
        row = self._connection.execute(
            """
            SELECT *
            FROM workflow_runs
            WHERE workflow_type = ? AND idempotency_key = ?
            """,
            (workflow_type, idempotency_key),
        ).fetchone()
        return None if row is None else _row_record(row)

    def list_workflow_runs(
        self,
        *,
        workflow_type: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        filters = _without_none({"workflow_type": workflow_type, "status": status})
        return self._list(
            "workflow_runs",
            filters=filters,
            order_by="updated_at DESC, id",
            limit=limit,
            offset=offset,
        )

    def update_workflow_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        completed_steps: object | None = None,
        generated_artifacts: object | None = None,
        outstanding_need_info: object | None = None,
        failure_code: str | None = None,
        failure_reason: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> Record:
        """Persist a workflow checkpoint so a later process can resume it.

        ``None`` means "leave unchanged" for optional update arguments.  Empty
        lists or dictionaries can be supplied to clear JSON checkpoint fields.
        """

        current = self.get_workflow_run(run_id)
        if current is None:
            raise RecordNotFoundError(f"Workflow run does not exist: {run_id}")

        now = max(utc_now(), current["updated_at"], finished_at or current["created_at"])
        values: dict[str, object] = {"updated_at": now}
        if status is not None:
            values["status"] = status
            if status in _STARTED_WORKFLOW_STATUSES and current["started_at"] is None:
                values["started_at"] = started_at or now
            elif started_at is not None:
                values["started_at"] = started_at
            if status in _TERMINAL_WORKFLOW_STATUSES:
                values["finished_at"] = finished_at or now
            elif finished_at is not None:
                values["finished_at"] = finished_at
        elif started_at is not None:
            values["started_at"] = started_at
        elif finished_at is not None:
            values["finished_at"] = finished_at
        if current_step is not None:
            values["current_step"] = current_step
        if completed_steps is not None:
            values["completed_steps_json"] = _json_text(completed_steps)
        if generated_artifacts is not None:
            values["generated_artifacts_json"] = _json_text(generated_artifacts)
        if outstanding_need_info is not None:
            values["outstanding_need_info_json"] = _json_text(outstanding_need_info)
        if failure_code is not None:
            values["failure_code"] = failure_code
        if failure_reason is not None:
            values["failure_reason"] = failure_reason

        self._update_by_id("workflow_runs", run_id, values)
        record = self.get_workflow_run(run_id)
        assert record is not None
        return record

    def insert_job_snapshot(self, *, job_id: str, source_url: str, source_text: str,
                            source_sha256: str, extractor_version: str, captured_at: str,
                            workflow_run_id: str) -> None:
        self._insert("job_snapshots", {"id": job_id, "source_url": source_url,
            "source_text": source_text, "source_sha256": source_sha256,
            "extractor_version": extractor_version, "captured_at": captured_at,
            "workflow_run_id": workflow_run_id})

    def insert_job_requirement(self, *, requirement_id: str, job_id: str, position: int,
                               start_offset: int, end_offset: int, quoted_text: str,
                               category: str, classification_basis: str) -> None:
        self._insert("job_requirements", {"id": requirement_id, "job_id": job_id,
            "position": position, "start_offset": start_offset, "end_offset": end_offset,
            "quoted_text": quoted_text, "category": category, "classification_basis": classification_basis})

    def get_job_snapshot(self, job_id: str) -> Record | None:
        return self._get_by_id("job_snapshots", job_id)

    def list_job_snapshots(self) -> list[Record]:
        self._require_initialized()
        return [_row_record(r) for r in self._connection.execute("SELECT * FROM job_snapshots ORDER BY captured_at, id")]

    def database_size_bytes(self) -> int:
        """Return allocated SQLite pages, including writes in the current transaction.

        Call inside a mutation transaction to roll back additions that exceed a
        storage budget. This does not shrink existing storage or enforce a global
        limit on unrelated application operations.
        """
        self._require_initialized()
        pages = self._connection.execute("PRAGMA page_count").fetchone()[0]
        size = self._connection.execute("PRAGMA page_size").fetchone()[0]
        return int(pages) * int(size)

    def list_job_requirements(self, job_id: str) -> list[Record]:
        self._require_initialized()
        return [_row_record(r) for r in self._connection.execute(
            "SELECT * FROM job_requirements WHERE job_id = ? ORDER BY position", (job_id,))]

    def insert_material_version(self, *, material_id: str, job_id: str, structure: object,
                                manifest: object, validation: object, pdf: bytes, latex: str,
                                extracted_text: str, bundle_sha256: str, created_at: str,
                                workflow_run_id: str, claim_ids: Sequence[str]) -> None:
        with self.transaction():
            self._insert("material_versions", {"id": material_id, "job_id": job_id,
                "structure_json": _json_text(structure), "manifest_json": _json_text(manifest),
                "validation_json": _json_text(validation), "pdf_bytes": pdf, "latex_text": latex,
                "extracted_text": extracted_text, "bundle_sha256": bundle_sha256,
                "created_at": created_at, "workflow_run_id": workflow_run_id})
            for claim_id in claim_ids:
                self._connection.execute("INSERT INTO material_claims VALUES (?, ?)", (material_id, claim_id))

    def get_material_version(self, material_id: str) -> Record | None:
        return self._get_by_id("material_versions", material_id)

    def list_material_ids(self, job_id: str | None = None) -> tuple[str, ...]:
        self._require_initialized()
        return tuple(r[0] for r in self._connection.execute(
            "SELECT id FROM material_versions WHERE (? IS NULL OR job_id = ?) ORDER BY created_at, id",
            (job_id, job_id)))

    def list_material_claim_ids(self, material_id: str) -> tuple[str, ...]:
        self._require_initialized()
        return tuple(r[0] for r in self._connection.execute(
            "SELECT claim_id FROM material_claims WHERE material_id = ? ORDER BY claim_id", (material_id,)))

    def get_material_approval(self, material_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM material_approvals WHERE material_id = ?", (material_id,)).fetchone()
        return None if row is None else _row_record(row)

    def insert_material_approval(self, *, material_id: str, bundle_sha256: str,
                                 actor_id: str, approved_at: str, workflow_run_id: str) -> None:
        self._require_initialized()
        self._connection.execute("INSERT INTO material_approvals VALUES (?, ?, ?, ?, ?)",
            (material_id, bundle_sha256, actor_id, approved_at, workflow_run_id))

    def insert_application(self, *, application_id: str, job_id: str, created_at: str, workflow_run_id: str) -> None:
        self._insert("applications", {"id": application_id, "job_id": job_id, "created_at": created_at, "workflow_run_id": workflow_run_id})

    def get_application(self, application_id: str) -> Record | None:
        return self._get_by_id("applications", application_id)

    def list_applications(self) -> list[Record]:
        self._require_initialized()
        return [_row_record(r) for r in self._connection.execute("SELECT * FROM applications ORDER BY created_at, id")]

    def insert_application_event(self, *, event_id: str, application_id: str, position: int, state: str,
                                  actor_id: str, at: str, previous_sha256: str | None, event_sha256: str,
                                  payload: object, workflow_run_id: str) -> None:
        self._insert("application_events", {"id": event_id, "application_id": application_id,
            "position": position, "state": state, "actor_id": actor_id, "at": at,
            "previous_sha256": previous_sha256, "event_sha256": event_sha256,
            "payload_json": _json_text(payload), "workflow_run_id": workflow_run_id})

    def list_application_events(self, application_id: str) -> list[Record]:
        self._require_initialized()
        return [_row_record(r) for r in self._connection.execute(
            "SELECT * FROM application_events WHERE application_id = ? ORDER BY position", (application_id,))]

    def insert_submission_snapshot(self, *, application_id: str, event_id: str, material_id: str,
                                   snapshot: object, snapshot_sha256: str, submitted_at: str) -> None:
        self._require_initialized()
        self._connection.execute("INSERT INTO submission_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (application_id, event_id, material_id, _json_text(snapshot), snapshot_sha256, submitted_at))

    def get_submission_snapshot(self, application_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM submission_snapshots WHERE application_id = ?", (application_id,)).fetchone()
        return None if row is None else _row_record(row)

    def support_counts(self) -> dict[str, int]:
        """Only fixed table counts; no values, IDs, paths, or source text."""
        self._require_initialized()
        tables = ("claims", "evidence", "claim_retirements", "job_snapshots", "material_versions", "applications", "application_events")
        return {table: int(self._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}

    def insert_preparation_batch(self, *, batch_id: str, manifest: object,
                                 manifest_sha256: str, created_at: str,
                                 workflow_run_id: str) -> None:
        self._insert("preparation_batches", {"id": batch_id, "manifest_json": _json_text(manifest),
            "manifest_sha256": manifest_sha256, "created_at": created_at,
            "workflow_run_id": workflow_run_id})
        self._connection.execute("INSERT INTO preparation_batch_leases (batch_id) VALUES (?)", (batch_id,))

    def insert_saved_search(self, *, search_id: str, manifest: object, manifest_sha256: str,
                            created_at: str, workflow_run_id: str) -> None:
        self._insert("saved_searches", {"id": search_id, "manifest_json": _json_text(manifest),
            "manifest_sha256": manifest_sha256, "created_at": created_at, "workflow_run_id": workflow_run_id})
        self._connection.execute("INSERT INTO search_leases (search_id) VALUES (?)", (search_id,))

    def insert_daily_schedule(self, *, schedule_id: str, search_id: str, manifest: object,
                               manifest_sha256: str, search_manifest_sha256: str, created_at: str, workflow_run_id: str) -> None:
        self._insert("daily_schedules", {"id": schedule_id, "search_id": search_id,
            "manifest_json": _json_text(manifest), "manifest_sha256": manifest_sha256,
            "search_manifest_sha256": search_manifest_sha256, "created_at": created_at, "workflow_run_id": workflow_run_id})
        self._connection.execute("INSERT INTO schedule_leases (schedule_id) VALUES (?)", (schedule_id,))

    def get_daily_schedule(self, schedule_id: str) -> Record | None:
        return self._get_by_id("daily_schedules", schedule_id)

    def list_daily_schedules(self) -> list[Record]:
        return self._list("daily_schedules", filters={}, order_by="created_at, id", limit=None, offset=0)

    def insert_schedule_occurrence(self, *, occurrence_id: str, schedule_id: str, local_date: str,
                                    due_at: str, created_at: str, record_sha256: str) -> None:
        self._insert("schedule_occurrences", {"id": occurrence_id, "schedule_id": schedule_id, "local_date": local_date,
            "due_at": due_at, "created_at": created_at, "record_sha256": record_sha256})

    def get_schedule_occurrence(self, occurrence_id: str) -> Record | None:
        return self._get_by_id("schedule_occurrences", occurrence_id)

    def list_schedule_occurrences(self, schedule_id: str) -> list[Record]:
        return self._list("schedule_occurrences", filters={"schedule_id": schedule_id}, order_by="local_date, id", limit=None, offset=0)

    def insert_schedule_event(self, *, event_id: str, schedule_id: str, position: int, at: str, action: str,
                               state: object, workflow_run_id: str | None, previous_sha256: str | None, event_sha256: str) -> None:
        self._insert("schedule_events", {"id": event_id, "schedule_id": schedule_id, "position": position, "at": at,
            "action": action, "state_json": _json_text(state), "workflow_run_id": workflow_run_id,
            "previous_sha256": previous_sha256, "event_sha256": event_sha256})

    def list_schedule_events(self, schedule_id: str) -> list[Record]:
        return self._list("schedule_events", filters={"schedule_id": schedule_id}, order_by="position", limit=None, offset=0)

    def insert_occurrence_event(self, *, event_id: str, occurrence_id: str, position: int, at: str, action: str,
                                 state: object, previous_sha256: str | None, event_sha256: str) -> None:
        self._insert("occurrence_events", {"id": event_id, "occurrence_id": occurrence_id, "position": position,
            "at": at, "action": action, "state_json": _json_text(state), "previous_sha256": previous_sha256, "event_sha256": event_sha256})

    def list_occurrence_events(self, occurrence_id: str) -> list[Record]:
        return self._list("occurrence_events", filters={"occurrence_id": occurrence_id}, order_by="position", limit=None, offset=0)

    def get_schedule_lease(self, schedule_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM schedule_leases WHERE schedule_id = ?", (schedule_id,)).fetchone()
        return None if row is None else _row_record(row)

    def acquire_schedule_lease(self, schedule_id: str, *, occurrence_id: str, owner: str,
                                 expected_epoch: int, now: str, expires_at: str) -> int | None:
        self._require_initialized()
        changed = self._connection.execute("""UPDATE schedule_leases SET occurrence_id = ?, owner = ?, expires_at = ?, epoch = epoch + 1
            WHERE schedule_id = ? AND epoch = ? AND (owner IS NULL OR expires_at <= ?)""",
            (occurrence_id, owner, expires_at, schedule_id, expected_epoch, now)).rowcount
        return expected_epoch + 1 if changed == 1 else None

    def revoke_schedule_lease(self, schedule_id: str) -> None:
        """Fence an interrupted owner even if the schedule is immediately enabled again."""
        self._require_initialized()
        self._connection.execute("""UPDATE schedule_leases SET occurrence_id = NULL, owner = NULL,
            expires_at = NULL, epoch = epoch + 1 WHERE schedule_id = ?""", (schedule_id,))

    def release_schedule_lease(self, schedule_id: str, *, occurrence_id: str, owner: str, epoch: int) -> bool:
        self._require_initialized()
        return self._connection.execute("""UPDATE schedule_leases SET occurrence_id = NULL, owner = NULL, expires_at = NULL
            WHERE schedule_id = ? AND occurrence_id = ? AND owner = ? AND epoch = ?""",
            (schedule_id, occurrence_id, owner, epoch)).rowcount == 1

    def insert_scheduled_search_link(self, run_id: str, occurrence_id: str) -> None:
        self._require_initialized()
        self._connection.execute("INSERT INTO scheduled_search_links (run_id, occurrence_id) VALUES (?, ?)", (run_id, occurrence_id))

    def get_scheduled_search_link(self, run_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM scheduled_search_links WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else _row_record(row)

    def get_occurrence_search_link(self, occurrence_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM scheduled_search_links WHERE occurrence_id = ?", (occurrence_id,)).fetchone()
        return None if row is None else _row_record(row)

    def insert_schedule_notification(self, *, notification_id: str, schedule_id: str, occurrence_id: str,
                                       created_at: str, delta: object, summary_sha256: str,
                                       previous_summary_sha256: str, notification_sha256: str) -> None:
        self._insert("schedule_notifications", {"id": notification_id, "schedule_id": schedule_id,
            "occurrence_id": occurrence_id, "created_at": created_at, "delta_json": _json_text(delta),
            "summary_sha256": summary_sha256, "previous_summary_sha256": previous_summary_sha256,
            "notification_sha256": notification_sha256})

    def get_schedule_notification(self, notification_id: str) -> Record | None:
        return self._get_by_id("schedule_notifications", notification_id)

    def list_schedule_notifications(self, schedule_id: str) -> list[Record]:
        return self._list("schedule_notifications", filters={"schedule_id": schedule_id}, order_by="created_at, id", limit=None, offset=0)

    def insert_schedule_notification_ack(self, notification_id: str, acknowledged_at: str, workflow_run_id: str) -> None:
        self._require_initialized()
        self._connection.execute("INSERT INTO schedule_notification_acks VALUES (?, ?, ?)", (notification_id, acknowledged_at, workflow_run_id))

    def get_schedule_notification_ack(self, notification_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM schedule_notification_acks WHERE notification_id = ?", (notification_id,)).fetchone()
        return None if row is None else _row_record(row)

    def get_saved_search(self, search_id: str) -> Record | None:
        return self._get_by_id("saved_searches", search_id)

    def list_saved_searches(self) -> list[Record]:
        return self._list("saved_searches", filters={}, order_by="created_at, id", limit=None, offset=0)

    def insert_search_run(self, *, run_id: str, search_id: str, created_at: str, workflow_run_id: str) -> None:
        self._insert("search_runs", {"id": run_id, "search_id": search_id,
            "created_at": created_at, "workflow_run_id": workflow_run_id})

    def get_search_run(self, run_id: str) -> Record | None:
        return self._get_by_id("search_runs", run_id)

    def list_search_runs(self, search_id: str | None = None) -> list[Record]:
        return self._list("search_runs", filters={} if search_id is None else {"search_id": search_id},
            order_by="created_at, id", limit=None, offset=0)

    def insert_search_event(self, *, event_id: str, run_id: str, position: int, at: str, action: str,
                             state: object, previous_sha256: str | None, event_sha256: str) -> None:
        self._insert("search_run_events", {"id": event_id, "run_id": run_id, "position": position,
            "at": at, "action": action, "state_json": _json_text(state),
            "previous_sha256": previous_sha256, "event_sha256": event_sha256})

    def list_search_events(self, run_id: str) -> list[Record]:
        return self._list("search_run_events", filters={"run_id": run_id}, order_by="position", limit=None, offset=0)

    def get_search_lease(self, search_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM search_leases WHERE search_id = ?", (search_id,)).fetchone()
        return None if row is None else _row_record(row)

    def acquire_search_lease(self, search_id: str, *, run_id: str, expected_epoch: int,
                              owner: str, now: str, expires_at: str) -> int | None:
        self._require_initialized()
        changed = self._connection.execute("""UPDATE search_leases SET run_id = ?, owner = ?, expires_at = ?, epoch = epoch + 1
            WHERE search_id = ? AND epoch = ? AND (owner IS NULL OR expires_at <= ?)""",
            (run_id, owner, expires_at, search_id, expected_epoch, now)).rowcount
        return expected_epoch + 1 if changed == 1 else None

    def release_search_lease(self, search_id: str, *, run_id: str, owner: str, epoch: int) -> bool:
        self._require_initialized()
        return self._connection.execute("""UPDATE search_leases SET run_id = NULL, owner = NULL, expires_at = NULL
            WHERE search_id = ? AND run_id = ? AND owner = ? AND epoch = ?""",
            (search_id, run_id, owner, epoch)).rowcount == 1

    def insert_search_batch_link(self, batch_id: str, run_id: str) -> None:
        self._require_initialized()
        self._connection.execute("INSERT INTO search_batch_links (batch_id, run_id) VALUES (?, ?)", (batch_id, run_id))

    def get_search_batch_link(self, batch_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM search_batch_links WHERE batch_id = ?", (batch_id,)).fetchone()
        return None if row is None else _row_record(row)

    def get_search_run_batch_link(self, run_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM search_batch_links WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else _row_record(row)

    def get_preparation_batch(self, batch_id: str) -> Record | None:
        return self._get_by_id("preparation_batches", batch_id)

    def list_preparation_batches(self) -> list[Record]:
        return self._list("preparation_batches", filters={}, order_by="created_at, id", limit=None, offset=0)

    def insert_preparation_item(self, *, item_id: str, batch_id: str, position: int,
                                job_id: str, spec: object, spec_sha256: str) -> None:
        self._insert("preparation_batch_items", {"id": item_id, "batch_id": batch_id,
            "position": position, "job_id": job_id, "spec_json": _json_text(spec), "spec_sha256": spec_sha256})

    def list_preparation_items(self, batch_id: str) -> list[Record]:
        return self._list("preparation_batch_items", filters={"batch_id": batch_id},
            order_by="position", limit=None, offset=0)

    def insert_preparation_event(self, *, event_id: str, item_id: str, position: int,
                                 at: str, state: object, previous_sha256: str | None,
                                 event_sha256: str) -> None:
        self._insert("preparation_batch_events", {"id": event_id, "item_id": item_id,
            "position": position, "at": at, "state_json": _json_text(state),
            "previous_sha256": previous_sha256, "event_sha256": event_sha256})

    def list_preparation_events(self, item_id: str) -> list[Record]:
        return self._list("preparation_batch_events", filters={"item_id": item_id},
            order_by="position", limit=None, offset=0)

    def get_preparation_lease(self, batch_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute("SELECT * FROM preparation_batch_leases WHERE batch_id = ?", (batch_id,)).fetchone()
        return None if row is None else _row_record(row)

    def acquire_preparation_lease(self, batch_id: str, *, expected_epoch: int,
                                   owner: str, now: str, expires_at: str) -> int | None:
        """Compare-and-swap a validated lease; call inside a short transaction."""
        self._require_initialized()
        changed = self._connection.execute("""UPDATE preparation_batch_leases
            SET owner = ?, expires_at = ?, epoch = epoch + 1, stop_reason = NULL
            WHERE batch_id = ? AND epoch = ? AND (owner IS NULL OR expires_at <= ?)""",
            (owner, expires_at, batch_id, expected_epoch, now)).rowcount
        return expected_epoch + 1 if changed == 1 else None

    def release_preparation_lease(self, batch_id: str, *, owner: str, epoch: int,
                                  stop_reason: str | None) -> bool:
        """Only the current owner may finish its invocation or clear its lease."""
        self._require_initialized()
        changed = self._connection.execute("""UPDATE preparation_batch_leases
            SET owner = NULL, expires_at = NULL, stop_reason = ?
            WHERE batch_id = ? AND owner = ? AND epoch = ?""",
            (stop_reason, batch_id, owner, epoch)).rowcount
        return changed == 1

    @contextmanager
    def read_transaction(self) -> Iterator[Self]:
        """Pin a consistent read snapshot without acquiring a write transaction."""
        self._require_initialized()
        if self._connection.in_transaction:
            yield self
            return
        self._connection.execute("BEGIN")
        try:
            yield self
        finally:
            self._connection.execute("ROLLBACK")

    def get_claim_retirement(self, claim_id: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute(
            "SELECT * FROM claim_retirements WHERE claim_id = ?", (claim_id,),
        ).fetchone()
        return None if row is None else _row_record(row)

    def list_claim_retirements(self) -> list[Record]:
        self._require_initialized()
        return [_row_record(row) for row in self._connection.execute(
            "SELECT * FROM claim_retirements ORDER BY retired_at, claim_id"
        )]

    def add_claim_retirement(
        self, *, claim_id: str, replacement_claim_id: str | None,
        workflow_run_id: str, actor_id: str, preview_token: str,
        claim_sha256: str, replacement_sha256: str | None,
        idempotency_sha256: str, retired_at: str,
    ) -> None:
        self._require_initialized()
        with self.transaction():
            self._connection.execute(
                "INSERT INTO claim_retirements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (claim_id, replacement_claim_id, workflow_run_id, actor_id, preview_token,
                 claim_sha256, replacement_sha256, idempotency_sha256, retired_at),
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RepositoryClosedError("SQLite repository is closed")

    def _require_initialized(self) -> None:
        self._ensure_open()
        if not self._initialized:
            raise RepositoryNotInitializedError(
                "Call SQLiteRepository.initialize() before data access"
            )
        version = read_schema_version(self._connection)
        if version > LATEST_SCHEMA_VERSION:
            raise FutureSchemaError(
                f"Database schema {version} is newer than supported {LATEST_SCHEMA_VERSION}"
            )
        if version != LATEST_SCHEMA_VERSION:
            self._initialized = False
            raise SchemaError(
                f"Database schema changed from supported version {LATEST_SCHEMA_VERSION} "
                f"to {version}; initialize it again"
            )

    def _insert(self, table: str, values: Mapping[str, object]) -> Record:
        self._require_initialized()
        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        with self.transaction():
            self._connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(values[column] for column in columns),
            )
        identifier = values.get("id")
        if not isinstance(identifier, str):
            raise RepositoryError(f"Inserted {table} row has no text id")
        record = self._get_by_id(table, identifier)
        assert record is not None
        return record

    def _get_by_id(self, table: str, identifier: str) -> Record | None:
        self._require_initialized()
        row = self._connection.execute(
            f"SELECT * FROM {table} WHERE id = ?",
            (identifier,),
        ).fetchone()
        return None if row is None else _row_record(row)

    def _list(
        self,
        table: str,
        *,
        filters: Mapping[str, object],
        order_by: str,
        limit: int | None,
        offset: int,
    ) -> list[Record]:
        self._require_initialized()
        conditions = [f"{column} = ?" for column in filters]
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        pagination, pagination_parameters = _pagination(limit, offset)
        rows = self._connection.execute(
            f"SELECT * FROM {table} {where} ORDER BY {order_by} {pagination}",
            (*filters.values(), *pagination_parameters),
        ).fetchall()
        return [_row_record(row) for row in rows]

    def _update_by_id(
        self,
        table: str,
        identifier: str,
        values: Mapping[str, object],
    ) -> None:
        self._require_initialized()
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self.transaction():
            cursor = self._connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                (*values.values(), identifier),
            )
            if cursor.rowcount != 1:
                raise RecordNotFoundError(f"{table} record does not exist: {identifier}")


def _new_id() -> str:
    return str(uuid.uuid4())


def inspect_schema(
    database: str | os.PathLike[str],
    *,
    migrations_dir: str | os.PathLike[str] | None = None,
) -> int:
    """Validate an existing database through a read-only connection."""

    raw_path = os.fspath(database)
    if not raw_path or raw_path == ":memory:" or raw_path.startswith("file:"):
        raise ValueError("Schema inspection requires a filesystem database path")
    path = Path(raw_path).expanduser().absolute()
    identity = prepare_sqlite_path(path, existing_only=True, read_only=True)
    uri = f"{path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        if require_safe_sqlite_path(path, read_only=True) != identity:
            raise UnsafeRuntimePathError("Database identity changed during schema inspection")
        connection.execute("PRAGMA query_only = ON")
        directory = (
            Path(migrations_dir)
            if migrations_dir is not None
            else default_migrations_directory()
        )
        version = validate_schema(connection, directory)
        if require_safe_sqlite_path(path, read_only=True) != identity:
            raise UnsafeRuntimePathError("Database identity changed during schema inspection")
        return version
    finally:
        connection.close()


def _normalize_hash(value: str | None) -> str | None:
    return value.lower() if value is not None else None


def _json_text(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not valid JSON: {exc}") from exc


def _without_none(values: Mapping[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _pagination(limit: int | None, offset: int) -> tuple[str, tuple[int, ...]]:
    if limit is not None and limit < 0:
        raise ValueError("limit must not be negative")
    if offset < 0:
        raise ValueError("offset must not be negative")
    if limit is None and offset == 0:
        return "", ()
    if limit is None:
        return "LIMIT -1 OFFSET ?", (offset,)
    return "LIMIT ? OFFSET ?", (limit, offset)


def _row_record(row: sqlite3.Row) -> Record:
    return {str(key): row[key] for key in row.keys()}
