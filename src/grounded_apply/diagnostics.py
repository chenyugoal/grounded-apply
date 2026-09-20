"""Opt-in, allowlisted command diagnostics, separate from private CLI results.

There is deliberately no free-text message, exception, argument, identifier,
hash, or metadata argument. This boundary cannot sanitize arbitrary content.
It emits only internally generated data and exact enum members. It is a
best-effort observation channel, never a transaction or recovery ledger.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import TextIO
from uuid import uuid4


class DiagnosticCommand(StrEnum):
    UNKNOWN = "unknown"
    PATHS = "paths"
    DOCTOR = "doctor"
    BRIEF = "brief"
    BACKUP = "backup"
    RESTORE = "restore"
    DELETE = "delete"
    JOBS_ADD = "jobs.add"
    JOBS_LIST = "jobs.list"
    JOBS_SHOW = "jobs.show"
    JOBS_ASSESS = "jobs.assess"
    JOBS_DISCOVER = "jobs.discover"
    BATCHES_PREPARE = "batches.prepare"
    BATCHES_RESUME = "batches.resume"
    BATCHES_SHOW = "batches.show"
    BATCHES_LIST = "batches.list"
    SEARCHES_CONFIGURE = "searches.configure"
    SEARCHES_RUN = "searches.run"
    SEARCHES_RESUME = "searches.resume"
    SEARCHES_SHOW = "searches.show"
    SEARCHES_LIST = "searches.list"
    SEARCHES_SCOPES = "searches.scopes"
    SEARCHES_EXPORT = "searches.export"
    SCHEDULES_CONFIGURE = "schedules.configure"
    SCHEDULES_TICK = "schedules.tick"
    SCHEDULES_SHOW = "schedules.show"
    SCHEDULES_LIST = "schedules.list"
    SCHEDULES_PAUSE = "schedules.pause"
    SCHEDULES_RESUME = "schedules.resume"
    SCHEDULES_ACK = "schedules.ack"
    MATERIALS_BUILD = "materials.build"
    MATERIALS_LIST = "materials.list"
    MATERIALS_SHOW = "materials.show"
    MATERIALS_APPROVE = "materials.approve"
    MATERIALS_EXPORT = "materials.export"
    ANSWERS = "answers"
    EXPORT = "export"
    APPLICATIONS_ADD = "applications.add"
    APPLICATIONS_LIST = "applications.list"
    APPLICATIONS_SHOW = "applications.show"
    APPLICATIONS_TRANSITION = "applications.transition"
    PROFILE_INIT = "profile.init"
    PROFILE_IMPORT = "profile.import"
    PROFILE_REVIEW = "profile.review"
    PROFILE_DECIDE = "profile.decide"
    PROFILE_SHOW = "profile.show"
    PROFILE_RETIRE = "profile.retire"
    PROFILE_EXTRACT = "profile.extract"
    PROFILE_ONBOARD = "profile.onboard"


class DiagnosticOutcome(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    DECISION_OUTCOME_UNKNOWN = "decision_outcome_unknown"
    BACKUP_OUTCOME_UNKNOWN = "backup_outcome_unknown"
    DELETION_OUTCOME_UNKNOWN = "deletion_outcome_unknown"
    BATCH_OUTCOME_UNKNOWN = "batch_outcome_unknown"
    SEARCH_OUTCOME_UNKNOWN = "search_outcome_unknown"
    SCHEDULE_OUTCOME_UNKNOWN = "schedule_outcome_unknown"


class CommandDiagnostics:
    """Write fixed-schema JSON lines to an explicitly supplied local stream."""

    def __init__(self, sink: TextIO, command: DiagnosticCommand) -> None:
        if type(command) is not DiagnosticCommand:
            raise TypeError("Diagnostic command must be a registered enum member")
        self._sink = sink
        self._command = command
        self._run_id = str(uuid4())
        self._decision_outcome_unknown = False
        self._backup_outcome_unknown = False
        self._deletion_outcome_unknown = False
        self._batch_outcome_unknown = False
        self._search_outcome_unknown = False
        self._schedule_outcome_unknown = False

    def require_schedule_recovery(self) -> None:
        if self._command not in {DiagnosticCommand.SCHEDULES_CONFIGURE, DiagnosticCommand.SCHEDULES_TICK,
                                 DiagnosticCommand.SCHEDULES_PAUSE, DiagnosticCommand.SCHEDULES_RESUME,
                                 DiagnosticCommand.SCHEDULES_ACK}:
            raise TypeError("Schedule recovery applies only to daily schedule mutations")
        self._schedule_outcome_unknown = True

    def require_search_recovery(self) -> None:
        if self._command not in {DiagnosticCommand.SEARCHES_CONFIGURE,
                                 DiagnosticCommand.SEARCHES_RUN, DiagnosticCommand.SEARCHES_RESUME}:
            raise TypeError("Search recovery applies only to search configuration or execution")
        self._search_outcome_unknown = True

    def require_batch_recovery(self) -> None:
        if self._command not in {DiagnosticCommand.BATCHES_PREPARE, DiagnosticCommand.BATCHES_RESUME}:
            raise TypeError("Batch recovery applies only to batch preparation or resumption")
        self._batch_outcome_unknown = True

    def require_deletion_recovery(self) -> None:
        if self._command != DiagnosticCommand.DELETE:
            raise TypeError("Deletion recovery applies only to deletion")
        self._deletion_outcome_unknown = True

    def require_decision_recovery(self) -> None:
        """Preserve the fixed recovery instruction when normal stderr is muted."""

        self._decision_outcome_unknown = True

    def require_backup_recovery(self) -> None:
        """Preserve fixed filesystem recovery advice without logging any path."""

        if self._command not in {DiagnosticCommand.BACKUP, DiagnosticCommand.RESTORE}:
            raise TypeError("Backup recovery applies only to backup and restore commands")
        self._backup_outcome_unknown = True

    def emit(self, outcome: DiagnosticOutcome) -> None:
        """Emit no caller content; failed diagnostics never change the command."""

        if type(outcome) is not DiagnosticOutcome:
            raise TypeError("Diagnostic outcome must be a registered enum member")
        if outcome != DiagnosticOutcome.STARTED and self._decision_outcome_unknown:
            outcome = DiagnosticOutcome.DECISION_OUTCOME_UNKNOWN
        if outcome != DiagnosticOutcome.STARTED and self._backup_outcome_unknown:
            outcome = DiagnosticOutcome.BACKUP_OUTCOME_UNKNOWN
        if outcome != DiagnosticOutcome.STARTED and self._deletion_outcome_unknown:
            outcome = DiagnosticOutcome.DELETION_OUTCOME_UNKNOWN
        if outcome != DiagnosticOutcome.STARTED and self._batch_outcome_unknown:
            outcome = DiagnosticOutcome.BATCH_OUTCOME_UNKNOWN
        if outcome != DiagnosticOutcome.STARTED and self._search_outcome_unknown:
            outcome = DiagnosticOutcome.SEARCH_OUTCOME_UNKNOWN
        if outcome != DiagnosticOutcome.STARTED and self._schedule_outcome_unknown:
            outcome = DiagnosticOutcome.SCHEDULE_OUTCOME_UNKNOWN
        try:
            record = {
                "schema_version": 1,
                "event": "cli.command",
                "run_id": self._run_id,
                "at": datetime.now(UTC).isoformat(),
                "command": self._command.value,
                "outcome": outcome.value,
                "recovery": (
                    "The decision may already be recorded. Retry the exact same "
                    "confirmed request and idempotency key. No external action was taken."
                    if outcome == DiagnosticOutcome.DECISION_OUTCOME_UNKNOWN else
                    "Backup or restore may be incomplete or already complete. Retry the same request; "
                    "incomplete output requires a new destination. No existing data was overwritten."
                    if outcome == DiagnosticOutcome.BACKUP_OUTCOME_UNKNOWN else
                    "Deletion may be incomplete or complete. Inspect the external receipt and target; "
                    "do not reuse a partial operation."
                    if outcome == DiagnosticOutcome.DELETION_OUTCOME_UNKNOWN else
                    "Batch progress may already be saved. Retry preparation with the same spec and "
                    "idempotency key, or resume the same batch ID. Saved drafts still require review."
                    if outcome == DiagnosticOutcome.BATCH_OUTCOME_UNKNOWN else
                    "Search configuration or run progress may already be saved. Retry the same "
                    "request and idempotency key, or resume the saved run ID. Inspect searches scopes/list."
                    if outcome == DiagnosticOutcome.SEARCH_OUTCOME_UNKNOWN else
                    "Daily schedule state or run progress may already be saved. Inspect schedules show/list; "
                    "retry configuration or changes with the same key, and tick the same schedule. "
                    "Reuse the same notification ID when acknowledging delivery."
                    if outcome == DiagnosticOutcome.SCHEDULE_OUTCOME_UNKNOWN else None
                ),
            }
            self._sink.write(json.dumps(record, sort_keys=True) + "\n")
            self._sink.flush()
        except (Exception, KeyboardInterrupt):
            # Never echo a sink error or retry a completed command. The stream
            # can be partial or absent; persisted workflow audit owns recovery.
            pass


class DiscardDiagnostics:
    """Drop human stderr while the opt-in structured event channel owns it."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass
