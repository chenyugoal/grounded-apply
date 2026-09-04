# Development guide

This guide describes the current Phase 0 workflow. It intentionally separates
commands that work now from the target toolchain described in the product design.

## Prerequisites

- Python 3.12 or newer
- a POSIX shell for `scripts/gapply`
- Git

The current bootstrap has no runtime dependencies. `pyproject.toml` contains
packaging metadata and Hatchling as its build backend, but repository development
does not currently require installing the package.

## Canonical commands

Run commands from the repository root.

| Purpose | Current command | Status |
|---|---|---|
| Full repository gate | `./scripts/check` | Supported now |
| CLI help | `./scripts/gapply --help` | Supported now |
| Environment/path diagnostics | `./scripts/gapply doctor --json` | Supported now |
| Structured profile proposal import | `./scripts/gapply profile import --source-file FILE --proposals-file FILE --idempotency-key KEY [--dry-run] [--json]` | Supported for synthetic data |
| Pending profile review | `./scripts/gapply profile review [--json]` | Supported, read-only |
| Full test suite | `PYTHONPATH=src python3 -m unittest discover -s tests -v` | Supported now |

The full gate runs both CLI smokes and the test suite without creating bytecode
in the checkout. The wrapper adds `src` to `PYTHONPATH`, so it exercises the
working tree without an editable install. For a disposable runtime root:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-dev ./scripts/gapply doctor --json
```

Do not use real candidate data in repository development.

### Structured profile import and review

The current import boundary consumes two separate UTF-8 inputs: exact source text
and a schema-version-2 proposal manifest. Each option accepts a regular file or
`-` for stdin; at most one may use stdin. File input is captured through one
descriptor after `lstat`: the terminal path must be a regular non-symlink file,
the platform must provide no-follow and nonblocking open flags, the opened
device/inode must match, and size plus change timestamps must remain stable
through the bounded read. There are intentionally no inline arguments for source
text, canonical text, values, source references, or extraction metadata. The
opaque idempotency key permits only a bounded identifier alphabet and must not
contain candidate data.

The manifest must use zero-based, end-exclusive Unicode-codepoint spans. Its
top-level fields are exactly `schema_version`, `source_sha256`,
`span_index_base`, `span_unit`, `span_end`, and `proposals`. `source_sha256` is a
lowercase 64-character digest assertion over the exact source UTF-8 bytes; the
application recomputes and compares it before any persistence. It binds the two
inputs for consistency but is not producer authentication. The manifest cannot
supply `source_ref`, `artifact_id`, `extractor_id`, `extraction_method`, or a
trust/authority field. Each proposal contains exactly `claim_type`, `value`,
`canonical_text`, `span`, and optional `confidence`; each span contains `start`,
`end`, and exact `text`. Duplicate JSON fields, non-finite numbers, unknown
fields, authority fields, more than 1,000 proposals, non-UTF-8 input,
non-regular/unstable files, digest mismatches, and mismatched spans fail closed.
The CLI and service both limit source input to 16 MiB of UTF-8; proposal-file
input is limited to 4 MiB at the CLI boundary.
Service validation also requires at least one alphanumeric source code point and
limits every screened content or metadata component to 8,192 code points; the
value, canonical-text, evidence, source-reference, and extraction-method limits
below are stricter.

Manifest schema version 2 uses a closed value-schema-version-1 registry. There
is no generic JSON fallback for an unregistered claim type. In this table,
`TEXT(N)` means non-blank, already-trimmed text with at most `N` Unicode code
points and no CR/LF or Unicode control, format, surrogate, line-separator, or
paragraph-separator code points:

| Claim type | Exact accepted `value` shape |
|---|---|
| `achievement` | `TEXT(2048)` |
| `certification` | `TEXT(512)` |
| `education` | `TEXT(1024)` |
| `education_degree` | `TEXT(512)` |
| `education_field` | `TEXT(512)` |
| `employment_dates` | Exact object `{"start": "YYYY-MM", "end": "YYYY-MM"}`; dates are from 1900-01 through 2099-12, `end` may instead be lowercase `present`, and a dated end cannot precede the start |
| `employment_description` | `TEXT(2048)` |
| `employment_title` | Exact object `{"employer": TEXT(512), "title": TEXT(512)}` |
| `language` | `TEXT(128)` |
| `portfolio_item` | `TEXT(2048)` |
| `project_contribution` | Exact object `{"project": TEXT(512), "contribution": TEXT(2048), "ownership": LEVEL}`; `LEVEL` is `supported`, `contributed`, `co-led`, `led`, or `owned` |
| `project_outcome` | Exact object `{"activity": TEXT(512), "before_minutes": INT, "after_minutes": INT}`; minutes are non-boolean integers from 0 through 525,600 |
| `publication` | `TEXT(2048)` |
| `skill_use` | `TEXT(256)` |

Before its first storage call, the service snapshots all nested request state and
validates the entire batch. The persisted surfaces it screens include recursive
value strings (whose object keys are fixed by the closed schemas), canonical
text, exact selected evidence, application-derived source/artifact/ingress
identifiers, and caller-supplied subject/scope identifiers. Deterministic
high-confidence rules are derived from one immutable restricted-text taxonomy.
Taxonomy version 1 has stable categories for work authorization/immigration,
security clearance, veteran status, disability status, criminal/legal
attestations, conflicts of interest, demographic self-identification,
government identifiers, and authentication credentials. Each category declares
context patterns, exact normalized assignment keys, and label-only patterns;
the application validates the declaration at import and derives every matcher
from it. Percent-encoded content is decoded repeatedly to a fixed point, with at
most eight decoding rounds; content still changing after that is rejected.
Fixed-size ordered boundary pairs are checked within each proposal. Recognized
assignment keys split across two persisted content or metadata components are
checked across the batch; broader multi-component fragmentation is outside this
heuristic.
Caller-controlled metadata is limited to 1,048,576 persisted code points per
batch, including the per-record copies made for each claim and evidence row.
This is deterministic lexical defense in depth, not a complete semantic, secret,
or PII classifier. Manifest schema 2, request-identity schema 4, value schema 1,
content policy 2, restricted taxonomy 1 plus its canonical SHA-256,
source-identity 1, span-locator 1, result-manifest 3, record-ID 1, and record-digest
1 participate in the idempotency request hash and are recorded in the workflow
audit input.
That input contains only version identifiers, counts, digests, the registered
ingress ID, and digest-derived source/artifact references. It includes the
already hashed idempotency key so the lookup column is redundantly bound to the
audited input; it contains no raw source, proposed values, selected evidence,
file path, filename, or raw idempotency key.

Canonical text is limited to 2,048 code points. Each selected evidence item is
limited to 4,096 code points and 16 lines, and all selected evidence in one batch
is limited to 65,536 code points. The batch rejects selected spans covering 80%
or more of the source's alphanumeric content. Exact normalized, case-folded
whole-source reconstruction is rejected in the raw form and every percent-decode
layer in each value, canonical-text, evidence, and metadata channel. The same
aligned-layer exact check runs across combined value/canonical/evidence content;
metadata remains a separate channel. That combined check concatenates proposal
serialization order and its full reverse; it does not exhaustively test arbitrary
cross-channel reorderings. Every distinct decode layer through the fixed point
is evaluated rather than only the final layer. Individual channels also use an
80% token-weight guard and one adaptive exact-window comparison
selected from 1, 2, 4, 8, or 16 code points (or the exact shorter source length).
Window selection projects each channel onto at most the source's alphanumeric
character multiset so unrelated or repeated padding cannot select a misleadingly
large window. These heuristics are deterministic defense in depth, not proof of
exhaustive near-duplicate classification.

Rejection occurs before a storage transaction and does not reserve the
idempotency key.

Unselected raw source text remains caller-managed and in memory. It is used for
span and whole-source validation and its digest, but it is not persisted or
globally sensitive-pattern scanned. A deterministic digest-only artifact stores
`sha256:<digest>`, byte and Unicode-codepoint sizes, and versioned retention
metadata; it stores no input path, original filename, or raw source body. Every
claim/evidence pair references that artifact and the registered
`grounded-apply.profile-import.manifest@1` ingress identifier. The identifier
names the application path that accepted the untrusted manifest; it does not
assert who produced the proposals. To prevent an answer-only selection from
hiding a sensitive label, the service screens up to 256 unselected code points
on either side within the selected logical line, including a same-line label
prefix, and the nearest nonblank exact recognized label-only line in a
512-code-point lookbehind. Blank extraction separators therefore cannot detach
an answer from its label. Label-only matching applies the same bounded
fixed-point percent decoding and Unicode/control normalization used by the
taxonomy without classifying unrelated unselected lines. Semantic contradiction
classification, redacted logging, and data lifecycle commands are still required
before real candidate use. If more than 512 code points precede the selection on
the same logical line, validation fails closed rather than silently treating a
truncated suffix as the complete label context.

`profile import --dry-run` performs service-owned request validation without
opening a repository or creating runtime state. Its `storage_checked: false`
field makes clear that it does not validate schema, artifact references, or prior
idempotency-key use. A persisted import requires an already initialized current
schema. It opens that database in existing-only SQLite `mode=rw`, validates the
current schema on the same connection used for the write, and never creates or
migrates storage implicitly. Missing, empty, or input-time-replaced databases
fail closed.

Import and review also revalidate runtime privacy without repairing it as a side
effect: the data directory and database must retain user-only permissions, the
resolved database target must remain inside the private data directory and
outside Git worktrees, and every portable runtime child must remain beneath
`GROUNDED_APPLY_HOME`. Permission drift and path escapes fail unchanged.

New imports contain only `needs_review`/`pending` claims and pending exact
evidence spans. The public generic claim/evidence service rejects
`SourceType.IMPORTED_RESUME`; only the import workflow can create those records.
Claim and evidence UUIDs are deterministically derived from the creating
workflow plus proposal position. Schema 2 stores a durable association from that
position to one unique claim/evidence pair and its immutable record digest. A
retry accepts only the exact succeeded checkpoint shape and revalidates the
digest artifact, workflow-bound IDs, ordered result manifest, association,
unique one-to-one support link, claim semantics, locator, selected text,
checksum, registered ingress, and evidence metadata. It accepts a correctly
audited terminal review projection as an exact replay and never repairs a missing
or conflicting provenance record. CLI import output is minimized to hashes,
counts, and opaque record IDs.

`profile review` opens SQLite in read-only/query-only mode and lists the pending
queue with exact supporting evidence, structured values, scope, sensitivity,
creating workflow ID, proposal index, stale-safe review token, and explicit
`content_trust: untrusted` / `usable: false` markers. Before display it requires
exact current workflow/result/association identity and one uniquely linked
pending imported evidence item, then revalidates the record digest, current
content policy, digest-only artifact, registered ingress, locator source/bounds,
and selected-text checksum. The CLI command has no mutation or approval option.
Prompt-like imported content is data, never an instruction.

The typed application-service boundary `ProfileService.decide_review_item`
records one approval or rejection atomically. Requests require the displayed
review token, an opaque actor ID, and an opaque idempotency key. The decision
workflow stores only version identifiers, IDs, hashes, decision, actor, and time;
it does not store raw source, value, evidence, filename, path, or raw idempotency
key. Approval changes the claim to `verified`/`approved` and its evidence to
`confirmed` with the same actor/time; rejection changes them to
`withdrawn`/`rejected` and retains no verification actor. Confidential and highly
sensitive items cannot be approved. An active same-subject explicitly
`contradicted` record blocks approval, with non-public conflicts reported through
a generic non-disclosing error. The decision-time check does not reject a
distinct value solely because it differs, but existing resolution returns
`Contradiction` when multiple approved value groups compete for one intent.
Resolution first revalidates every associated imported projection and terminal
audit. The unkeyed record digest is a consistency and stale-review control, not
authentication against a writer able to recompute the entire database
projection.

Manifest version 1, request-identity versions 1–3, result-manifest versions 1–2,
content-policy version 1, and the former public `CreateImportProposal`
constructor are unsupported. Current imports use manifest 2, request identity 4,
result manifest 3, record digest 1, content policy 2, restricted taxonomy 1, and
database schema 2. Migration 002 creates the association/decision table but does
not fabricate record digests or associations for earlier imports. Regenerate
manifest-v1 input; for an earlier manifest-v2 workflow, use a fresh opaque
idempotency key in disposable synthetic state. There is no automatic policy
migration or reclassification. Legacy path-provenance or earlier-policy rows
fail review/decision closed without displaying their old provenance. Future
version changes likewise require a registered dispatcher or an explicit
migration; stored version tags do not by themselves grant compatibility.

This boundary is currently for synthetic development data only.

### Planned command hardening

The design targets `uv`, Typer, SQLAlchemy, Alembic, Ruff, and Pyright. They are
**planned**, not current prerequisites or passing checks. Expected commands such
as the following must not be called canonical until their dependencies,
configuration, and tests are committed and the commands pass:

```bash
uv sync --all-groups
uv run gapply --help
uv run pytest
uv run ruff check .
uv run pyright
uv run alembic upgrade head
```

When the project adopts them, update this command matrix, `README.md`,
`AGENTS.md`, and `docs/SESSION_HANDOFF.md` in the same change. Remove obsolete
commands rather than leaving two ambiguous paths.

## Source layout and dependency direction

Grounded Apply is a modular monolith. The target domain boundaries are:

- `profile`: claims, evidence, preferences, eligibility, stories, contradictions,
  and memory proposals;
- `jobs`: discovery, normalization, snapshots, deduplication, and requirements;
- `matching`: gates, requirement-to-claim mapping, gaps, and fit explanations;
- `materials`: structured content, verification, LaTeX, PDF, and manifests;
- `applications`: records, append-only events, submissions, and reminders;
- `automation`: safe browser plans, human gates, traces, and resumability;
- `connectors`: external job, model, document, browser, and email adapters;
- `agent`: Codex workflows and versioned structured model calls.

Keep dependency direction inward:

```text
CLI / future API -> application services -> domain and policies
                                      <- repository and external adapters
```

Domain and policy modules must not import CLI, web, ORM, browser, or model SDK
types. Prompts may orchestrate typed services; they never own truth,
authorization, persistence, or state transitions.

## Development workflow

1. Resume exactly as described in `AGENTS.md` and
   `docs/SESSION_HANDOFF.md`.
2. Choose one bounded roadmap outcome and identify its truth/privacy failure
   modes before implementation.
3. Add or update synthetic tests. Prefer a failing test that expresses the rule
   before changing behavior.
4. Implement through the appropriate domain or application boundary. Avoid
   direct storage mutation from an interface.
5. Run the narrow test while iterating.
6. Run the canonical full test command and both CLI smoke commands.
7. Review the complete diff for private data, secrets, scope creep, and stale
   documentation.
8. Update `docs/SESSION_HANDOFF.md` at the meaningful stopping point.

### Definition of done

A change is complete only when:

- behavior and failure behavior are represented by tests;
- the canonical test suite passes, or a precise pre-existing failure is recorded;
- public interfaces are typed and errors are structured;
- truth, scope, sensitivity, and human-gate policies remain fail-closed;
- persistence changes are migration-safe and idempotent where applicable;
- fixtures are unmistakably synthetic;
- user-facing and handoff documentation matches the code;
- the live checkpoint identifies the next exact task.

## Truth-layer development

For every value that might appear in an application:

1. classify its intent and sensitivity;
2. resolve typed entities, approved claims, and approved derivations;
3. filter by status, scope, effective dates, and reuse policy;
4. return supported claim IDs, `NeedInfo`, or `Contradiction`;
5. generate only from the resulting claim packet;
6. verify factual units after generation;
7. persist provenance and validation results.

Use explicit enums for lifecycle and policy states. Confidence measures extraction
certainty; it never upgrades an unverified claim. A derived claim must record its
versioned rule, inputs, calculation time, and staleness policy. Phase 0 stores and
audits that provenance but rejects derived values during resolution until a
registered evaluator can recompute them.

## Persistence and migrations

SQLite is the target default source of truth; FTS or embeddings are secondary
indexes only. Once persistence lands:

- application code uses repositories and services, never ad hoc SQL from the CLI;
- every schema change has a forward migration and an upgrade test;
- opening a future schema version fails closed;
- mutations accept or grow toward idempotency keys and dry-run behavior;
- application events are append-only, while current state is a projection;
- job-posting and submission snapshots are immutable;
- runtime data is created outside the repository with user-only permissions.

Until Alembic is actually installed and configured, do not document a migration
command as supported. Record the temporary migration mechanism and its test in
the live checkpoint.

## Testing priorities

Use only synthetic fixtures. Alongside normal unit tests, add adversarial cases
as their surfaces are introduced:

- missing or stale information produces `NeedInfo`;
- contradictory claims are not silently resolved;
- unsupported metrics, technologies, seniority, or ownership are rejected;
- sensitive facts cannot be inferred or reused outside policy;
- imported prompt-injection text remains inert;
- repeated mutations are idempotent;
- invalid application transitions fail;
- duplicate jobs are not over-merged;
- rendering fails when verified content or critical extracted fields are absent;
- browser plans stop at every mandatory human gate.

The unsupported factual claim rate on the golden corpus is always zero.

## Runtime data and safe diagnostics

`GROUNDED_APPLY_HOME` overrides all data locations beneath one dedicated private
root. Without it, the current POSIX implementation uses XDG-compatible config,
data, cache, and state directories. Native Windows directory conventions remain
planned. Never default to a repository-relative data directory.

Normal logs may include run IDs, typed event metadata, hashes, redacted errors,
and protected artifact references. They may not include raw resumes, sensitive
answers, personal model prompts, secrets, cookies, browser storage, or unredacted
screenshots.

Before sharing a diff or support artifact:

```bash
git diff --check
git status --short
```

Inspect the complete diff manually. Automated scanning is an additional gate,
not a replacement for review.

## Exact session handoff protocol

`docs/SESSION_HANDOFF.md` is the single live checkpoint. `docs/ROADMAP.md` holds
phase scope; do not create competing status notes.

At session start:

1. Run `pwd`.
2. Read `AGENTS.md`, `docs/SESSION_HANDOFF.md`, `docs/ROADMAP.md`, this guide,
   relevant ADRs, and relevant design sections.
3. Run `git status --short --branch`, `git log -5 --oneline --decorate`, and
   `git diff --stat`; inspect relevant uncommitted diffs.
4. Run the handoff's **First command** and compare the result with **Verification**.
5. Continue the named **Next exact task**, or update the handoff before selecting
   a different task if repository evidence makes it stale.

At every meaningful stopping point, replace—not append duplicate copies of—the
live fields with:

- timestamp and current `HEAD`;
- milestone and status;
- completed items, naming whether they are committed or uncommitted;
- exact commands run and pass/fail outcomes;
- current working-tree paths relevant to the milestone;
- known issues or blockers;
- numbered next tasks, each naming the first file or command;
- one **First command** for the next session;
- durable key decisions not already captured by an ADR.

Do not write “tests pass” without the command. Do not write “next: continue
implementation.” The checkpoint must allow a new agent with no chat history to
act safely in its first minute.
