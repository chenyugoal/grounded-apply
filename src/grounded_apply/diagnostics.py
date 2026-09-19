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
                    if outcome == DiagnosticOutcome.DELETION_OUTCOME_UNKNOWN else None
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
