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
import uuid
from hashlib import sha256
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Self

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
    ) -> None:
        if timeout < 0:
            raise ValueError("timeout must not be negative")

        self._database = os.fspath(database)
        self._migrations_dir = Path(migrations_dir) if migrations_dir is not None else None
        self._connection = sqlite3.connect(
            self._database,
            timeout=timeout,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        self._initialized = False
        self._transaction_depth = 0
        self._savepoint_counter = 0

        timeout_ms = min(round(timeout * 1000), 2_147_483_647)
        self._connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        self._connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = self._connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            self._connection.close()
            self._closed = True
            raise RepositoryError("SQLite foreign-key enforcement could not be enabled")

        if self._database != ":memory:" and not self._database.startswith("file:"):
            Path(self._database).chmod(0o600)

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
        """Idempotently validate and migrate the database to this code version.

        Databases from newer code and databases with inconsistent or edited
        migration history are rejected before migrations run.
        """

        self._ensure_open()
        migrations_dir = self._migrations_dir or default_migrations_directory()
        version = initialize_schema(self._connection, migrations_dir)
        if version != LATEST_SCHEMA_VERSION:
            raise SchemaError(
                f"Expected schema version {LATEST_SCHEMA_VERSION}, initialized {version}"
            )
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

    def __enter__(self) -> Self:
        return self.initialize()

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

        now = utc_now()
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

    path = Path(database).expanduser().resolve()
    uri = f"{path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        directory = (
            Path(migrations_dir)
            if migrations_dir is not None
            else default_migrations_directory()
        )
        return validate_schema(connection, directory)
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
