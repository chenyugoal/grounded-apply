# Grounded Apply

**A local-first, evidence-backed job-search assistant for Codex.**

Grounded Apply is designed to turn a candidate's approved career history into
traceable job assessments, application materials, questionnaire answers, and an
application record. Its central rule is simple:

> The system may transform, select, summarize, and reorganize verified
> information, but it may not invent candidate facts.

## Current status

The **Codex-guided application preparation** milestone is implemented and locally
verified for industry research and engineering workflows. Tell Codex what you want to do;
it operates the local tools and handles IDs and commands. The repository skill
and [conversational workflow](docs/CODEX_WORKFLOW.md) cover profile review,
job comparison, application packs, and resumption. See [the roadmap](docs/ROADMAP.md)
and live checkpoint for verification status.

`./scripts/gapply brief --json` provides a read-only next-action view across
saved jobs and applications. Its ordering follows workflow stage, not fit or
hiring probability; response-check suggestions neither send messages nor schedule
reminders. Private facts and application history stay in the existing runtime.

Grounded Apply now has a **local application pilot**: extract selected facts from
a UTF-8 resume, approve them once, save job text and its URL, inspect a requirement
evidence matrix, produce a traceable PDF and career answers, approve the material,
and track manually submitted applications. See [the quickstart](docs/QUICKSTART.md)
for the complete workflow and [ADR 0006](docs/adr/0006-local-application-pilot.md)
for its bounded scope. The live checkpoint contains the exact release evidence.

It is a command-line tool that Codex can operate with you. Resume tailoring means
selecting and ordering approved text; it does not write new factual prose.
Matching retrieves evidence and gaps without claiming fit or predicting hiring.
No model calls, live job fetching, discovery, browser fill, or external submission
are implemented. Input is plain text; PDF output uses a local TeX installation.

The pilot includes encrypted whole-database backup, confirmed restore into a new
private home, fixed-schema support export, audited claim withdrawal/replacement,
and explicit whole-portable-home deletion. External copies and source documents
remain caller-owned. Full filesystem backup, per-record deletion, automatic
retention, richer input formats, and hosted OS/interpreter verification remain
unfinished. Real data belongs only in a private runtime outside the repository;
development and tests always use fictional data.

The single live checkpoint is [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md).
It records what is actually implemented, the last verification results, known
issues, and the next exact task. The longer-term scope in
[`GROUNDED_APPLY_DESIGN.md`](GROUNDED_APPLY_DESIGN.md) and
[`docs/ROADMAP.md`](docs/ROADMAP.md) is planned unless the checkpoint says it is
implemented and verified.

## Start the local pilot

Prerequisite: Python 3.12 or newer. The repository launcher uses `GAPPLY_PYTHON`
when supplied, otherwise `.venv/bin/python3` when present, otherwise `python3`
on PATH. Unsupported interpreters stop with setup guidance. For example,
`GAPPLY_PYTHON=/absolute/path/to/python3.12 ./scripts/gapply --help` needs no
shell activation. The base CLI has no runtime package
dependencies and does not require installation. Encrypted backup/restore requires
the optional `backup` extra; PDF generation/verification requires the `materials`
extra plus `pdflatex`, `lmodern`, `geometry`, and `enumitem`. The quickstart explains
the isolated environment. The currently verified platform is macOS/Python 3.13.1.

```bash
source .venv/bin/activate
./scripts/gapply --help
./scripts/gapply profile extract --help
./scripts/gapply jobs assess --help
./scripts/gapply materials build --help
./scripts/gapply applications transition --help
```

For development verification:

```bash
./scripts/check
./scripts/gapply --help
./scripts/gapply doctor --json
./scripts/gapply profile decide --help
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`./scripts/check` is the canonical full gate; the other commands are useful
individual checks. `uv`, Typer, SQLAlchemy, Alembic,
Ruff, and Pyright are planned Phase 0 hardening work; their commands are not yet
supported project checks. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) before
changing the toolchain.

Optional diagnostic events use `./scripts/gapply --log-events doctor --json`.
Stderr then contains only fixed-schema command events; stdout remains private
and may include paths or candidate data. No files or telemetry are created.
Events exclude arguments, exception text, source content, tokens, and keys.
Use command `--json` to retain normal errors and warnings on stdout in this mode.
The optional installed-package gate, `python scripts/check_package.py`, is
documented in the development guide and verifies a fresh wheel installation.
The configured GitHub Actions matrix runs the synthetic checks and installed
package gates on Linux/macOS with Python 3.12/3.13. The hosted matrix has not yet
passed in full; see the handoff for current hosted results and local verification.
Workflow changes also require `actionlint -shellcheck= -pyflakes=
.github/workflows/check.yml` with actionlint 1.7.12 installed. This checks GitHub
workflow expressions separately from the application tests; setup and scope are
documented in the development guide.

### Portable data deletion

`./scripts/gapply delete --target-home ABSOLUTE_HOME --receipt EXTERNAL_ABSOLUTE_PATH`
previews deletion of a dedicated portable runtime. To perform it, repeat the
same command with `--preview-token TOKEN_FROM_PREVIEW --confirm`. `--json` is
supported. No environment default selects the deletion target. The receipt must
be a new file under a private directory outside the target.

The command refuses changed previews, unknown files, links, unsafe permissions,
SQLite sidecars, files over the supported 16 MiB inventory limit, and nonempty
artifact/cache/output directories. An external
receipt records start and completion without personal content. An incomplete
operation is refused on retry and requires inspection. External backups and
source files remain; this is logical deletion, not secure erasure. Retention is
manual until explicitly confirmed deletion. See ADR 0004 for the bounded scope.

### Encrypted profile backup and restore

In an installed environment, install `grounded-apply[backup]` from your built
wheel. For source development, install `requirements-backup.txt` in a disposable
virtualenv outside the repository, then run
`python -W error scripts/check_backup.py` with that interpreter. This gate requires
the provider and cannot pass by skipping encryption tests.

Use absolute paths under an existing private (`0700`) directory outside Git.
The following examples prompt for a passphrase without echoing it:

```bash
gapply backup --encrypt /secure/private-directory/profile.gapply --dry-run --json
gapply backup --encrypt /secure/private-directory/profile.gapply --json
gapply restore --archive /secure/private-directory/profile.gapply \
  --target-home /secure/private-directory/restored-profile --json
gapply restore --archive /secure/private-directory/profile.gapply \
  --target-home /secure/private-directory/restored-profile \
  --archive-sha256 HASH_FROM_PREVIEW --confirm --json
```

Retain a strong passphrase separately; the application does not store or recover
it. Automation can supply bounded UTF-8 stdin with `--passphrase-stdin`. There is
no inline-secret, environment-variable, or passphrase-file option. The optional
provider uses Fernet with Argon2id; `.gapply` archives are not age files.

The archive contains one consistent database, up to 16 MiB, including approved
facts, job snapshots, generated PDF/LaTeX/text, answers, and application history.
It excludes original documents, exported copies, browser state, config, caches, and logs;
non-digest artifact references fail closed. Restore validates authentication,
current schema, database integrity, and references before writing. It creates a
new private home and never replaces a profile. Point `GROUNDED_APPLY_HOME` at that
new home to use it.

The same backup path, passphrase, and snapshot return the original ciphertext;
changed input never overwrites it. An exact restore retry succeeds only while
its completion receipt and complete target remain unchanged. A crash can leave
a private partial archive or target; it is refused on retry, so inspect it
separately and choose a new destination. These commands do not delete anything.
See [ADR 0003](docs/adr/0003-encrypted-profile-backup.md) for format and limits.

On the current POSIX bootstrap, runtime data defaults to XDG-compatible user
directories outside the repository. Native Windows path support remains
planned. To isolate development data, point the application at a dedicated,
private scratch directory:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-dev ./scripts/gapply profile init --dry-run --json
```

Never point `GROUNDED_APPLY_HOME` at the repository or commit anything created
under a real runtime-data directory. An existing override root must already be
private (`0700`); broad directories such as `/`, the user home, and the shared
temporary directory are rejected.

### Synthetic structured-import demo

The checked-in proposal manifest and resume are conspicuously fictional. Use
these fixtures for development. The end-to-end synthetic gate is
`python -W error scripts/check_pilot.py` with both optional extras and TeX installed.
It never reads a personal runtime and exercises the full lifecycle, including
backup/restore and deletion. The older structured-import example is:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile init --json

GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-readme-import-1 \
  --dry-run --json

GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-readme-import-1 --json

GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile review --json
```

Use one `claim.id` and its exact `review_token` from that review response for a
decision. Omitting `--confirm` validates only the command input shape:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile decide \
  --claim-id CLAIM_ID_FROM_REVIEW \
  --review-token TOKEN_FROM_REVIEW \
  --decision approve \
  --actor-id synthetic-local-reviewer \
  --idempotency-key synthetic-readme-decision-1 --json

GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile decide \
  --claim-id CLAIM_ID_FROM_REVIEW \
  --review-token TOKEN_FROM_REVIEW \
  --decision approve \
  --actor-id synthetic-local-reviewer \
  --idempotency-key synthetic-readme-decision-1 --confirm --json
```

`--source-file` and `--proposals-file` accept a regular UTF-8 file or `-` for
stdin, but only one input may use stdin. Candidate text and proposed values have
no inline command-line option. Dry-run performs persistence-independent request
validation and reports `storage_checked: false`; it checks runtime containment
plus database/sidecar type, link, and orphan safety, but does not create or open
the repository or inspect its schema or database permissions. A real import
requires an earlier `profile init`, remains
idempotent under its opaque key, and creates only pending, unusable records.
The strict proposal manifest is schema version 2. It carries the SHA-256 of the
exact source bytes as a consistency assertion, plus span conventions and
proposals; it cannot supply a source path, artifact ID, extractor identity, or
trust state. The application recomputes the digest, derives a path-free
`sha256:<digest>` source reference and deterministic digest-only artifact, and
assigns the registered manifest-ingress identifier. A matching digest proves
that the two inputs belong together; it does not authenticate who authored the
source or proposals.

Every allowed claim type has a closed versioned value shape. Vocabulary 1 retains
the original career types; vocabulary 2 adds explicit candidate name and contact
fields. Workflows bind the smallest vocabulary needed. Before any
storage transaction, the service snapshots all nested request state and
validates the whole batch under content policy 2. All restricted-text matchers
are derived from one immutable taxonomy version 1 covering work authorization
and immigration, security clearance, veteran and disability status,
criminal/legal attestations, conflicts of interest, demographic
self-identification, government identifiers, and authentication credentials.
The taxonomy uses deterministic high-confidence context, exact-assignment, and
label rules, plus fixed-point percent decoding and bounded fragmentation checks.
Its version and canonical SHA-256 are recorded in request-identity schema 4.
These checks are defense in depth, not complete semantic, secret, or PII
classification. Manifest, request, value, content-policy, restricted-taxonomy,
locator, result-manifest, source-identity, record-ID, and record-digest versions
are bound into the workflow audit identity. Claim and evidence IDs are
deterministically bound to the creating workflow and proposal position. Each
immutable claim/evidence projection has a stable digest in result-manifest schema
3 and a durable schema-2 review association. An idempotent retry revalidates the
checkpoint, source artifact, ordered result records, association, links, and
stored content before returning them.

Raw source text is used in memory for digest, span, context, and whole-source
checks but is not persisted or globally sensitive-pattern scanned. From the
source body, only exact selected evidence spans are stored. The digest-only
artifact retains the digest
and byte/code-point sizes, never an input path, original filename, or source
body. Terminal symlinks and files that change between inspection and descriptor
read are rejected. To prevent an answer-only span from hiding its label,
validation also checks a bounded same-line neighborhood and the nearest nonblank
recognized label-only line within a 512-code-point lookbehind, including across
blank extraction separators. Unrelated unselected source lines remain outside
classification. A selected span with more than 512 unbroken same-line prefix
code points fails closed because its full bounded label context is unavailable.
Import, review, confirmed decisions, and database diagnostics fail unchanged if
the private data directory or database gains group/other access, if the database
target escapes its data directory, or if a portable runtime child escapes
`GROUNDED_APPLY_HOME`.

Before the typed CLI or public SQLite adapters open or inspect storage, the named
database and any recognized SQLite `-journal`, `-wal`, or `-shm` sidecar must be direct regular
non-symlink files. Each must have exactly one hard link, sidecars must have no
group/other access, and orphan sidecars are rejected while left unchanged.
Read-only review and doctor inspection are stricter: every sidecar must be absent
and the database header must use rollback-journal rather than persistent WAL
mode, so a read does not create, recover, or delete SQLite state. Mutating
commands may let SQLite process an otherwise private, single-link sidecar.
An abrupt-process-exit integration test verifies hot rollback-journal recovery
during import, restoration of pre-crash data, and exactly idempotent replay.
`profile init` separately validates any existing `config.toml` as a private,
direct, single-link regular file and creates a missing default through an
exclusive no-follow descriptor, so a dangling config symlink cannot redirect
the write.

These checks are repeated around SQLite open or inspection and compare sampled
device/inode identity. They reduce replacement races but are not an atomic lock:
a same-UID process can still change a pathname, link count, or sidecar after the
last sample. The current local-first boundary therefore does not claim protection
against a malicious same-user process. Direct SQLite repository and schema
inspection calls now enforce the database/sidecar guards and require a private
parent directory outside Git. The CLI additionally checks the complete runtime
layout. Initialization creates a private file before SQLite access, or repairs
an existing safe file through a checked no-follow descriptor. URI targets and
implicit temporary databases are refused; explicit in-memory repositories
remain supported. See [ADR 0002](docs/adr/0002-local-storage-and-diagnostics.md).

`profile review` is physically read-only. Its output contains untrusted candidate
content for inspection, clearly marks every item unusable, and exposes the
creating workflow ID, proposal index, and a stale-safe review token; it cannot
record an approval or rejection. Before display it revalidates the exact current
import workflow/result identity, path-free source artifact, registered ingress,
unique one-to-one evidence link, immutable record digest, locator bounds,
selected-text checksum, and current content policy. Generic claim/evidence
mutation APIs reject `imported_resume`; only the import workflow can create that
provenance.

`profile decide` accepts exactly one claim ID, the displayed review token, an
`approve` or `reject` action, an opaque actor ID, and an opaque idempotency key.
Without `--confirm`, it performs storage-free syntax validation only and reports
`dry_run: true`, `storage_checked: false`, and `decision_recorded: false`; it does
not prove that the item exists, the token is current, or policy will permit the
decision, and it records no decision or external action. The preceding
`profile review` output is the content preview.

With `--confirm`, the CLI opens existing private storage and calls
`ProfileService.decide_review_item`, which revalidates the current item, token,
provenance, content, and decision policy in one atomic mutation. Approval moves
the claim/evidence pair to verified/approved and confirmed; rejection preserves
withdrawn/rejected terminal history. Confidential and highly sensitive imports
cannot be approved. An active same-subject explicitly contradicted record blocks
approval; public conflicts produce a minimized structured contradiction and
non-public conflicts produce a generic non-disclosing error. The command has no
bulk, force, edit, submission, or other external-action option. Confirmed success
and structured contradiction data report `external_action_taken: false`.

Successful decision JSON contains only the audit/status projection and opaque
record identifiers; it omits source text, proposed values, review tokens, file
paths, and raw idempotency keys. An exact confirmed retry returns the original
terminal result. If output fails after the atomic commit, or a confirmed command
is interrupted before reporting its terminal result, the CLI warns that the
decision may already be recorded; repeat the exact same confirmed request and
idempotency key to recover that result without creating a second decision. That
warning uses stderr because the requested stdout payload may already be partial.
With `--log-events`, it is preserved as a fixed recovery instruction in a
`decision_outcome_unknown` event. Diagnostic sink failures never repeat a command
and may leave partial or absent logs; the workflow audit remains authoritative.
Changed retry inputs fail closed. Approved imported claims are revalidated again
before they may enter a resolved claim packet. The unkeyed record hashes provide
consistency and stale-review detection, not authentication against a writer able
to recompute the whole database projection.

Manifest version 1, request-identity versions 1–3, result-manifest versions 1–2,
content-policy version 1, and the former public `CreateImportProposal` shape are
intentionally unsupported. Current imports use manifest 2, request identity 4,
result manifest 3, record digest 1, content policy 2, restricted taxonomy 1, and
database schema 4. Migration 002 upgrades the schema but deliberately does not
invent review associations or record digests for earlier imports. Regenerate
manifest-v1 input; for an earlier manifest-v2 workflow, use a fresh opaque
idempotency key in disposable synthetic state. There is no automatic policy
migration or reclassification. Legacy or earlier-policy rows remain unusable and
fail review/decision closed rather than being promoted or repaired.

## Product shape

The accepted architecture is a local-first Python modular monolith with:

- a typed `gapply` CLI as the primary machine contract;
- a relational, provenance-aware candidate evidence store;
- deterministic services for truth, permissions, state transitions, validation,
  and persistence;
- language models only at language-heavy edges, behind structured adapters;
- SQLite by default and private artifacts outside the public repository;
- optional local web, model, job-source, document, and browser adapters later.

The intended workflow is:

```text
approved evidence -> claims -> job requirements -> claim packet
                  -> verified prose -> rendered artifact -> human review
```

Job discovery and browser-assisted safe-fill remain roadmap work. Resume/PDF
generation and manual application tracking are part of the local pilot.
Future browser automation will stop at authentication,
CAPTCHA, sensitive or legal questions, ambiguous fields, signatures, and final
submission.

## Trust and privacy contract

- Every factual output must link to approved claim IDs or an approved,
  deterministic derivation.
- Missing, stale, contradictory, sensitive, or insufficient evidence becomes
  `NeedInfo` or `Contradiction`, never a guess.
- Imported pages and documents are untrusted data, not instructions.
- Permission to use an answer once is separate from permission to store or reuse
  it.
- Sensitive eligibility, legal, identity, and demographic answers are never
  inferred.
- Real candidate data, raw private artifacts, credentials, and unredacted logs
  never belong in this repository. Tests and demos use synthetic data only.
- The default product prepares, explains, validates, and pauses. A human performs
  the final application submission.

Repository agents and contributors must follow the complete rules in
[`AGENTS.md`](AGENTS.md).

## Documentation

- [`GROUNDED_APPLY_DESIGN.md`](GROUNDED_APPLY_DESIGN.md) — full product design
- [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md) — live resume point
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — delivery phases and acceptance gates
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — local workflow and handoff protocol
- [`docs/adr/0001-local-first-modular-monolith.md`](docs/adr/0001-local-first-modular-monolith.md) — accepted architecture

## Contributing

Start by following the resume protocol in `AGENTS.md`. Keep changes small, add
synthetic tests for new behavior, preserve unfamiliar working-tree changes, and
update the live checkpoint at every meaningful stopping point. A feature is not
implemented until its code exists and its documented verification passes.
