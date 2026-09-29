# Development guide

Grounded Apply is a public experimental alpha. Broader feature development is
paused; the requirements here still apply to fixes and contributions. See the
[README](../README.md) for current scope, [roadmap](ROADMAP.md) for unfinished
work, and [product vision](PRODUCT_VISION.md) for the intended experience.
Human time savings and independent user acceptance remain unmeasured.

This guide owns setup, verification and maintainer contracts. User workflows live
in the [daily quickstart](DAILY_QUICKSTART.md) and [operator reference](REFERENCE.md).
The [session handoff](SESSION_HANDOFF.md) records current verification; Git history
preserves earlier checkpoints.

## Prerequisites

- Python 3.12 or newer, a POSIX shell and Git.
- The base application has no third-party runtime dependencies.
- Encrypted backup/restore requires the optional `backup` extra (`cryptography`);
  there is no plaintext fallback.
- The `materials` extra supplies pypdf. PDF generation additionally requires local
  `pdflatex` with `lmodern`, `geometry`, `enumitem` and `needspace` on PATH.

No model SDK is used. `pyproject.toml` defines packaging with Hatchling, but source
checks do not require installing the package. The current implementation uses
POSIX paths; native Windows support is not verified.

## Canonical commands

Run commands from the repository root. `scripts/gapply` and `scripts/check` select
`GAPPLY_PYTHON`, then `.venv/bin/python3`, then `python3` on PATH, and reject an
unsupported Python before importing the application. `sh scripts/python ...`
uses the same selection for direct verification scripts. Create any virtual
environment with an explicit compatible interpreter; launchers install nothing.

| Purpose | Current command | Status |
|---|---|---|
| Full repository gate | `./scripts/check` | Supported now |
| CLI help | `./scripts/gapply --help`; `./scripts/gapply profile decide --help` | Supported now |
| Environment/path diagnostics | `./scripts/gapply doctor --json` | Read-only schema/path checks and database file headroom; advisory at 90% of the snapshot limit, unhealthy above it; no backup-validity or next-write guarantee |
| PDF prerequisite presence | `./scripts/gapply doctor --materials [--json]` | Separate intake/output dependency presence, no runtime or functional test; default doctor unchanged |
| Search resumption | `./scripts/gapply brief [--job-id ID] [--follow-up-days 7] [--json]` | Read-only validated snapshot; stage-based next actions, no scheduling or messages |
| Content-free command event stream | `./scripts/gapply --log-events doctor --json` | Supported; opt-in JSONL on stderr, private response on stdout |
| Structured profile proposal import | `./scripts/gapply profile import --source-file FILE --proposals-file FILE --idempotency-key KEY [--source-kind resume\|user-statement] [--retain-all-facts] [--dry-run] [--json]` | Default resume; opt-in user statements accept exact text/stdin and remain pending |
| Pending profile review | `./scripts/gapply profile review [--claim-id ID\|--claim-id-json JSON_STRING\|--limit 1..50 [--after CLAIM_ID\|--after-json JSON_STRING]] [--json]` | Read-only; chosen pending fact or bounded page; no-option response unchanged |
| One profile review decision | `./scripts/gapply profile decide --claim-id CLAIM_ID --review-token TOKEN --decision approve\|reject --actor-id ACTOR_ID --idempotency-key KEY [--confirm] [--json]` | Supported; storage-free syntax preview unless confirmed |
| Full test suite | `PYTHONPATH=src python3 -m unittest discover -s tests -v` | Supported now |
| Installed-package gate | `python scripts/check_package.py` | Supported with optional build tools; latest local gate verified on Python 3.12.14 |
| Encrypted profile backup | `gapply backup --encrypt ABSOLUTE_PATH [--dry-run] [--passphrase-stdin] [--json]` | Optional backup extra |
| New-home restore | `gapply restore --archive ABSOLUTE_PATH --target-home NEW_ABSOLUTE_PATH [--archive-sha256 HASH --confirm] [--passphrase-stdin] [--json]` | Write-free inspection unless explicitly confirmed |
| Required encryption gate | `python -W error scripts/check_backup.py` | Optional provider required; skipped tests fail the gate |
| Installed encryption gate | `python scripts/check_package.py --backup-wheelhouse ABSOLUTE_PATH` | Offline dependency wheels required; installs and exercises the extra |
| Portable-home deletion | `./scripts/gapply delete --target-home ABSOLUTE_HOME --receipt EXTERNAL_ABSOLUTE_PATH [--preview-token TOKEN --confirm] [--json]` | Read-only inventory preview unless confirmed; synthetic verification |
| Resume inventory | `./scripts/gapply profile extract --source-file FILE [--source-format auto\|text\|latex\|pdf] [--extractor-version 1\|2\|3\|4] [--json]` | Local text/PDF/static-LaTeX; no storage; every nonblank extracted line accounted for |
| Wrapped publication grouping | `./scripts/gapply profile group-publication --source-file FILE --source-sha256 TEXT_HASH --document-sha256 ORIGINAL_HASH --extractor-version N --indexes I,J [--indexes K,L] [--source-format auto\|text\|latex\|pdf] [--allow-partial] [--json]` | Read-only complete schema-2 proposal manifest; explicit disjoint publication groups, both hashes required for every format; no retention or approval |
| Complete or selected onboarding | `./scripts/gapply profile onboard --source-file FILE --source-sha256 HASH --extractor-version 4 --select all --idempotency-key KEY [--document-sha256 HASH] [--allow-partial] [--dry-run] [--json]` | Pending only; PDF/TeX require original hash. Numeric selections require version; `--retain-all-facts` selects policy 3 |
| Optional profile questions | `./scripts/gapply profile interview [--topic research] [--depth 1\|2\|3] [--after QUESTION_ID] [--limit 3] [--json]` | Read-only catalogue; no profile read, answer storage or durable interview state |
| Retained profile inventory | `./scripts/gapply profile inventory [--topic TOPIC [--limit 20] [--after ID\|--after-json JSON_STRING]] [--json]` | Counts-only overview or bounded exact topic context; all states retained, no usability/completeness/interview-progress assessment |
| Current verified projection | `./scripts/gapply profile show [--json]` | Read-only; includes effective retirements |
| Claim withdrawal/replacement | `./scripts/gapply profile retire --claim-id ID [--replacement-claim-id ID] --actor-id ACTOR --idempotency-key KEY [--preview-token TOKEN --confirm] [--json]` | Audited preview/confirmation |
| Job capture | `./scripts/gapply jobs add --url URL --source-file FILE --idempotency-key KEY [--dry-run] [--json]` | User-supplied UTF-8 snapshot, no fetch |
| Public job discovery | `./scripts/gapply jobs discover (--sources-file FILE\|--preset major-tech) [--title-contains TERM] [--location-contains TERM] [--missing-location include\|exclude] [--limit-per-source 100] [--dry-run] [--json]` | Bounded Greenhouse/Ashby/Lever/Workable and Netflix reads; dry-run fetches without storage, otherwise validated idempotent capture; incomplete coverage returns exit 2 with results |
| Public-board watchlist setup | `./scripts/gapply jobs sources (--url URL [--url URL...]\|--urls-file FILE) [--keep-valid] [--json]` | Offline recognition/deduplication; opt-in partial setup retains schema-1 manifest or null and reports rejected positions; unsupported sources remain manual gaps |
| Public-search planning | `./scripts/gapply jobs plan-search --role TERM [--role TERM] [--location TERM] [--json]` | Pure bounded literal-term plan; no profile, network or storage. Codex executes authorized searches separately and validates observed links |
| Batch preparation | `./scripts/gapply batches prepare --spec-file FILE --idempotency-key KEY [--dry-run] [--max-items 20] [--max-seconds 900] [--json]` | Closed saved-job specification, shared evidence choices, durable per-item progress and isolated blockers |
| Batch recovery/review | `./scripts/gapply batches resume --batch-id ID`; `./scripts/gapply batches show --batch-id ID`; `./scripts/gapply batches list` | Resume accepts the same invocation budgets; show/list are read-only; each supports JSON |
| Saved search scope | `./scripts/gapply searches configure --spec-file FILE --idempotency-key KEY [--dry-run] [--json]` | Immutable sources, shared evidence and bounds; v2 adds literal title/location preparation filters while preserving v1; syntax preview opens no runtime and makes no requests |
| Search-to-draft execution | `./scripts/gapply searches run --search-id ID --idempotency-key KEY`; `./scripts/gapply searches resume --run-id ID` | Public discovery, capture and child batch; both accept `--max-items 20 --max-seconds 900 --json` |
| Saved search review | `./scripts/gapply searches scopes`; `./scripts/gapply searches list [--search-id ID]`; `./scripts/gapply searches show --run-id ID` | Validated read-only views; each supports JSON |
| Search review copies | `./scripts/gapply searches export --run-id ID --output-dir ABSOLUTE_DIR [--dry-run] [--json]` | Implemented: one current-fact snapshot, private Markdown/JSON index and current material copies; no overwrite or approval |
| Daily search configuration | `./scripts/gapply schedules configure --spec-file FILE --idempotency-key KEY [--dry-run] [--json]` | Immutable saved-search/timezone/time/start-date/budget policy; installs no wake-up mechanism |
| Daily execution and review | `./scripts/gapply schedules tick --schedule-id ID [--dry-run]`; `./scripts/gapply schedules show --schedule-id ID`; `./scripts/gapply schedules list` | One bounded due occurrence or recovery attempt; read-only preview/show/list; each supports JSON |
| Daily control and delivery acknowledgment | `./scripts/gapply schedules pause --schedule-id ID --idempotency-key KEY`; `./scripts/gapply schedules resume --schedule-id ID --idempotency-key KEY`; `./scripts/gapply schedules ack --schedule-id ID --notification-id ID --idempotency-key KEY` | Pause fences dispatch; acknowledgment records delivery, never approval; each supports JSON |
| Job review/matrix | `./scripts/gapply jobs list`; `./scripts/gapply jobs show --job-id ID`; `./scripts/gapply jobs assess --job-id ID` | Read-only; each supports `--json` |
| Resume build | `./scripts/gapply materials build --job-id ID --claim-ids ID1,ID2 --idempotency-key KEY [--questions-file FILE] [--layout-file FILE] [--dry-run] [--json]` | Approved packets, versioned presentation, local LaTeX/PDF and exact extracted-text validation |
| Material inspection | `./scripts/gapply materials show --material-id ID [--json]` | Revalidates current facts and PDF |
| Saved material versions | `./scripts/gapply materials list [--job-id ID] [--json]` | Validated summaries; retired evidence yields needs_review |
| Material approval | `./scripts/gapply materials approve --material-id ID --bundle-sha256 HASH --actor-id ACTOR --idempotency-key KEY [--confirm] [--json]` | Requires human review of exact bundle |
| Material copies | `./scripts/gapply materials export --material-id ID --output-dir ABSOLUTE_DIR [--dry-run] [--json]` | New external private directory; exact replay, no overwrite |
| Questionnaire draft | `./scripts/gapply answers --job-id ID --questions-file FILE [--json]` | No storage; sensitive/unknown questions return NeedInfo |
| Application creation | `./scripts/gapply applications add --job-id ID --actor-id ACTOR --idempotency-key KEY [--dry-run] [--json]` | Initial discovered event |
| Application history | `./scripts/gapply applications list`; `./scripts/gapply applications show --application-id ID` | Read-only; each supports `--json` |
| Application transition | `./scripts/gapply applications transition --application-id ID --to STATE --actor-id ACTOR --idempotency-key KEY [--material-id ID] [--confirm-submitted] [--preview-token TOKEN --confirm] [--json]` | Append-only; applied records human submission, performs no external action |
| Support export | `./scripts/gapply export --redacted ABSOLUTE_FILE [--dry-run] [--json]` | Fixed version/count fields; excludes personal content, paths, identifiers and logs |
| Real PDF gate | `python -W error scripts/check_materials.py` | TeX + materials extra required; skips fail |
| Complete onboarding gate | `python -W error scripts/check_onboarding.py [--with-materials]` | Materials extra required; original PDF/TeX and no-CV user statements, all-fact paged review/approval/replay, partial refusal, interview/setup. Optional materials path requires TeX and verifies a Research-section PDF with historical audits; fresh installed pilot includes it |
| Workable discovery gate | `python -W error scripts/check_workable.py` | TeX + materials extra required; pure role/location planning, fictional observed links and company-board setup, standalone location selection before quota, capture/version replay, partial failures, two real PDFs, saved-search budget resume and quiet unchanged refresh; no network |
| Saved-job batch gate | `python -W error scripts/check_batch.py --with-backup [--demo-output NEW_EXTERNAL_DIR]` | Ten fictional jobs, two isolated blockers, real PDFs, replay/budget recovery and encrypted queue restoration |
| Configured-search gate | `python -W error scripts/check_search.py --with-backup [--demo-output NEW_EXTERNAL_DIR]` | Synthetic multi-source transport through actual adapters and CLI; real PDFs, changed/unchanged runs, budget resume, read-only one-folder review export/replay and encrypted restoration |
| Daily-search gate | `python -W error scripts/check_schedule.py --with-backup [--demo-output NEW_EXTERNAL_DIR]` | External synthetic clock/transport, actual CLI, eleven real PDFs, quiet notifications, missed/paused days, bounded resume and encrypted restoration |
| Advancing-source gate | `python -W error scripts/check_source_window.py --with-backup [--demo-output NEW_EXTERNAL_DIR]` | Actual CLI and Netflix parsers over fictional public responses; later-window matches, exact replay, changed head posting, nine real PDFs and encrypted cursor restoration |
| Preparation-filter gate | `python -W error scripts/check_search_filters.py --with-backup [--demo-output NEW_EXTERNAL_DIR]` | Actual CLI, filters before preparation cap, explicit missing-location choice, two real PDFs, exact replay and encrypted restoration |
| Source-rotation gate | `python -W error scripts/check_source_rotation.py --with-backup [--demo-output NEW_EXTERNAL_DIR]` | Three daily runs reach three automatic boards under one-request/one-job limits; isolated blocker, two real PDFs, stable replay and encrypted priority restoration |
| Storage-capacity gate | `python -W error scripts/check_storage_capacity.py [--workspace NEW_EXTERNAL_DIR]` | Both extras + TeX; daily preparation and read-only snapshot-repository checks above 16 MiB and near 256 MiB, exact encrypted CLI restore/replay and per-process memory/time measurements |
| Complete synthetic pilot | `python -W error scripts/check_pilot.py [--demo-output NEW_EXTERNAL_DIR]` | Both extras + TeX required; includes recovery and deletion |
| Installed pilot gate | `python scripts/check_package.py --pilot-wheelhouse ABSOLUTE_PATH` | Fresh offline install of both extras + full pilot; TeX required |
| Workflow expression validation | `actionlint -shellcheck= -pyflakes= .github/workflows/check.yml` | Supported with actionlint 1.7.12 installed; separate from application tests |


`./scripts/check` runs CLI smokes and the full suite against `src`, with warnings
as errors and no checkout bytecode. It needs no editable install. Use synthetic
data and an external disposable runtime, for example:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-dev ./scripts/gapply doctor --json
```

`doctor --materials` checks dependency presence only, without importing pypdf or
running TeX. A present dependency does not establish that PDF processing works;
use the real-PDF gates below. Its response contract is in the
[operator reference](REFERENCE.md#pdf-prerequisite-presence).

## Optional installed-package verification

Choose unused external temporary directories for these examples. `python3` must
resolve to Python 3.12+; substitute an explicit interpreter if necessary:

```bash
python3 -m venv /tmp/grounded-apply-build-tools
/tmp/grounded-apply-build-tools/bin/python -m pip install -r requirements-build.txt
/tmp/grounded-apply-build-tools/bin/python scripts/check_package.py
```

The gate builds an sdist and wheel outside the checkout, validates the wheel and
packaged migrations, then installs offline with `--no-index --no-deps` into a
fresh environment. Synthetic CLI checks run without source-path or user-site
imports. Build tools are optional development dependencies, not base runtime
requirements.

### Required encryption checks

Use the same disposable environment, then build a wheelhouse for a fresh offline
installation of the backup extra:

```bash
/tmp/grounded-apply-build-tools/bin/python -m pip install -r requirements-backup.txt
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_backup.py
/tmp/grounded-apply-build-tools/bin/python -m pip download --only-binary=:all: \
  --dest /tmp/grounded-apply-backup-wheels -r requirements-backup.txt
/tmp/grounded-apply-build-tools/bin/python scripts/check_package.py \
  --backup-wheelhouse /tmp/grounded-apply-backup-wheels
```

`check_backup.py` requires the provider and fails on skipped encryption tests.
A base-suite crypto skip does not verify encryption.

### Materials and complete pilot checks

After the build and backup setup above, install the materials extra, add its
wheels to the same wheelhouse, and ensure the TeX prerequisites are on PATH:

```bash
/tmp/grounded-apply-build-tools/bin/python -m pip install -r requirements-materials.txt
/tmp/grounded-apply-build-tools/bin/python -m pip download --only-binary=:all: \
  --dest /tmp/grounded-apply-backup-wheels -r requirements-materials.txt
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_materials.py
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_onboarding.py --with-materials
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_workable.py
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_pilot.py
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_batch.py --with-backup
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_search.py --with-backup
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_schedule.py --with-backup
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_source_window.py --with-backup
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_search_filters.py --with-backup
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_source_rotation.py --with-backup
/tmp/grounded-apply-build-tools/bin/python scripts/check_package.py \
  --pilot-wheelhouse /tmp/grounded-apply-backup-wheels
```

The gates use fictional profiles, jobs and source responses through actual CLI
and adapter boundaries. They render real PDFs and exercise replay, blockers,
budget recovery and encrypted restoration. They do not make live job-board or
model calls. Optional `--demo-output NEW_EXTERNAL_DIR` retains fictional artifacts
for visual inspection. Inspect representative PDFs when changing rendering;
extracted-text equality alone does not establish good layout.

The installed pilot also covers onboarding, Workable, batches, saved and daily
searches, source windows, filters, rotation and a database above 16 MiB. The
separate capacity gate covers both above-16-MiB and near-256-MiB profiles:

```bash
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_storage_capacity.py
```

It accepts `--workspace NEW_EXTERNAL_DIR` to retain measurements and
`--python PATH --installed` to exercise another fresh installed environment.
`--skip-maximum-probe` is a smoke check only. Run large capacity probes sequentially
so contention does not distort their memory/time evidence. See
[ADR 0011](adr/0011-supported-profile-capacity.md) for resource limits and what
these synthetic measurements establish.

### Hosted CI and workflow validation

[GitHub Actions](../.github/workflows/check.yml) tests Ubuntu 24.04 and macOS 15 on
Python 3.12 and 3.13. All four jobs passed the base suite, required encryption,
base packaging and offline installed backup checks in
[run 36638357341](https://github.com/chenyugoal/grounded-apply/actions/runs/36638357341)
for source `b27b140`. Full PDF/source pilots and installed pilot verification
remain local macOS/Python 3.12 evidence; CI does not install TeX. Consult the
[handoff](SESSION_HANDOFF.md) for the exact current local environment and results.

Jobs have a 60-minute bound. Actions are pinned, repository permissions are
read-only, checkout does not persist credentials, and no private artifacts are
uploaded. Keep pull-request checks on `pull_request`, never
`pull_request_target` with untrusted code.

Install [actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)
from its official release and verify the published checksum, then run the
canonical workflow-expression check separately from application tests. The
workflow assigns `RUNNER_TEMP` from its first shell step through `GITHUB_ENV`;
do not use `runner.temp` in job-level `env`, where that context is unavailable.
See GitHub's [context availability](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability).

## Source layout and dependency direction

Follow [ADR 0001](adr/0001-local-first-modular-monolith.md). The target boundaries
are `profile` (claims/evidence/policies), `jobs` (discovery/snapshots), `matching`
(requirements/gaps), `materials` (verified outputs), `applications` (append-only
history), `automation` (human-gated browser plans), `connectors` (external adapters)
and `agent` (Codex orchestration). A target boundary is not proof that its planned
features are implemented.

```text
CLI / future API -> application services -> domain and policies
                                      <- repository and external adapters
```

Domain code must not import CLI, web, ORM, browser or model SDK types. Prompts may
orchestrate typed services; truth, authorization, persistence and state transitions
belong in code. Use typed public interfaces, explicit enums, UTC timestamps,
stable IDs and deterministic policy functions. Relational records remain the
source of truth; retrieval indexes cannot replace provenance or policy checks.

`uv`, Typer, SQLAlchemy, Alembic, Ruff and Pyright remain planned hardening, not
prerequisites or supported checks. Adopt them only with working configuration and
verification, updating this matrix, README, AGENTS and the handoff together.

## Truth, privacy and runtime safety

The full rules in [AGENTS.md](../AGENTS.md) are mandatory. Generate factual units
only from approved, in-scope, current claim packets; preserve their claim-to-output
mapping and fail rendering/readiness when verification fails. Unknown or
contradictory evidence yields `NeedInfo` or `Contradiction`. Extraction confidence
never grants approval. Derived claims remain unusable until a registered
versioned evaluator can recompute their values from approved input claims.

Real candidate data must remain outside the repository, including fixtures,
commits and demos. Imported pages, documents and answers are untrusted data,
never tool instructions. Never infer sensitive eligibility, demographic, legal or
signature answers. Retention, approval and reuse are separate choices. Preserve
human stops for authentication, sensitive questions, attestations, ambiguous
fields, signatures and final submission. A recorded application state performs
no external action.

### Runtime database and sidecars

Respect `GROUNDED_APPLY_HOME`; otherwise use the existing XDG-compatible POSIX
locations. Never add a repository-relative fallback. At typed-CLI and public
SQLite adapter boundaries, database and SQLite `-journal`, `-wal`, `-shm` files
must be direct private regular files with exactly one hard link. Orphan sidecars
fail closed. Read-only diagnostics/review also require no sidecars and reject
persistent WAL mode, avoiding recovery, creation or removal of SQLite state.

Direct repository/inspection adapters own these guards; the CLI additionally
checks the full runtime layout and portable-root containment. Existing-only opens
must not create or repair files. Repeat path and identity checks around opens,
close failed connections, and reject URI-based bypasses; explicit internal
in-memory construction is separate. `profile init` creates its default config
through an exclusive no-follow descriptor and refuses unsafe existing configs
without changing them.

These are sampled checks, not an atomic filesystem lock or authentication
boundary. A same-UID process can win a race after the final check. Preserve this
residual TOCTOU limitation in code and documentation; see
[ADR 0002](adr/0002-local-storage-and-diagnostics.md).

### Backup, deletion and capacity

Encrypted profile archives cover the validated SQLite profile, not source files,
exports, configuration, caches or logs. Restore retains supported schema versions
4–7 exactly; it does not migrate implicitly. Upgrade explicitly with `profile init`.
Archive format 1 and passphrase handling are defined in
[ADR 0003](adr/0003-encrypted-profile-backup.md) and the
[backup reference](REFERENCE.md#encrypted-profile-backup-and-restore).

The supported database limit is 256 MiB and archive limit 384 MiB. Cooperative
30-second snapshot/validation limits do not preempt every native operation.
Capacity guards preserve committed work and never authorize deleting history.
Full filesystem backup, automatic retention and per-record deletion are unfinished.
Portable-home deletion requires an explicit target, preview, confirmation and an
external audit receipt. It is logical deletion, not secure media erasure; see the
[deletion reference](REFERENCE.md#portable-data-deletion) and
[ADR 0004](adr/0004-portable-data-deletion.md).

### Content-free diagnostics

`--log-events` emits JSONL on stderr with exactly `schema_version`, `event`,
`run_id`, `at`, `command`, `outcome` and `recovery`. IDs/timestamps are generated
internally; command/outcome/recovery values come from fixed registered sets. No
free text, caller metadata, paths, identifiers, exceptions, prompts or private
content may enter that API. Use `--json` for private errors/warnings on stdout;
never combine stdout with the event stream in a support log.

A broken event sink cannot fail or repeat a command. Events create no runtime
files or network requests and are not a durable workflow audit. Indeterminate
confirmed decisions and backup/restore results retain fixed same-request recovery
instructions. This boundary does not sanitize third-party output, terminal
recordings or shell history. Fixed-schema support export excludes personal
content, paths, identifiers and logs.

## Persistence and migrations

Never edit a runtime database by hand. Mutations use validated domain/application
services and typed CLI commands. Current schema support and registered migration
availability both end at 7. Each SQL migration has an immutable execution policy;
future, unversioned or inconsistent databases fail closed.

Schema validation reads version, tables and migration ledger in one snapshot,
releases its own read transaction before locking, and preserves caller-owned
transactions. Each migration revalidates under `BEGIN IMMEDIATE` and commits its
schema, ledger and version atomically. Ordinary initialization rejects a pending
conversion-only step before applying earlier steps in the requested range. An
initializer that races past an explicit target is refused. Capacity failure rolls
back that migration; earlier individually committed migrations may remain.

Read-only snapshot capture explicitly supports registered schemas 4–7.
`SQLiteProfileRepository.from_snapshot` validates current-schema bytes into an
owned read-only in-memory connection, without staging plaintext or offering a
writable bypass; callers close it. Trusted SQL comparison constructs a separate
empty in-memory database, never alters an existing profile. Snapshot construction
limits do not impose a deadline on all later queries.

Historical validation is distinct from current readiness. Preserve these
boundaries when changing adapters or audits:

| Surface | Required invariant |
|---|---|
| Material facts | Validate creation-time approved evidence, packet membership and peer context; later retirement may remain valid history. |
| Build/approval records | Strict closed JSON, exact typed identities/digests/artifact ownership, bound timestamps and completed workflows; consistency is not writer authentication. |
| Approval eligibility | Combine record checks, creation/approval-time facts and required-answer completeness; optional unanswered questions remain visible. |
| Application use | Check every ready/applied event, historical use time and immutable submission snapshot, including versions later replaced. |
| Batch history | Check every checkpoint, even drafts concealed by later blockers; retained partial/unapproved history is not current readiness. |
| Component inventory | Account for exact ownership of materials, claim links, approvals, applications, submissions, batches, leases and their workflows; orphan records fail closed. |
| Saved searches | Validate scope/run origin, linked workflow, raw JSON and timestamp bindings before checkpoint traversal. |

Ordinary approval reads distinguish absent approval from corrupt records, retain
current-fact blockers and use one read snapshot. Read transactions clean up only
what they own. Full search/schedule history, active-lease conversion admission and
aggregate custody validation remain unfinished. Detailed internal contracts and
source touchpoints are retained in
[ADR 0010](adr/0010-content-addressed-material-storage.md#current-implementation-boundaries-and-touchpoints).
No schema 8 or conversion command is implemented.

### Isolated material-payload preparation

The internal payload codec/table helpers described in
[ADR 0010](adr/0010-content-addressed-material-storage.md) are preparatory only.
They check exact bytes, kind, hash and length, with a 2 MiB per-kind bound and
caller-owned transactions. They confer no claim/approval authority and are not
used by production material reads/writes or a migration.

## Workflow contracts for contributors

Use the linked guides for complete user/JSON contracts rather than introducing
parallel definitions here:

| Area | Contract and developer boundary |
|---|---|
| Intake/review | [Profile setup](PROFILE_SETUP.md), [shared import/review reference](REFERENCE.md#shared-import-and-review-contracts), [ADR 0012](adr/0012-complete-profile-onboarding.md). Extraction is bounded local parsing; selected retention and approval remain separate. |
| Discovery | [Job discovery](JOB_DISCOVERY.md). Fixed public GET routes, no credentials, cookies, proxy inheritance, redirects or private profile context; response bounds and incomplete coverage remain explicit. DNS screening is not connection pinning or a portable DNS deadline. |
| Materials | [Operator reference](REFERENCE.md), [ADR 0006](adr/0006-local-application-pilot.md), [ADR 0008](adr/0008-versioned-resume-presentation.md). Current evidence and PDF text must validate; standalone answers write nothing and required unknown/sensitive answers block readiness. |
| Batches | [Batch preparation](BATCH_PREPARATION.md). Render outside write transactions; recheck lease generation and expected plan hash before commits. Reuse needs fresh facts, with no cross-call authority cache; reserve 256 KiB for checkpoints. |
| Saved searches | [Search runs](SEARCH_RUNS.md). Fetch with runtime closed; capture/checkpoint atomically. Immutable scopes, frozen selection, parent leases and preparation budgets also govern resumption. |
| Daily execution | [Daily searches](DAILY_SEARCHES.md). No installed wake-up mechanism or production clock override; notification acknowledgment means delivery only. |

Source gates use synthetic transports. Live external reads require separate
user authorization and bounded observations; a previous successful live request
does not establish current coverage. Browser filling, automatic final submission
and semantic resume rewriting remain outside the alpha.

## Structured profile import and review

This section owns the registered value schema linked from the
[profile guide](PROFILE_SETUP.md). Manifest and review response details live in
[the operator reference](REFERENCE.md#shared-import-and-review-contracts).

Manifest schema 2 binds exact source UTF-8 bytes by SHA-256 and zero-based,
end-exclusive Unicode-codepoint spans. The application recomputes the digest and
validates exact span text before any persistence. Duplicate fields, non-finite
numbers, unknown/authority fields, unsupported types and malformed versions fail
closed. Input files must be stable direct regular files read through guarded
no-follow descriptors; at most one input may use stdin. Candidate text never
belongs in inline CLI arguments or idempotency keys.

Vocabulary 1 contains career types, vocabulary 2 adds explicit contact/name types,
and vocabulary 3 adds `research_description`. Dispatch uses the smallest needed
registered vocabulary, preserving older schemas and replay identities. There is
no arbitrary JSON fallback. `TEXT(N)` means nonblank, already-trimmed text of at
most `N` Unicode codepoints, without CR/LF or control, format, surrogate,
line-separator or paragraph-separator codepoints:

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
| `research_description` (v3) | `TEXT(2048)`; explicitly classified research prose, with no inferred employment relationship |
| `skill_use` | `TEXT(256)` |
| `candidate_name` (v2) | `TEXT(256)`; explicitly supplied display name, not a legal identity inference |
| `contact_email` (v2) | `TEXT(320)` |
| `contact_phone` (v2) | `TEXT(64)` |
| `contact_location` (v2) | `TEXT(256)` |
| `contact_url` (v2) | `TEXT(1024)` |

Before its first storage call, import snapshots nested request state and validates
the whole batch. Source input is limited to 16 MiB UTF-8, CLI proposal input to
4 MiB and manifests to 1,000 proposals. Canonical text is bounded at 2,048
codepoints; each selected evidence item at 4,096 codepoints/16 lines; combined
evidence at 65,536 codepoints. Screened components are bounded at 8,192 codepoints
and repeated caller metadata at 1,048,576 persisted codepoints per batch.

Both content policies screen all persisted content/metadata through the registered
restricted-text taxonomy, including bounded percent decoding and recognized
split assignment keys. This is deterministic lexical defense, not a complete
semantic, secret or PII classifier. Policy 2 additionally rejects 80%-or-more
alphanumeric source coverage and whole-source reconstruction. Explicit
`--retain-all-facts` policy 3 permits complete selected factual retention while
preserving sensitive-data screening and other bounds. Neither policy proves
that a proposed value is semantically equivalent to its source; human review
remains required.

Resume provenance uses request/source-identity/record-digest versions 4/1/1;
explicit text user statements use 5/2/2. All version markers, reserved workflow
namespaces and per-record associations remain audited on replay and later reads.
A changed source origin cannot reuse an idempotency key. These digests establish
consistency, not authentication. Legacy path-provenance and earlier-policy imports
fail review/decision closed without displaying their old provenance. There is no
automatic policy migration or reclassification.

Local document intake limits input/extracted text to 16 MiB, PDFs to 100 pages
and parsing to 15 seconds. PDF extraction runs in a subprocess with stream and
available OS resource limits, not a universal memory sandbox. Static LaTeX never
executes includes or commands. Original document hashes guard intake but do not
create persistent original-file provenance. Line accounting, explicit publication
grouping and neutral section boundaries do not prove semantic completeness.
Interview/inventory reads add no saved answers, approval or interview progress.

## Focused regression checks

Choose the existing test modules for the rule you change, including its failure
modes and adjacent service/CLI boundaries. For example, a profile-review change
can be checked with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_profile_review_selection tests.test_profile_review_selection_cli tests.test_profile_review_pages tests.test_profile_review_pages_cli tests.test_logging -v
```

Then run `./scripts/check`. This covers the full unit suite and CLI smokes, so
there is no need to repeat overlapping historical test groups. Optional
provider/TeX, source acceptance and installed-package gates remain separate;
use the canonical matrix and setup commands above for the affected surfaces.

## Development workflow and handoff

Follow the mandatory [AGENTS.md session protocol](../AGENTS.md#exact-session-resume-protocol):
confirm the root, read the live checkpoint/roadmap/design, inspect existing work,
and run the handoff's **First command** before editing. Preserve unfamiliar work.
Choose a bounded authorized outcome; paused development is not a standing
instruction to start new roadmap features.

Implement through the domain/application boundary. Test the rule or failure mode,
using conspicuously fictional fixtures. Truth, sensitive-field, prompt-injection,
idempotency, invalid-transition and rendering failures need adversarial coverage
as their surfaces change. Unsupported factual claims in the golden corpus must
remain zero; a finite corpus does not prove universal factual correctness.

Before finishing, run narrow checks and all applicable canonical gates, inspect
the full diff for private data/secrets/generated artifacts/unrelated changes,
and run:

```bash
git diff --check
git status --short
```

A change is complete only with typed interfaces, structured fail-closed errors,
safe/idempotent persistence where applicable, passing required verification and
accurate user/developer documentation. Record precise pre-existing failures
rather than hiding them behind a passing unrelated check.

Update the single live [SESSION_HANDOFF.md](SESSION_HANDOFF.md) with exact commands
and outcomes, commit/working-tree state, decisions, known limits and the next
bounded task with an exact first command or file. Replace stale fields rather
than appending another session narrative; older evidence remains in Git history.
Use [ROADMAP.md](ROADMAP.md) for feature status and ADRs for architecture decisions.
Do not copy personal job-search state into development handoffs.
