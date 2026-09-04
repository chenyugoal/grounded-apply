# ADR 0002: Safe SQLite opens and content-free diagnostic events

- **Status:** Accepted
- **Date:** 2026-09-04
- **Scope:** Current POSIX bootstrap; synthetic development data

## Decision

Every public SQLite adapter open owns a mandatory filesystem guard. The CLI
retains its additional validation of the complete runtime directory layout and
`GROUNDED_APPLY_HOME` containment. Direct `SQLiteRepository` and `inspect_schema`
callers cannot omit checks for Git exclusion, private parent permissions, direct
regular single-link database files, private direct single-link sidecars, or
orphan sidecars. URI targets and implicit temporary databases are rejected;
explicit `:memory:` remains available to non-existing-only repository callers.

Initialization may create a missing file or repair an existing safe file's mode.
It uses an exclusive no-follow descriptor for creation and a verified no-follow
descriptor for mode repair. SQLite then opens an already-private file using
`mode=rw`, so no journal is created before the main file is private. Existing-only
access never creates or chmods a database. Read-only access and schema inspection
also require no recognized sidecars and reject persistent WAL before SQLite
access. Connections are closed when setup or context-manager initialization
fails. The private migration helpers accept an already-owned connection and are
not a substitute for the public guarded adapter.

An abrupt subprocess exit test proves the current SQLite implementation can
recover a private hot rollback journal during mutating import. The fixture proves
dirty pages reached the main database and checks the journal header before CLI
access. Recovery restores the pre-crash data; import then completes once and
exact retries retain the original result. Read-only access and unsafe-journal
cases fail without recovery or mutation. This is process-crash coverage, not a
claim about disk failure, power loss, every journal mode, or repair of corruption.

Normal diagnostic events are opt-in via `gapply --log-events COMMAND --json`.
Stderr contains fixed-schema JSON lines, while stdout remains the private
response channel. Events contain schema version, fixed event name, a fresh
invocation UUID, UTC timestamp, registered command/outcome enums, and a nullable
fixed recovery instruction. The logging interface accepts no free text, arguments,
exceptions, paths, candidate identifiers, hashes, source content, or metadata.
Human stderr is discarded in this mode; JSON responses still carry their usual
errors and warnings on stdout. No log files or network telemetry are created.

Diagnostic failures do not fail or repeat a command. An interrupted confirmed
decision or post-commit output failure emits `decision_outcome_unknown` with a
fixed instruction to retry the exact same request and idempotency key. Logs may
be partial or absent after sink failure; they never replace the durable workflow
audit and do not prove that a decision was or was not committed. Do not combine
private stdout with the event stream or treat a private response as a support log.

## Limits

Filesystem validation is repeated sampling, not an atomic lock or authentication
boundary. A malicious same-UID process can replace a file after the final sample;
the adapter does not claim to defeat that process. The checks do not authenticate
the database's logical contents. Native Windows secure-path behavior remains
unimplemented and unverified. There is no recovery/repair command for refused
read-only journal states.

The diagnostic boundary covers this CLI and its explicit logger. It does not
sanitize arbitrary third-party logging, shell history, terminal recording,
injected Python code, or caller-controlled output sinks. Candidate content stays
out by construction, rather than through a claim of comprehensive text redaction.
No backup, export, deletion, retention, or real-data release is implied.
