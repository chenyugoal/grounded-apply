# Development guide

This guide describes the foundation and local application pilot. It separates
commands that work now from the target toolchain described in the product design.
The [daily quickstart](DAILY_QUICKSTART.md) describes user setup and review without
requiring command syntax; this guide owns the executable verification matrix.

The project is being wrapped up as an experimental alpha; broader feature work
is paused. User-facing scope lives in [README](../README.md), release changes in
[CHANGELOG](../CHANGELOG.md), and the intended product schematic in
[PRODUCT_VISION](PRODUCT_VISION.md). The verification and truth requirements below
still apply to fixes and contributions. Release wrap-up does not establish the
unmeasured human time-saving or hosted-platform acceptance criteria.

## Prerequisites

- Python 3.12 or newer
- a POSIX shell for `scripts/gapply`
- Git

The base bootstrap has no runtime dependencies. Encrypted profile backup/restore
uses the optional `backup` extra (`cryptography`); no plaintext fallback exists.
The `materials` extra supplies pypdf. PDF generation also requires local
`pdflatex` with `lmodern`, `geometry`, `enumitem`, and `needspace`. No model SDK is used.
`pyproject.toml` contains
packaging metadata and Hatchling as its build backend, but repository development
does not currently require installing the package.

## Canonical commands

Run commands from the repository root.

`scripts/gapply` and `scripts/check` use the same interpreter selection:
`GAPPLY_PYTHON` when set, then `.venv/bin/python3` when present, otherwise
`python3` on PATH. A version check runs before importing the application.
`sh scripts/python ...` uses this contract for direct Python verification scripts.
Use an explicit compatible interpreter to create `.venv`; its existence is not
assumed. No dependencies are installed by the launcher.

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

The full gate runs its CLI smokes and the test suite with warnings treated as
errors, without creating bytecode in the checkout. The wrapper adds `src` to
`PYTHONPATH`, so it exercises the
working tree without an editable install. For a disposable runtime root:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-dev ./scripts/gapply doctor --json
```

Do not use real candidate data in repository development.

### Materials dependency presence

`doctor --materials` branches before runtime resolution.
It calls `importlib.util.find_spec("pypdf")` and `shutil.which("pdflatex")` without
importing the optional provider or executing the binary. Each dependency state
is `present`, `missing` or `unknown` for a probe failure; exceptions and discovered
module/executable paths are not returned. The data contract is:

```json
{
  "schema_version": 1,
  "check_method": "dependency_presence@1",
  "dependencies": {"pypdf": "present", "pdflatex": "present"},
  "pdf_intake": {"prerequisites": ["pypdf"], "status": "present"},
  "pdf_materials": {"prerequisites": ["pypdf", "pdflatex"], "status": "present"},
  "functional_tests_run": false,
  "profile_read": false,
  "read_only": true
}
```

This example shows the all-present case, not a functional test result. A capability
is `missing` if any prerequisite is missing, otherwise `unknown` if any is unknown,
otherwise `present`. Exit 0 requires both dependencies present; other presence
results return exit 2 with fixed `MaterialsPrerequisitesUnavailable`. A missing
TeX executable can coexist with present PDF-intake prerequisites. Keep dependency
selection separate from candidate facts and base runtime health.

The feature resolves no runtime, reads no profile/source document, connects no
database/network, imports no `pypdf`, runs no compiler and installs nothing.
It adds no writes; this is not a claim about arbitrary interpreter bytecode
behavior. Probe/output failures stay fixed and private, with existing doctor
diagnostics only. Default doctor JSON/human behavior remains unchanged. Presence
does not verify TeX packages/fonts, PDF parsing/rendering, layout or material
readiness. The batch dependency-recovery hint uses `doctor --materials`;
normal runtime troubleshooting still uses base doctor.

The installed-package gate runs the actual opt-in entry
point before profile initialization, accepting only exit 0/2 consistent with
the presence result. Controlled installed probes cover missing dependencies and
unknown probe failures with import/process/runtime guards, even without a pilot
wheelhouse. These checks create no extra PDF or dependency installation; source
tests cover all four presence combinations and exact default-doctor compatibility.

Run the focused source checks with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_materials_preflight_cli tests.test_batch_cli tests.test_logging -v
```

### Optional installed-package verification

The zero-install gate remains dependency-free. To test distribution packaging,
create a disposable build environment outside the checkout:

```bash
python3 -m venv /tmp/grounded-apply-build-tools
/tmp/grounded-apply-build-tools/bin/python -m pip install -r requirements-build.txt
/tmp/grounded-apply-build-tools/bin/python scripts/check_package.py
```

The pinned build tools are development-only. The package check itself downloads
nothing: it builds a source archive and wheel using the installed tools, verifies
wheel contents, installs into a new temporary virtualenv with `--no-index` and
`--no-deps`, removes source-path/user-site imports, and runs installed help,
version, doctor, idempotent initialization, import, review, decision preview,
confirmed approval, and exact replay. It verifies migrations resolve inside the
installation. All runtime data uses synthetic fixtures in temporary paths.
This passes on the documented local interpreter; it does not establish a
cross-platform release matrix or MVP readiness.

`.github/workflows/check.yml` configures Python 3.12/3.13 on Ubuntu 24.04 and
macOS 15. Each job runs the base suite in a fresh dependency-free environment,
the required encryption gate, and both installed-package gates. Build tools,
dependency wheels, and all synthetic runtime state stay in runner temporary
directories. Checkout/setup actions are pinned by commit, permissions are
read-only, checkout credentials are not persisted, and no artifacts or runtime
logs are uploaded. This is ordinary `pull_request` CI, not privileged
`pull_request_target` execution. The hosted matrix has not yet passed in full;
see SESSION_HANDOFF for the reported job results and pending correction. Local
results cannot establish a successful hosted run of a patched revision.

When editing the workflow, also run the workflow expression validation command
above. Passing application tests do not validate GitHub's workflow syntax. Use
the official [actionlint 1.7.12 release](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)
and verify its published archive checksum when installing a binary; this is a
development-only tool, not an application dependency. The documented command
checks workflow syntax, context availability and action usage while disabling
the separate optional ShellCheck and Pyflakes integrations.

The runtime override is assigned in the first shell step using `RUNNER_TEMP`
and `GITHUB_ENV`, so later steps inherit an isolated path outside the checkout.
Do not reference `runner.temp` in job-level `env`: GitHub does not provide the
`runner` context there. See the official
[context availability table](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability).
Workflow validation happens before any test job starts. It is a separate gate
from successful execution of the application checks on every matrix target.

### Local pilot verification

The Codex entry point is `.agents/skills/grounded-apply/SKILL.md`; product usage
is documented in `CODEX_WORKFLOW.md`. The skill does not bypass service checks
or upgrade proposals into approved facts. `brief` reads validated profiles,
jobs, materials and application history in one read transaction. Its output
contains IDs/URLs/status/counts, but no raw career text, answer bodies, tokens,
or material bytes. Profile review counts are global even when filtering a job.
The configurable response interval is applied to recorded submission time;
it is neither an employer deadline nor a persisted or scheduled reminder.

Focused gate: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_briefing tests.test_launcher -v`.

See `QUICKSTART.md` for the user workflow. In a disposable environment with the
build and backup requirements already installed, add the material parser and
download wheels for a fresh offline installation:

```bash
/tmp/grounded-apply-build-tools/bin/python -m pip install -r requirements-materials.txt
/tmp/grounded-apply-build-tools/bin/python -m pip download --only-binary=:all: \
  --dest /tmp/grounded-apply-backup-wheels -r requirements-materials.txt
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_materials.py
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

`pdflatex` and its named TeX packages must already be available on PATH. These
gates make no network or model calls. The complete pilot creates fresh fictional
inputs and private runtime state, exercises preview/confirmation/idempotency,
checks isolated diagnostics, preserves the exact submission through encrypted
backup/restore, retires a claim, and deletes both synthetic homes with receipts.
`--demo-output NEW_EXTERNAL_DIR` retains a fictional bundle for PDF visual review.
The installed gate imports package code only from a fresh virtualenv and runs the
same CLI flow. Its harness supplies literal synthetic inputs, never a user's
runtime. Optional-provider skips in the base suite are not pilot release evidence.
With `--pilot-wheelhouse`, the installed gate also runs complete onboarding, Workable discovery and the real-PDF batch,
configured-search, daily-search, advancing-source, preparation-filter and
source-rotation flows.
It also exercises the larger-profile smoke gate above 16 MiB against the fresh
wheel. Run `scripts/check_storage_capacity.py` separately for the near-256 MiB
resource probe; `--skip-maximum-probe` is explicitly only a smoke check. The
capacity script accepts `--python PATH --installed` for another fresh installed
interpreter. Run large probes sequentially to keep memory measurements useful.
Search fixtures inject their transport from an external
temporary test module, prohibit real network access, and verify imports resolve
to the intended source tree or installed wheel. There is no product test mode.

Schema 003 stores append-only effective claim retirements; schema 004 stores jobs,
requirements, material bytes/mappings/approvals, application events, and submission
snapshots. Schema 005 adds immutable batch requests/items, append-only item
checkpoints and fenced expiring leases. Original import records remain intact
for exact provenance replay.
Schema 006 adds verified saved search scopes, run events, search leases and
child-batch links. Schema 007 adds daily schedules, occurrences, lifecycle events,
parent leases, child links and notification acknowledgments. Each public migration checks the
256 MiB allocation bound inside its transaction. An oversized migration rolls
back, leaving its prior version restorable; earlier successful migrations may
remain committed. No history is deleted to make an upgrade fit.
Current use must go through `ProfileService.validated_profile`, `packet_for_claim`
or `packets_for_claims`;
raw repository rows are historical storage, not authority. Independent career
bullets do not conflict just because they share a type. Singular facts and
explicit contradictions still fail closed. Derived claims remain unusable without
a registered evaluator.

Material factual units contain approved packet IDs, evidence IDs, text, packet
digests, and requirement associations explicitly marked as inference. The fixed
template preserves selected order within sections and source bullet markers;
it does not infer employment associations. The renderer escapes TeX input,
disables shell escape, uses private temporary build files and discards compiler
logs. Validation recomputes template text, PDF extraction, exact normalized text,
page/overflow limits, hashes, and current evidence. Unsupported text fails closed.
Normalization changes only whitespace and Unicode presentation forms. This is
not a claim of compatibility with every ATS or support for arbitrary languages.

Material plans automatically choose `approved_text_selection@3` with renderer
`grounded-apply.latex-resume@3` only when a selected resume claim has type
`research_description`. Those facts appear in a fixed Research section, with
Publications unchanged. Research headings retain the version-2 four-field text
order and explicit heading/bullet/paragraph choices. Nonresearch selections,
unselected research and answer-only research retain transformation 2. The planner
and builder share the same selection, preserving existing batch fingerprints,
child keys and approved reuse. Saved materials replay with their stored version;
version-1/2 renderer mappings and LaTeX remain unchanged. Old transformations
refuse the new research type. No automatic relabeling or schema migration occurs.

Material approval covers one exact bundle digest, including any answer specs.
Required unresolved answers block readiness. The application state records past
events; `currently_ready` separately revalidates a ready-for-review material.
Recording applied requires explicit confirmation of a human submission. Snapshots
retain the exact tracked bundle but cannot observe edits made at an external site.
No sensitive-answer memory, browser automation, or final submit action is
implemented. Public-feed fetching is available through `jobs discover`, and
`schedules tick` executes configured daily discovery/preparation. An external
wake-up mechanism remains separate; arbitrary page fetching is unfinished.
Current snapshots keep immutable historical bytes after
retirement, while current material inspection/export/readiness fails closed.
Application history validates every event timestamp as timezone-aware, including
its first event, and compares event order by instant rather than string order.
Saved approval must be at or before both ready-for-review and applied events;
the submission snapshot follows the same comparison. Equal instants with different
offsets are valid. Raw timestamp strings remain unchanged in event/workflow and
submission bindings and hashes. Invalid confirmed transitions roll back; reads
never repair stored records. This validates chronology, not factual eligibility
at every past application event or actor authentication.
Focused chronology gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_application_chronology tests.test_applications tests.test_briefing -v`.

Questionnaires stop at explicit requests to sign, e-sign or attest, including
questions containing career words and selected approved facts. They return
`human_answer_required`, no answer or factual mappings, and no external action.
Compact career noun phrases such as single sign-on, sign-on integrations and
sign language remain eligible; they do not exempt another sign request in the
same question. This is a bounded deterministic classifier, not general intent
recognition. The standalone `answers` command writes no runtime state. Required
unanswered signature questions block material approval. Both repository and
fresh installed pilot gates cover the signature and benign career cases.

### Isolated material-payload preparation

`repositories/material_payloads.py` is a preparatory adapter helper tested only
against in-memory payload tables. Normal schema-7 repository reads/writes, the
migration directory, CLI and archive format do not use it. It interns exact
PDF/LaTeX/text bytes by kind/digest, verifies length, digest and byte equality,
and preserves strict UTF-8 without normalization. Its 2 MiB per-kind bound is
an internal allowance, not a new guarantee that all historical text can convert.
Caller-owned transactions retain material rollback and coherent reconstruction;
the helper opens no file and creates no schema. Corruption remains a fatal
storage-integrity error. Fact, bundle and approval validation remain separate.
See [ADR 0010](adr/0010-content-addressed-material-storage.md) for the proposed
conversion release and its remaining gates.

Focused gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_artifact_storage -v`.
The tests use synthetic in-memory DDL rather than an unregistered production
migration. Full repository and fresh installed-package gates still apply.

### Saved-job batch verification

See [BATCH_PREPARATION.md](BATCH_PREPARATION.md) for the closed shared-evidence
manifest, CLI, review states and recovery. Schema 005 stores immutable requests
and items, append-only hash-linked checkpoints and expiring owner/epoch leases.
Rendering runs outside write transactions. Lease fences and a bound expected
material plan are rechecked before child writes; current evidence is rechecked
inside the material commit. A stale owner or changed evidence cannot commit a
child under an earlier preparation identity. Material writes reserve 256 KiB
for checkpoint progress under the 256 MiB supported database limit.

The ten-job real-PDF gate requires eight drafts and two isolated blockers, keeps
a useful partial resume when a required answer is missing, verifies exact replay
and budgeted recovery, and optionally restores the encrypted queue. It performs
no approvals or submissions. All generated fixture PDFs need visual review when
the rendering or output structure changes.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_batches tests.test_batch_cli tests.test_profile_batch_resolution tests.test_backup_schema_compatibility -v
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_batch.py --with-backup
```

Bulk claim resolution reuses one freshly validated read snapshot within a call,
not cached authority across calls or rendering. Regression tests retain exact
resolution outcomes and verify provenance changes, retirements and conflicting
singular facts. Performance observations in the handoff are local synthetic
measurements, not a measured improvement in user active time.
Material planning also resolves automatic name/contact selection and explicitly
selected facts from that same fresh snapshot. Rendering and material commit
still perform independent fresh validation; the pure selection helper does not
validate provenance or grant reusable authority.

Search-history audits retain every historical PDF, bundle, binding, workflow and
approval check, plus a fresh validated profile per batch. They resolve current
facts only for exact incoming posting versions that could reuse a draft. The
typed history result carries attempt counts and reuse eligibility, never a
user-facing readiness report; ordinary review still checks all displayed facts.
On the same restored 170-material synthetic history, one ten-PDF daily tick fell
from 67.254s to 28.330s. Historical PDF checks stayed at 350; historical current
plan/questionnaire calls fell from 350 to ten each. Timings reflect local load,
not measured user-time savings. No evidence authority is cached across calls.

### Configured-search verification

[SEARCH_RUNS.md](SEARCH_RUNS.md) documents the immutable scope and combined
review. Configuration stores no new candidate facts or approvals. Discovery runs
with the runtime closed, then atomically captures one bounded source window and
its checkpoint. Search-scoped leases fence source and child writes. Resume keeps
the frozen selection; a new run skips currently valid unchanged drafts before
its preparation cap. Validated historical submissions and explicit exclusions
are checked before preparation and material commit.

Saved configuration reads validate the closed origin and configuration-workflow
records in one owned or borrowed read transaction. Recursive raw JSON rejects
duplicate keys, nonfinite numbers and scalar type aliases; normalized manifests,
identifiers, digests and artifact ownership must agree. Both manifest versions
retain workflow input version 1. Completed workflow metadata and exact original
creation/start/finish strings are required; creation stays canonical UTC with
microseconds, while update ordering compares aware instants. Valid later or
equivalent-offset updates and harmless JSON formatting remain compatible.
Expected stored-record failures use the fixed SearchIntegrityError; missing
searches, invalid caller IDs, interruptions and caller-owned work retain their
existing contracts. These checks add no network, profile or PDF work and do not
establish full search-run/checkpoint/schedule custody or conversion admission.

Search-run reads also validate the closed four-field origin before consulting
its parent scope, then check the exact linked search_run_create workflow before
checkpoint traversal. Strict JSON, literal input version 1, parent/manifest and
deterministic run identity, single-artifact ownership, completed metadata and
raw creation/start/finish bindings must agree; update ordering is timezone-aware.
The existing canonical creation format remains required. One read snapshot is
owned or borrowed across origin, parent and the existing history checks. Missing
run/caller errors, parent-scope integrity errors and current-material behavior
retain their boundaries. SQLite transaction failures use a fixed run-integrity
error. This adds no parent/run chronology, checkpoint policy, lease or scheduler
rule; partial and legacy run histories remain subject to their existing checks.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_run_record tests.test_search_run_history -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_scope_record tests.test_search_scope_history -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_searches tests.test_search_policy tests.test_search_cli tests.test_migration_capacity -v
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_search.py --with-backup
```

The real-PDF fixture selects ten jobs across two feeds, isolates two injected
renderer blockers and retains a third source failure. Subsequent runs exercise
unchanged-feed advancement, changed versions and item-budget recovery. It checks
sixteen real PDF bundles, no new fact/material approvals or applications, unknown
questionnaire coverage, and encrypted restoration of scopes, runs and materials.
This does not measure user active-time savings or verify a daily scheduler.

Saved-search Netflix windows refresh one newest posting and advance through up
to six later entries under the existing ten-request source budget. Cursor
reservation and completion are validated search-ledger events; a later finished
generation cannot be overwritten by an older resumed run. Legacy search events
retain their exact bytes and hashes. Standalone discovery retains its bounded
first-window behavior. Counts describe each invocation, never complete coverage.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_netflix_windows tests.test_search_windows tests.test_schedule_windows -v
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_source_window.py --with-backup
```

The window gate starts with seven nonmatching postings, reaches matching later
windows, preserves progress through encrypted restore, detects a changed head
posting and checks nine real PDFs. Adapter regressions distinguish isolated
detail failures from source restrictions and count refused pre-GET budget
reservations accurately. Daily composition checks next-day advancement, quiet
same-day replay and interrupted-window recovery across midnight.

Version-2 saved scopes add closed literal title/location preparation filters.
Version-1 normalization and event shapes remain unchanged, and no SQL migration
is needed. Filters run before capture and preparation quotas, within the already
bounded adapter results. Unknown location defaults to include and stays visible
as `null`; exclusion requires the explicit saved policy. The two-PDF acceptance
gate reaches a later matching posting under a one-job cap and verifies both
missing-location choices, replay, immutable scope binding and encrypted restore.
Before each new source capture, validated historical item attempts order exact
posting versions least-attempted first, with stable adapter-order ties. Completed
current drafts still skip before quota use, and resumed selections stay frozen.
Across new runs, a durable reservation also rotates automatic sources for both
fetch/quota order and final round-robin. Manual gaps follow automatic sources;
retries keep their reservation, failed leases consume none, and started legacy
runs keep their original order. This is bounded fairness, not a coverage promise.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_filters tests.test_search_filter_integration tests.test_search_filter_cli -v
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_search_filters.py --with-backup
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_source_rotation tests.test_search_rotation -v
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_source_rotation.py --with-backup
```

### Daily-search verification

[DAILY_SEARCHES.md](DAILY_SEARCHES.md) defines immutable schedule policy, local
dates, bounded recovery and delivery acknowledgment. The service executes a
tick; a separately authorized wake-up mechanism must invoke it. Its external
test harness injects time and fictional feeds, prohibits real network access and
checks source/wheel import origin. There is no production clock override.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_schedule_policy tests.test_schedule_notifications tests.test_schedules tests.test_schedule_cli -v
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_schedule.py --with-backup
```

The actual-CLI fixture verifies eight initial drafts and two isolated blockers,
eleven real PDFs across changed postings and bounded recovery, quiet acknowledged
unchanged results, one latest occurrence after thirty offline days, separate
paused-day accounting and encrypted restoration. Core regressions cover local
clock changes, stale workers, interrupts, child commits before parent checkpoints,
exhausted-attempt reconciliation and altered histories. No personal schedule or
external wake-up mechanism is installed by these checks.

### Public discovery verification

Opt-in standalone `jobs discover --location-contains TERM` (repeatable) or
`--missing-location include|exclude` selects report schema 2. Without either
option, full schema-1 JSON/human output remains unchanged. The separate typed
`discover_with_locations` API shares bounded fetching with legacy discovery;
`SourceReport` and saved-search serialization remain unchanged.

The pure request validator checks all opt-in title/location terms, missing policy
and quota before source-file/stdin, runtime or transport access. Location terms
use existing `PreparationFilters` validation: at most 20 already-trimmed terms,
each 1–128 codepoints, with no case changes, trimming or deduplication. Literal
casefold substring OR applies within locations, AND after the title filter and
before the selected-job quota. Missing locations default to include and bypass
location matching, while still requiring the title match. `Remote` can match
`Not Remote`; selection never establishes geography or eligibility.

The separate `selections` rows account for every valid normalized record as a
title exclusion, known-location exclusion, excluded unknown, selected job or
quota-deferred job. Included unknowns before quota and selected unknowns are
separate counters. Provider observations, errors, scan limits, indexed/remaining
counts and manual gaps stay visible; no request budget, provider or saved-search
behavior changes. Capture uses the existing validated storage and immutable
posting contract. No-profile preview stores nothing, and diagnostics remain fixed.

Focused location gate:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_discovery_locations tests.test_discovery_locations_cli tests.test_discovery tests.test_discovery_capture tests.test_search_filters tests.test_workable_cli tests.test_logging -v
```

The shared Workable gate exercises later matching postings under quota one,
missing-location policies and counts, invalid selectors before source access,
legacy response compatibility, fixed diagnostics and filtered capture/replay.
Its original two PDFs and saved-search flow remain unchanged. The installed
package gate executes the same acceptance against the fresh wheel.

`jobs plan-search` calls the pure `build_public_search_plan` service. Input
counts are bounded before exact deduplication: 1–3 explicit roles and 0–2
locations. Terms retain punctuation, case and Unicode spelling; outer whitespace
is trimmed. Invalid Unicode, control/line-separator characters and blank or
over-128-codepoint trimmed terms fail with fixed errors. Human output escapes
Unicode format marks while JSON retains exact terms. No candidate-sensitivity
classifier interprets job-search intent as a candidate assertion.

Schema-1 plans contain literal role-only and role/location rows, seven fixed
supported board domains, a nine-query ceiling and an 18-distinct-link review
budget. They report no profile read, network access, storage change or established
coverage. They generate no URLs, shell commands, source manifest or engine query
syntax. Codex carries out authorized browsing separately, validates observed
links through `jobs sources`, and previews existing feeds without a profile.
Search snippets never become snapshots; guessed board tokens are prohibited.

Focused planning and integration gate:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_discovery_plan tests.test_discovery_plan_cli tests.test_discovery_sources tests.test_discovery_sources_partial tests.test_discovery_sources_partial_cli tests.test_workable_cli tests.test_logging tests.test_discovery_capture -v
```

`jobs sources --keep-valid` uses a separate pure tolerant builder; the default
strict builder and response stay unchanged. Every original input position appears
once in accepted inputs or fixed `invalid_url` / `source_limit_reached` rejections.
Mixed and all-rejected setup returns exit 2 with `IncompleteSourceSetup`; the
existing schema-1 manifest is retained when nonempty and otherwise null. Empty
or over-256 inputs fail at the top level. After 32 retained distinct sources,
new sources are rejected but duplicates of retained sources remain accepted.
File positions count nonblank parsed entries. Recognition never repairs URLs,
fetches, reads a profile or establishes live coverage; rejected URLs and exception
text are absent from reports and diagnostics. Setup rejections remain visible
alongside downstream source gaps.

The source and fresh installed `check_workable.py` flow starts with a nonexistent
runtime, checks literal planning and private diagnostics, then supplies an
explicitly fictional observed-link fixture containing a malformed lead. It pins
strict compatibility and opt-in partial accounting before feeding the retained
manifest into discovery. It preserves the original two-PDF acceptance. This verifies the CLI
bridge; it does not run an internet search or measure index coverage. A separate
bounded conversational forward review checks that the skill actually carries
source finding instead of handing query syntax to the user.

See [JOB_DISCOVERY.md](JOB_DISCOVERY.md) for the closed source manifest, coverage
states, network bounds and capture semantics. The source/normalizer registry and
the `job_discovery_capture` workflow type reuse schema-4 snapshot/audit tables;
no schema migration or legacy `job_capture` rewrite is involved. New captures
store source identity and per-job content hash, revalidate them on reads, and
roll back additions above the 256 MiB supported database bound. A stable internally
derived child key recovers a committed capture after interrupted output. This
is per-posting replay, not a persisted multi-stage search runner or historical
source-health service. Changed postings keep separate immutable versions.

The HTTP adapter sends only fixed public GET requests, with no candidate claims,
proxies, cookies, credentials, redirects or returned-link following. Host/path
allowlisting and public-address screening do not pin DNS; DNS resolution has no
portable hard deadline. HTTPS verifies the allowlisted host. Connection work has
socket timeouts and established-response reads have a cumulative shutdown timer.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_discovery tests.test_discovery_http tests.test_discovery_capture tests.test_netflix_source tests.test_jobs tests.test_briefing -v
```

These tests use synthetic transports and data. Live public-board smoke checks
are separate observations, make no profile reads/writes, and are not part of
the offline repository or installed-package gates. Never add real postings or
candidate data to fixtures. Record live source results and their limits in the
handoff without copying job descriptions.

### Optional encrypted profile lifecycle verification

Version-4 through version-7 snapshots are accepted only when their SQL structures and
migration ledgers exactly match the corresponding registered migration prefix.
Restore preserves the snapshot without migration. Run `profile init` explicitly
to upgrade an older restored home before using current data commands. Future,
unregistered or altered schemas still fail closed.

Use a disposable environment and wheel directory outside the checkout:

```bash
/tmp/grounded-apply-build-tools/bin/python -m pip install -r requirements-backup.txt
/tmp/grounded-apply-build-tools/bin/python -W error scripts/check_backup.py
/tmp/grounded-apply-build-tools/bin/python -m pip download --only-binary=:all: \
  --dest /tmp/grounded-apply-backup-wheels -r requirements-backup.txt
/tmp/grounded-apply-build-tools/bin/python scripts/check_package.py \
  --backup-wheelhouse /tmp/grounded-apply-backup-wheels
```

The base `./scripts/check` and discovery suite explicitly skip optional crypto
tests when the provider is absent; that is not encryption release evidence.
`scripts/check_backup.py` refuses a missing provider or any skip. The installed
encryption gate downloads nothing: it installs wheels from the supplied local
directory into a fresh virtualenv, then checks backup, preview, confirmed restore,
unchanged retries, and preserved import/decision provenance.

The version-1 archive is a bounded database snapshot encrypted by Fernet using
Argon2id (64 MiB, 3 iterations, 4 lanes, random 16-byte salt). It is an application
format, not age. The consistent SQLite backup and encryption stay in memory;
there is no plaintext backup staging file. The archive cap is 384 MiB, the supported database
cap 256 MiB, and capture/validation each have a thirty-second cooperative work
budget. These checks do not hard-preempt a blocking SQLite/C operation. Whole
snapshot encryption remains in memory; larger databases increase peak memory.
Canonical Base64 validation uses aligned 64 KiB chunks and preserves format 1. Unknown schemas,
SQL objects differing from local migrations, corrupt databases, broken foreign
keys, and non-digest filesystem artifact references fail closed.

Passphrases are 12–1024 UTF-8 bytes through no-echo terminal input (twice on
creation) or explicit bounded stdin, with no inline, environment, or file-secret
option. Python does not guarantee memory erasure or protection against swap/core
dumps. Archive size and Fernet creation time are visible. The database
includes generated PDF/LaTeX/text, job snapshots, answer bundles, and immutable
application history; schema 005 adds preparation queues/checkpoints and schema
006 adds saved-search state and schema 007 adds daily occurrences, checkpoints
and delivery acknowledgments.
Source documents, exported copies, config, browser state,
caches, and logs are outside this scope.
Encryption does not upgrade claim truth, status, or provenance.

All output/input paths are absolute, outside Git, under existing private direct
directories. Files must be private, direct, regular, single-link, and user-owned.
Output is exclusive/no-follow mode 0600 and fsynced; existing files are never
repaired or overwritten. A matching existing archive is authenticated before
returning its original identity. Read-only source guards also refuse sidecars
and persistent WAL, and repeat runtime and database identity checks.

Restore defaults to inspection without filesystem mutation. `--confirm` requires
the exact archive hash from that inspection and a new private target home under
an existing private parent. New directories and files are created exclusively;
the completion receipt is written last. A retry validates the receipt, full
target layout, database bytes, and safe default config. A changed, pre-existing,
or incomplete target fails without repair or deletion. Abrupt termination can
leave private partial output; inspect it separately and use a new destination.
Fsync is not a claim of power-loss testing. Existing sampled same-UID TOCTOU
limits still apply; see ADR 0003.

### Explicit deletion and retention

Deletion accepts only an explicit portable home and new external receipt path;
the environment does not supply a destructive target. Preview binds file hashes,
directory/file identity, and both destinations to a token. Confirmation repeats
that inventory and requires `--preview-token TOKEN --confirm`. Only known runtime
directories, the current profile database/config, and an optional restore receipt
are recognized. Unknown files, links, unsafe permissions, journals/WAL, and
changed state fail closed. Output/cache/artifact directories must be empty.
Database inventory permits the supported 256 MiB; config/restore receipts each
retain a 16 MiB bound. The aggregate is bounded at 288 MiB. Hashing uses 64 KiB
chunks with before/open/after/path identity checks and retains no whole-file copy.

The adapter uses directory descriptors and individual unlink/rmdir operations;
there is no recursive deletion. A private external JSONL receipt is fsynced before
removal, with completion appended last. It holds only hashes, counts, an opaque
operation ID, timestamp, and phase. Exact complete retries require an absent
target; partial receipts or targets are never silently resumed. Interrupted or
indeterminate deletion emits the fixed `deletion_outcome_unknown` event and
content-free receipt-inspection advice. External sources/backups and the receipt
remain. Only explicit whole-home deletion and caller-managed external archives
are available; individual job/material pruning and automatic retention remain
unfinished. Deletion is logical, and sampled same-UID TOCTOU limits apply;
secure erasure and power-loss durability are not promised. See ADR 0004.

Focused gate: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error -m unittest tests.test_deletion tests.test_logging -v`.

### Runtime database and sidecar safety

At the typed `gapply` boundary and every public SQLite adapter open, storage
validates the private database before SQLite access. An existing
database must be a direct regular non-symlink file with exactly one hard link and
a resolved location inside the private data directory and outside Git worktrees.
Existing-data commands and doctor additionally require the data directory and
database to have no group/other permissions; initialization may repair modes
only after the type/link/location checks pass.
`profile init` also rejects an existing nonregular, symlinked, multiply linked,
or group/other-accessible `config.toml`. A missing default is created with an
exclusive no-follow descriptor and verified before initialization proceeds.
Recognized SQLite `-journal`, `-wal`, and `-shm` sidecars must likewise be direct
regular non-symlink, single-link, user-private files. A recognized sidecar without
the main database is treated as potentially recoverable state: initialization,
`profile init --dry-run`, `profile import --dry-run`, and diagnostics fail closed
without creating a replacement database or changing the sidecar.

Read-only `profile review` and doctor database inspection require all recognized
sidecars to be absent. They also inspect the SQLite header without SQLite and
reject persistent WAL mode even after its transient sidecars have disappeared.
This prevents a nominally read-only connection from creating, recovering, or
removing sidecars. Mutating initialization, import, and confirmed decisions may
allow SQLite to process an otherwise valid private, single-link sidecar.
The abrupt-subprocess-exit test in `tests/test_crash_recovery.py` creates dirty
spilled pages and a genuine hot journal, verifies that mutating import restores
the original data before proceeding once, and verifies read-only and unsafe
journal refusal without mutation. Power-loss, disk-corruption, and arbitrary
journal-mode recovery are outside this evidence.

The CLI repeats validation around SQLite opens and schema inspection and samples
the database device/inode identity before and after access. These checks narrow
path-replacement and link-count races; they do not form an atomic filesystem lock
or authenticate state against another same-UID process. A same-user process can
still win a TOCTOU race after the final sample, so do not present this boundary as
protection from a malicious process running under the same account.

Direct `SQLiteRepository` and `inspect_schema` calls own the file/sidecar guards
and require a private parent outside Git. The CLI additionally validates the
whole runtime layout and `GROUNDED_APPLY_HOME` containment. Initialization uses an
exclusive no-follow descriptor to create a missing database and a checked
no-follow descriptor to repair an existing safe file's mode; SQLite receives an
already-private file through `mode=rw`. Existing-only/read-only access never
creates or repairs it. URI targets and implicit temporary databases are refused;
only non-existing-only repositories accept explicit `:memory:`. Connection setup
and context-manager initialization failures close the connection. The private
migration helpers accept an already-owned connection and are not public safe-open
alternatives. See ADR 0002 for the threat-model limit.

### Complete profile intake

[PROFILE_SETUP.md](PROFILE_SETUP.md) is the user/operator contract; ADR 0012
records the retention decision. `profile extract` accepts bounded text, PDF and
LaTeX files, or stdin with format selection. Text remains exact. PDF uses the
existing optional `materials` extra in an isolated child interpreter with a
15-second timeout, 100-page and 16 MiB input/text bounds, 4 MiB decompressed
stream limit, quiet stderr and available OS resource limits. A platform may
refuse address-space limits; this is not an OS sandbox or universal memory bound.
No OCR, network, include following or execution of input TeX occurs. Unsupported
TeX constructs are located as issues, and affected text is excluded. All formats
remain untrusted and require comparison with the original and explicit approval.

Extraction returns a full nonblank-line inventory of headings, proposed facts,
unclassified text and blocked locations, bounded to 10,000 lines/1,000 proposals.
Blocked values are not echoed in that inventory. Aggregate visible content uses
the import fragmented-assignment guard. A full line inventory does not guarantee
semantic completeness. Extractor version 2 adds research/teaching/publication/
achievement headings. Explicit version 3 classifies only Research, Research
Experience and Research Projects as `research_description`; proposal order, spans
and canonical wording stay unchanged. Default 2 and explicit version 1 retain
existing behavior. New conversational intake explicitly uses 4; old claims are
never relabeled automatically. Indexed onboarding requires the displayed version,
preventing silent index reinterpretation.

Explicit extractor 4 retains version 3's supported classifications and adds four
exact neutral boundaries: Research Interests, Academic Research, Selected Research
and Professional Memberships. Matching uses existing casefold/trailing-colon
normalization. The visible heading clears the prior type; subsequent nonlabel text
remains unclassified until a supported heading. This neither assigns a new fact
type nor treats interests or memberships as completed research or publications.
Versions 1/2/3, default 2, source hashes, spans, import identities and stored facts
remain unchanged. Neutral headings and unclassified text remain in the full
inventory; classification gaps are independent of `document.incomplete`.
This exact set does not detect every unsupported heading or join wrapped facts.

`profile group-publication` constructs a complete import manifest for explicitly
chosen groups of adjacent publication proposals. Repeat `--indexes` for separate
works, using the original displayed proposal indexes for every group. Both displayed hashes
and an explicit extractor version are required for every source format. Argument
shape checks precede document reading; the existing bounded reader checks exact
source bytes and extraction issues without accessing profile storage. Incomplete
documents require `--allow-partial`, which does not resolve classification gaps.

The pure `services/publication_grouping.py` re-extracts once with that version, requires
ascending consecutive publication indexes within each group and whitespace-only intervening text,
and preserves the complete contiguous evidence span. Only whitespace is normalized
in the combined value/canonical text; bullets, punctuation and status qualifiers
remain. The whole result passes one existing complete-facts import preview and size
limits before output. This helper does not infer that selected fragments describe
one work, change generic import's semantic review boundary, or persist original
document provenance.

One `--indexes` occurrence preserves the entire schema-1 output and human response.
It includes a closed schema-2 `manifest` with all other supported
proposals unchanged and in order, plus `index_mapping`, `grouped_indexes`,
`grouped_manifest_index`, original/result counts, original inventory and separate
unclassified/blocked counts. Inventory indexes still refer to the original
extraction; mapping rows connect each `original_index` to its `manifest_index`.
Two or more occurrences use report schema 2, replacing `grouped_indexes` and
`grouped_manifest_index` with `groups: [{indexes, manifest_index}]`. Groups stay
in request order; the manifest and complete index map always stay in source order.
Reversing the request order produces the same manifest and map. Adjacent disjoint
groups remain separate; duplicated or overlapping groups fail rather than merge.
The pure plural API accepts 1–500 groups with at most 1,000 total original indexes;
each group retains the existing 2–1,000 ascending consecutive exact-integer bound.
The CLI additionally bounds all index arguments to 6,000 characters before parsing.
Request checks precede document reading; one invalid group refuses the whole result.
No result is truncated to fit a limit or silently reduced to the last group.
Only `data.manifest` is valid input to `profile import --retain-all-facts`.
The helper retains nothing, reads no profile and records no approval. Its fixed
diagnostic command is `profile.group-publication`; content remains private stdout.
Human output and fixed failure messages cannot echo blocked content or exceptions.

Focused grouping gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_publication_grouping tests.test_publication_grouping_cli tests.test_neutral_sections tests.test_neutral_sections_cli tests.test_onboarding_cli tests.test_resume_documents tests.test_complete_fact_intake tests.test_logging -v`.
The shared source/installed onboarding gate exercises single and multiple explicit
text/PDF/static-TeX groups, reversed request order, distinct qualifiers and complete
pending imports with unchanged replay, without adding generated
material PDFs or changing the existing approval flows.

Focused section-boundary gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_neutral_sections tests.test_neutral_sections_cli tests.test_research_intake tests.test_research_cli tests.test_resume_extraction tests.test_resume_documents tests.test_onboarding_cli tests.test_complete_fact_intake -v`.
The shared source/installed onboarding gate exercises new text/PDF/static-TeX
boundaries without extra material PDF builds, while preserving the existing
research, statement, inventory, approval/replay and historical-material checks.

PDF/LaTeX onboarding requires the displayed original-document hash as well as
the extracted-text hash. Incomplete documents require an explicit `--allow-partial`
choice; omitted content remains a review gap. These checks bind the current
selection to the displayed file. The stored import provenance remains exact
extracted-text evidence/digest plus the registered manifest ingress. Original-file
hashes, parser versions and extraction issues are intake metadata, not a newly
persisted original-document provenance contract. No schema migration occurs.

The [complete-inventory operator recipe](PROFILE_SETUP.md#complete-inventory-manifest-for-operators)
documents the closed manifest projection for explicitly classified source rows,
preserving every supported proposal and keeping unresolved inventory separate.
It uses the existing original-document import and dry-run contract; no new
classifier, helper or storage behavior is introduced.

`--select all` retains all supported proposals under content policy 3; indexed
onboarding and structured import can explicitly choose it with `--retain-all-facts`.
Policy 3 removes source-percentage/reconstruction heuristics in every channel,
including generated metadata whose hashes can accidentally overlap a short fact.
All typed values, evidence/context checks, restricted/credential/fragment checks,
opaque metadata rules and per-field/batch size limits remain. Pending review and
explicit approval remain mandatory. New decisions inherit their import's exact
policy version. Policy 2 remains the default for old indexed/structured imports,
and historical review, replay and resolution support both exact integer versions.
Changing a policy changes the request identity; use a new key for a new request.

`profile interview` is a pure, versioned catalogue with nine topics, three exact
depth rounds and pages of 1–10 questions. Cursors must belong to the chosen topic
and round; skip/stop is always allowed. It accepts no answers, accesses no runtime
and does not claim durable interview progress or completeness. Retaining exact
answers uses `profile import --source-kind user-statement --retain-all-facts`
with explicit text/span proposals and separate per-fact approval. Search
preferences remain separate from career claims.

`jobs sources` recognizes strict supported board/posting routes locally, accounts
for every input, deduplicates boards and preserves safe unsupported HTTPS sites as
manual gaps. It accepts up to 256 links/32 unique sources, fetches nothing, and
never turns an unknown host into automatic network access. Known query/fragment
discarding is explicit: scope is the entire board, without original URL filters.
This improves watchlist setup, not aggregate market coverage or source verification.

Focused gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_complete_fact_intake tests.test_resume_extraction tests.test_resume_documents tests.test_profile_interview tests.test_discovery_sources tests.test_onboarding_cli -v`.
The required material gate includes actual PDF intake tests; provider skips cannot
verify PDF intake. The synthetic CLI onboarding gate is
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_onboarding.py`.
Add `--with-materials` for generated Research/Publications sections, unchanged
qualifiers/mappings, explicit material approval/replay/export and read-only
historical inventory/eligibility/use audits. This optional path requires TeX and
is included by the fresh installed pilot. The same checks cover a separate
no-CV text/stdin profile whose claim and evidence origins are `user_statement`.
Imported LaTeX is never compiled.

Focused research intake/material/batch gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_research_intake tests.test_research_cli tests.test_research_materials tests.test_research_batches -v`.

### Retained profile inventory

`profile inventory` reads one complete `ProfileService.validated_profile()` snapshot
through `ProfileInventoryService`, then counts every retained claim by exact type
and recorded status/approval pair. The default overview includes all nine fixed
topics and no claim IDs, text, values, evidence, raw types or paths. Unknown types
remain `other`; existing research imports are never inferred or relabeled.

`--topic` displays only exact claim IDs/types/canonical wording, status/approval,
origin, scope and sensitivity for that topic. It omits evidence, value JSON, source
references, artifact IDs and review tokens. Pages default to 20, allow 1–50 and
preserve existing claim order. All explicit pagination requires a topic. Anchors
include decided/retired records and remain exact legacy IDs; `--after-json` carries
nonprinting IDs safely. Before/returned/after counts cover the complete topic;
a null next anchor ends this pass rather than proving profile completeness.

Request syntax is validated before runtime access. Full profile validation occurs
before filtering, so an invalid off-page or unrelated-topic managed record blocks
the read with a fixed integrity error. Transactions, retirement projection and
read-only file guards remain unchanged. Counts do not resolve evidence for use,
approve facts, infer answered questions, save interview progress or account for
unretained source lines. Existing show/interview/review shapes stay compatible.

Focused gate:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_profile_inventory tests.test_profile_inventory_cli tests.test_profile_review_pages tests.test_profile_review_pages_cli tests.test_profile_interview tests.test_statement_import tests.test_statement_lifecycle tests.test_research_intake tests.test_logging -v
```

Source and installed `check_onboarding.py --with-materials` checks summary privacy,
exact topic continuation and unchanged runtime bytes/metadata, retaining the same
PDF, statement, approval and historical material checks. All fixtures are fictional.

### Structured profile import and review

`profile review --claim-id ID` opens one pending fact shown in retained-profile
inventory without needing its predecessor in the global queue. For an ID with
nonprinting characters, pass a safely quoted JSON string through
`--claim-id-json`. Both forms preserve exact existing nonblank UTF-8 identifiers,
including legacy spaces, punctuation and NUL; no UUID-only restriction is added.
The selectors are mutually exclusive with each other and all pagination flags.
Syntax validation precedes runtime access.

`ProfileService.get_review_item` validates the entire pending queue within one
read snapshot before selecting its existing item. The result preserves evidence,
current review token, untrusted status and global pending count; generic pending
items retain their null token. Missing or nonpending IDs fail without disclosing
another record. CLI read, conversion and output failures use fixed private errors.
The selected response keeps `items` (one item), `pending_count` (the whole queue)
and `read_only: true`, without `page`. It writes no records or review progress
and grants no approval. Default and paged review contracts remain unchanged.

Focused selection/page/diagnostic gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_profile_review_selection tests.test_profile_review_selection_cli tests.test_profile_review_pages tests.test_profile_review_pages_cli tests.test_logging -v`.
Shared source and fresh installed onboarding acceptance select an ID from topic
inventory, compare exact item/evidence/token and total counts, preserve unrelated
fact omission and source/runtime identity, and refuse malformed, unknown and
already-decided selections without new approvals or material builds.
See [chosen-fact review](PROFILE_SETUP.md#review-one-pending-fact) for operator examples.

`profile review --limit N [--after CLAIM_ID]` returns up to N pending facts at a
time, with N from 1–50.
The no-option form retains its original full-queue output and ordering. Paged
JSON adds `page` with `limit`, `returned_count`, `pending_before_count`,
`pending_after_count` and `next_after`; `pending_count` still counts the entire
pending queue. The three page counts sum to that total. Earlier counts include
a still-pending anchor, so reaching the end does not conceal skipped facts.
The last displayed claim ID continues the pass when later pending facts exist.
Its durable position survives an approval or rejection; restarting without
`--after` revisits earlier pending facts. No cursor or review state is stored.
The complete pending queue and any anchor provenance are validated in one read
snapshot before slicing. Pagination limits disclosure, not validation cost.
Per-item review tokens and explicit approval remain unchanged. Invalid syntax is
rejected before runtime access; unknown anchors return a fixed recovery error.
Anchors preserve existing nonblank claim IDs, including legacy generic IDs with
spaces or nonprinting characters. `--after-json JSON_STRING` is mutually exclusive
with `--after` and decodes a single JSON string before lookup, so a NUL-containing
legacy ID can cross the process argument boundary. Human continuation commands
quote printable IDs safely and use this encoded form for nonprinting IDs. The
JSON response always retains the exact original ID; it never becomes a path.

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

Structured `profile import` keeps that exact-text default. Explicit
`--source-format auto|pdf|latex` first uses the same local document adapter as
extraction/onboarding, with required original hash and partial-extraction choice.
The manifest's digest and spans then refer to extracted text. This lets an
operator classify an unresolved extracted line without asking the user to export
the document. No new persistent original-document authority is implied.

For exact answers supplied by the user, explicitly choose
`--source-kind user-statement`. The default `--source-kind resume` retains the
existing behavior and response shape. Statement intake accepts exact UTF-8 text
from a file or stdin with the same closed manifest; it refuses PDF/LaTeX/auto
extraction, `--document-sha256` and `--allow-partial` before reading inputs.
The opt-in CLI response adds `source_type: user_statement`. It stores only the
selected supported facts/evidence and digest identity, not an interview transcript
or a saved question cursor. `--retain-all-facts` is the explicit complete-facts
policy; answering or retaining does not approve the facts.

Focused statement gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_statement_import tests.test_statement_cli tests.test_statement_repository tests.test_statement_lifecycle tests.test_statement_materials -v`.

Resume provenance remains request 4 / source identity 1 / record digest 1.
Statement provenance uses the complete registered combination 5 / 2 / 2, with a
record digest that binds its source type. Source references use
`user-statement:sha256:<digest>`, artifacts use
`profile-user-statement-source:sha256:<digest>`, and the registered ingress is
`grounded-apply.profile-user-statement.manifest@1`. Review decisions inherit the
import's digest version. Generic legacy user statements retain their existing
contract, but cannot impersonate these reserved namespaces. Workflow-owned record
IDs and claim/evidence markers require managed auditing even when an association
is missing or a marker changes. A changed origin cannot reuse an existing key.
Normal pending review, explicit decisions, retirement, resolution and historical
material checks validate the same provenance; no database or material-version
migration occurs. These digests provide consistency, not writer authentication.

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

Manifest schema version 2 uses a closed, versioned value registry. Vocabulary 1
retains the career types below. Vocabulary 2 adds the five explicit contact/name
types; vocabulary 3 adds bounded scalar `research_description`. The dispatcher
validates against the smallest vocabulary used by each workflow. Recorded
vocabulary 1/2 schemas, replay identities and exact integer version checks remain
unchanged. There is no generic
JSON fallback for an unregistered claim type. In this table,
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
| `research_description` (v3) | `TEXT(2048)`; explicitly classified research prose, with no inferred employment relationship |
| `skill_use` | `TEXT(256)` |
| `candidate_name` (v2) | `TEXT(256)`; explicitly supplied display name, not a legal identity inference |
| `contact_email` (v2) | `TEXT(320)` |
| `contact_phone` (v2) | `TEXT(64)` |
| `contact_location` (v2) | `TEXT(256)` |
| `contact_url` (v2) | `TEXT(1024)` |

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
or PII classifier. Manifest 2, value vocabulary 1/2/3, content policy 2 or explicit
complete-facts policy 3, restricted taxonomy 1 plus its canonical SHA-256,
span-locator 1, result-manifest 3 and record-ID 1 participate in the request hash.
Request/source-identity/record-digest versions are the origin-specific 4/1/1 or
5/2/2 combination described above. The workflow audit records these versions.
That input contains only version identifiers, counts, digests, the registered
ingress ID, and digest-derived source/artifact references. It includes the
already hashed idempotency key so the lookup column is redundantly bound to the
audited input; it contains no raw source, proposed values, selected evidence,
file path, filename, or raw idempotency key.

Both policies limit canonical text to 2,048 code points. Each selected evidence item is
limited to 4,096 code points and 16 lines, and all selected evidence in one batch
is limited to 65,536 code points. **Legacy policy 2 only:** the batch rejects selected spans covering 80%
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
a source-kind-specific digest reference, byte and Unicode-codepoint sizes, and
versioned retention metadata; it stores no input path, original filename, or raw
source body. Resume claim/evidence pairs reference that artifact and the registered
`grounded-apply.profile-import.manifest@1` ingress identifier; user statements use
the registered identity described above. The identifier
names the application path that accepted the untrusted manifest; it does not
assert who produced the proposals. To prevent an answer-only selection from
hiding a sensitive label, the service screens up to 256 unselected code points
on either side within the selected logical line, including a same-line label
prefix, and the nearest nonblank exact recognized label-only line in a
512-code-point lookbehind. Blank extraction separators therefore cannot detach
an answer from its label. Label-only matching applies the same bounded
fixed-point percent decoding and Unicode/control normalization used by the
taxonomy without classifying unrelated unselected lines. Semantic contradiction
classification remains outside the bounded pilot; human review is required.
Content-free diagnostics, encrypted database backup, support export, and
whole-portable-home deletion are now implemented as described above. If more
than 512 code points precede the selection on
the same logical line, validation fails closed rather than silently treating a
truncated suffix as the complete label context.

`profile import --dry-run` performs service-owned request validation without
opening a repository or creating runtime state. The CLI still validates runtime
containment plus database/sidecar type, link, and orphan safety. Its
`storage_checked: false` field means it does not validate database permissions,
schema, artifact references, or prior idempotency-key use. A persisted import
requires an already initialized current schema. It opens that database in
existing-only SQLite `mode=rw`, validates the
current schema on the same connection used for the write, and never creates or
migrates storage implicitly. Missing, empty, or input-time-replaced databases
fail closed.

Import, review, confirmed decisions, and diagnostics revalidate runtime privacy
without repairing existing data as a side effect: the data directory and database
must retain user-only permissions, the database must remain a direct single-link
file inside the private data directory and outside Git worktrees, and every
portable runtime child must remain beneath `GROUNDED_APPLY_HOME`. Permission
drift, path escapes, database/sidecar symlinks or extra hard links, orphan
sidecars, and unsafe read-only journal state fail unchanged.

Persisted imports contain only `needs_review`/`pending` claims and pending exact
evidence spans. The public generic claim/evidence service rejects
`SourceType.IMPORTED_RESUME` and reserved managed-statement markers; only the
import workflow can create those managed records. Existing generic user-statement
creation remains compatible.
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

`profile decide` operates on one displayed claim ID and review token. It also
requires `approve` or `reject`, an opaque actor ID, and an opaque idempotency key;
the actor ID and idempotency key must not contain candidate data. Without
`--confirm`, the command validates only the typed input shape without resolving
runtime paths or opening storage. Its result explicitly reports `dry_run: true`,
`storage_checked: false`,
`decision_recorded: false`, and `requires_confirmation: true`. This is not a
current-item, token-freshness, contradiction, or decision-policy check; use
`profile review` to inspect the content before confirming. The syntax preview
records no decision and performs no external action.

`--confirm` is not abbreviable. A confirmed command opens the already initialized
database for validated mutation and calls `ProfileService.decide_review_item`.
One transaction revalidates the item, review token, import provenance, current
content, and policy, then records one approval or rejection. The workflow stores
only version identifiers, IDs, hashes, decision, actor, and time; it does not
store raw source, value, evidence, filename, path, or raw idempotency key.
Approval changes the claim to `verified`/`approved` and its evidence to
`confirmed` with the same actor/time; rejection changes them to
`withdrawn`/`rejected` and retains no verification actor. Confidential and highly
sensitive items cannot be approved. An active same-subject explicitly
`contradicted` record blocks approval. Public blockers return a minimized
structured contradiction containing IDs and remediation metadata; non-public
blockers use a generic non-disclosing error. The decision-time check does not
reject a distinct value solely because it differs.

Successful CLI output is minimized to decision/audit status and opaque record
identifiers; confirmed success and structured contradiction data report
`external_action_taken: false`. Output never returns source text, proposed
values, review tokens, paths, or raw idempotency keys. The command has no bulk,
override, edit, submission, or other external-action option. Exact confirmed
retries return the original result. If rendering fails after commit,
`PostCommitOutputError` says the decision may already be recorded. A confirmed
command interrupted before reporting its terminal result gives the same
conservative recovery instruction: retry the exact same confirmed request and
idempotency key. The warning is written to stderr because stdout may already
contain a partial payload and is indeterminate for that attempt. A changed retry
fails closed.
Existing resolution still returns `Contradiction`
when multiple approved value groups compete for one intent, and resolution first
revalidates every associated imported projection and terminal audit. The unkeyed
record digest is a consistency and stale-review control, not authentication
against a writer able to recompute the entire database projection.

Manifest version 1, request-identity versions 1–3, result-manifest versions 1–2,
content-policy version 1, and the former public `CreateImportProposal`
constructor are unsupported. Current resume imports use request identity 4,
source identity 1 and record digest 1; explicit statement imports use 5/2/2.
Both use manifest 2, result manifest 3, content policy 2 or explicit complete-facts
policy 3, restricted taxonomy 1, and database schema 7. Migration 002 creates the association/decision table but does
not fabricate record digests or associations for earlier imports. Regenerate
manifest-v1 input; for an earlier manifest-v2 workflow, use a fresh opaque
idempotency key in disposable synthetic state. There is no automatic policy
migration or reclassification. Legacy path-provenance or earlier-policy rows
fail review/decision closed without displaying their old provenance. Future
version changes likewise require a registered dispatcher or an explicit
migration; stored version tags do not by themselves grant compatibility.

Development verification uses synthetic data only. The bounded personal-use
workflow follows ADR 0006 and CODEX_WORKFLOW.md, with selected retention and
explicit fact approval in a private runtime outside the repository.

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
6. Run the canonical full test command and CLI smoke commands.
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

Schema validation reads `user_version`, table existence, and migration history
inside one transaction so another initializer's commit cannot mix database
revisions. It releases its own read snapshot before migration locking and leaves
caller-owned transactions untouched. Each migration still revalidates under
`BEGIN IMMEDIATE` and atomically commits its schema, ledger, and version update.
Future, unversioned, and inconsistent databases still fail closed.
Registered migration availability and ordinary schema support have separate
version bounds, both currently 7. Every SQL file needs an explicit immutable
execution policy. Ordinary initialization rejects a pending conversion-only
step before applying any earlier migration in that requested range, even with
an explicit target. A concurrent initializer advancing beyond an explicit
target is refused instead of silently returning a different version.
Snapshot validation obtains trusted SQL structure from a separate function that
constructs and closes its own empty in-memory database and returns only schema
rows. It cannot operate on an existing profile. This adds no schema 8, conversion
command or restore version; ordinary schema-4–6 upgrades to 7 remain available.
Focused policy gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_migration_policy tests.test_schema tests.test_backup_schema_compatibility tests.test_migration_capacity -v`.

`repositories/snapshot_capture.py` supplies a separate internal historical
capture adapter. It requires explicit absolute runtime paths and a nonempty
exact frozenset of source versions within 4–7 before filesystem access. It opens
read-only, checks runtime containment, source privacy and database identity, uses defensive/query-only
SQLite settings, and captures a pinned size-bounded image. Exact registered SQL,
ledger, integrity, foreign-key and artifact validation precede acceptance of the
captured version. A cumulative thirty-second cooperative deadline covers capture
and validation; native operations are not forcibly interrupted. All owned
connections close on failure, and cleanup preserves original interruptions.
Normal `SQLiteRepository` and backup entry points still require the current
schema. Their snapshot method reuses only the bounded copying helper. Capture
does not audit full historical factual/material custody, refuse active leases,
grant conversion admission or publish a new profile. Repeated filesystem checks
retain the documented same-UID TOCTOU limitation. The installed-package gate
exercises capture of all four historical schemas without changing their files.
Focused capture gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_snapshot_capture tests.test_backup.SnapshotTests tests.test_backup_schema_compatibility tests.test_storage_safety tests.test_migration_policy -v`.

`SQLiteRepository.from_snapshot(snapshot)` always validates the supplied bytes
and requires current schema 7, even though historical 4–6 images remain
restorable and capturable. It creates an owned defensive in-memory connection,
deserializes with trusted schema disabled, then enables query-only mode and
uses strict existing-schema initialization. It has no writable/trusted-input
bypass, raw connection parameter, migration option, staging file or runtime
fallback. The caller closes the returned repository or uses its context manager.
The existing 256 MiB image bound and a cumulative thirty-second cooperative
construction budget apply; no expiry handler remains on subsequent service
reads. No extra input image is retained after construction. Factual provenance,
historical material custody and current-use authority still require service
validation; this constructor alone grants none of them.

Repository read transactions preserve an existing caller transaction, handle
interruption immediately after BEGIN and release owned connections when rollback
fails. Explicit close always attempts connection close despite rollback failure;
context cleanup preserves the original error. The installed-package gate compares
profile evidence and job records through the snapshot adapter and rejects writes.
The capacity gate additionally measures capture and snapshot-repository reads at
both its >16 MiB and near-capacity checkpoints, including historical factual
validation of its real-PDF materials. The installed pilot repeats the >16 MiB
case with installed services.
Focused snapshot-repository gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_snapshot_repository tests.test_repository tests.test_snapshot_capture tests.test_storage_safety tests.test_schema -v`.

`MaterialService.validate_historical_facts(material_id)` is an explicit internal
per-material audit, separate from ordinary reads. One read transaction checks
existing PDF/bundle/workflow bindings, original validated profile records and
retirement audits. It reconstructs only recorded packet membership in original
profile/evidence order, applying job policy at material creation time. Exact
canonical text, packet hashes, requirement links, selected-unit order and
registered v1/v2 presentation rules must agree. Approval and confirmation actors
must be recorded; claim/evidence timestamps and support links must predate or
equal creation. Later retirement is retained as history; earlier retirement,
stale-at-creation evidence and generic records changed without reconstructible
history fail closed. Later duplicate or conflicting profile additions never
expand a saved packet or turn this result into current use authority.
Unchanged same-subject peers known to exist at creation are also checked, so a
rehashed packet cannot hide a historical contradiction or omit a required
compatible peer. This does not rerun automatic resume selection.

Questionnaire drafts use the same registered deterministic classification as
preparation and require exact selected factual mappings and newline-joined
canonical text. Saved NeedInfo results remain unanswered and retain nonempty
closed structured blockers, even after later approvals. This validates their
shape and existing bundle binding, not complete reconstruction of every past
resolver decision. Questions newly stopped by the standalone-sign correction
may retain their older closed unanswered blockers; they cannot retain an
answered draft. Earlier human-gated questions keep their exact required blocker.
This internal method raises fixed fatal
`MaterialHistoryIntegrityError`, preserves interruptions, writes nothing and
grants no approval. Full approval/application/workflow/orphan custody and
conversion admission remain unfinished; production schema stays 7.
Focused historical gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_questionnaire_signatures tests.test_questionnaire_history tests.test_material_history tests.test_materials tests.test_applications -v`.

The creation-facts path also validates strict linked material-build records before
walking profile history. The pure `material_build_history` helper checks exact
material/workflow field sets, recursive duplicate/nonfinite-safe JSON, raw/decoded
value agreement, scalar types, identifiers, bundle/input digests, one-material
artifact ownership and completed-workflow metadata. Creation, start and finish
retain their exact original timestamp strings; update ordering uses aware time
comparison. Harmless JSON formatting, valid offset timestamps and later updates
remain accepted. `approved_text_selection@1`, `@2` and `@3` keep literal integer
workflow payload version 1; `@2` and `@3` carry the registered presentation mapping.
No new payload-size bound is imposed on existing schema-7 material bytes.

Approval-fact, application-use and batch-history audits inherit this linked-build
check through their existing factual path. It reuses the loaded bundle/PDF and
one profile snapshot, preserves fixed fatal errors and borrowed transactions,
and does not alter ordinary `get`, current-fact blockers or the separate
approval-record-only contract. This still does not inventory orphan material
build/approval workflows or admit conversion. Source/installed pilots and both
capacity checkpoints exercise the same helper through their historical audits.
Focused build-record gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_build_record tests.test_material_build_history -v`.

`MaterialService.validate_historical_inventory()` audits the complete material
component in one read snapshot. Four fixed repository queries project material,
claim-link, approval and build/approval-workflow ownership without document columns,
joins or status filters. Exact closed rows and sorted ownership tuples must agree
with every audited material. Claim-link identity is the composite material/claim
pair, so multi-claim materials remain valid and duplicate pairs fail. Complete or
incomplete orphan workflows, missing/extra rows and wrong owners fail closed.

Each material receives one bundle/PDF validation and one historical profile walk.
Unapproved partial output remains history; a present approval must pass combined
creation/approval factual and required-answer eligibility. Later retirement remains
valid history. The audit covers saved output even when interrupted preparation has
not yet recorded a batch checkpoint. It derives claim ownership from already
validated packets and answer mappings, preserving answer-only claims and shared
links. Existing public absent-approval and record-only contracts are unchanged.
Fixed `MaterialHistoryIntegrityError` failures preserve caller work and interrupts;
no current readiness, preparation, approval or mutation is performed.

Source and fresh installed pilots audit all materials alongside application
inventories in original, restored and later-retired restored homes. Capacity checks
replace repeated per-material public audits with this aggregate check, retaining
exact raw material/approval comparisons, coverage counts and source file guards.
Those counts describe validated coverage, not public method invocation counts.
Other workflow components, active leases and whole-home conversion remain separate
unfinished gates. Focused material-inventory gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_inventory tests.test_material_inventory_repository -v`.

`MaterialService.validate_historical_approval_record(material_id)` separately
audits one optional saved approval against its existing historical bundle in a
single read transaction. Absence is valid unapproved history. A present record
requires exact material/workflow identities, bundle and input digests, an opaque
actor, a closed integer-versioned payload, expected completed-workflow metadata,
and no duplicate JSON object keys. Approval creation/start/finish strings retain
their exact original binding. Aware timestamp comparisons require approval at or
after material creation and workflow update at or after completion; equivalent
timezones are compared as instants, and normal later updates remain valid.

The method returns no readiness value, creates no approval, invokes no current
plan/answer resolution, and maps failures to a fixed fatal
`MaterialHistoryIntegrityError` while preserving interrupts. Intact retired or
partial material records can retain their approval record without gaining current
use authority. This is record consistency, not proof of actor authenticity or
fact/question eligibility when the approval occurred. Creation-time factual
auditing, approval-time authority, full workflow inventory and conversion
admission remain separate checks. Production schema stays 7. The capacity gate
compares exact source/snapshot approval records, audits both present and absent
records at both sizes, and proves one explicitly approved synthetic material
survives encrypted restore. Its installed >16 MiB run exercises the same audit.
Focused approval-record gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_approval_history -v`.

`MaterialService.validate_historical_approval_facts(material_id)` checks emitted
facts against both material creation and the timestamp from its validated saved
approval. It reads the historical bundle once inside one transaction. Original
claim approval, evidence confirmation and support links must still predate or
equal creation; later approval cannot repair missing creation-time authority.
The additional policy check preserves the exact creation packet while requiring
every packet member to remain usable and unretired at approval. Same-subject peers
known at approval are checked too: intervening conflicting or packet-expanding
peers fail, while unrelated facts and peers retired before approval do not expand
the recorded output. Dates use aware instants and half-open effective windows.
Questionnaire drafts, including answer-only facts, use the same checks.

This audit writes nothing, returns no readiness value, invokes no current plan or
questionnaire preparation and raises fixed fatal `MaterialHistoryIntegrityError`
on failure, preserving interrupts and caller-owned transactions. No approval means
only the existing bundle was checked here; the separate creation-facts audit is
still required for unapproved history. Saved NeedInfo stays unanswered. An intact
required-partial approval record can pass emitted-fact validation without proving
the approval decision was valid. Exact historical blocker decisions,
unreconstructible later generic edits, actor authentication and
aggregate workflow custody remain separate conversion gates. The capacity and
fresh installed gates exercise both present and absent approval paths with real
PDFs and exact encrypted restoration. Schema 7 remains unchanged.
Focused approval-time factual gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_approval_facts tests.test_material_history tests.test_questionnaire_history -v`.

`MaterialService.validate_historical_approval_eligibility(material_id)` combines
strict saved-record validation, creation/approval factual checks and recorded
required-answer completeness. After proving the closed question/answer shapes,
it requires every required answer to be a valid draft. Required NeedInfo fails
even if evidence became available later; optional NeedInfo and unapproved partial
history remain valid. Separate record-only and fact-only audits retain their
existing partial-history behavior.

The two approval-factual entry points share a private snapshot helper, so the
combined check reads one bundle/PDF and one original profile in one transaction.
It invokes no current preparation or readiness, stores nothing, preserves
interrupts and caller transactions, and reports fixed fatal
`MaterialHistoryIntegrityError`. It grants no current-use authority and cannot
authenticate an approval action, reconstruct mutable historical states or establish
coverage of unknown employer questions. Aggregate/orphan custody and conversion
remain unfinished. The capacity gate replaces its approval-facts call with this
combined check; its exact restore/readiness assertions remain unchanged.
Focused recorded-eligibility gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_approval_eligibility tests.test_material_approval_facts tests.test_material_approval_history -v`.

`MaterialService.validate_historical_use(material_id, used_at=...)` additionally
requires a present intact approval and checks the recorded facts at a required,
aware use timestamp. Creation <= approval <= use must hold; original claim,
evidence and support-link authority still belongs to creation. All recorded
packet members, effective windows and reconstructible peer context are checked
at all three instants. Required answers must be complete; optional NeedInfo is
preserved. Missing approval is fatal for this method, while the older optional
approval audits keep their absent-approval behavior.

`ApplicationService.validate_historical_material_use(application_id)` binds those
checks to every saved ready-for-review and applied event, including earlier ready
materials later replaced. One outer read transaction pins the job, events,
workflows, bundles, approvals, facts and submission. Each use event loads one
bundle/PDF and original profile; its applied pair is reused to verify the exact
submission snapshot without another PDF load. No eligibility result is cached
only by material ID. Ordinary `get` keeps its separate current-readiness behavior.
The audit invokes no current approval/readiness or answer preparation and creates
no output or approval. Fixed fatal RepositoryError sanitizes failures; interrupts,
caller-owned transactions and stored bytes are preserved.

This verifies reconstructible emitted facts and recorded required answers, not
actor authenticity, unknown employer questions, generic mutable history, complete
workflow schemas or orphan/aggregate custody. Schema 7 and the conversion gate
remain unchanged. The real-PDF source and installed pilots audit manual application
history before restore, after restore and after later retirement, checking exact
source bytes and filesystem inventory without network use.
Focused material-use gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_use_history tests.test_application_material_history tests.test_application_chronology tests.test_applications -v`.

Ordinary application history and the explicit use audit share pure validators in
`services/application_history.py`. Application origins, events, their completed
workflows and submission rows have closed fields and exact types. Recursive JSON
duplicate keys, nonfinite values, boolean/integer/float substitutions and
unsupported pending/retry/model/failure metadata fail closed. Submission values
must match the existing validated job/material/approval reconstruction at every
depth and retain the original digest. Harmless JSON whitespace, object ordering
and Unicode escapes remain valid; no stored JSON or timestamp is rewritten.

Workflow creation/start/finish strings bind exactly to the event timestamp.
Updates must be aware and at or after that instant; later updates, including
future values, remain valid. Replay requires the exact workflow linked to the
recorded event, preventing an unlinked workflow from claiming its artifacts.
Valid replay after later transitions and ordinary request/key conflicts retain
their behavior. Corrupt replay records are validated before comparing the caller's
request. Invalid new writes roll back; reads and interrupts preserve stored state.
These checks cover linked application records, not complete orphan/aggregate
inventory, actor authentication or conversion admission.
Focused application-record gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_application_record_history tests.test_application_material_history tests.test_application_chronology tests.test_applications -v`.

`ApplicationService.validate_historical_inventory()` checks application-component
coverage in one outer read snapshot. A metadata-only repository query inventories
application IDs, event/workflow ownership, submission references and every
application-create/transition workflow. Each application receives the existing
historical record/use audit once. Exact sorted ownership tuples, including their
multiplicity, must match the inventory; duplicate identities and unvisited rows
fail closed, including orphan workflows when no application exists.

The initial event and application origin correctly share one creation workflow.
Submission references come from already checked applied events, avoiding another
PDF read. Valid retired histories and earlier ready/replacement rounds remain
intact. No current-readiness/preparation call, output, approval or mutation occurs;
fixed fatal errors, interruptions and caller-owned transactions retain the same
boundaries. The repository API independently pins its four metadata reads when
called directly. Source and installed pilots now run the full inventory audit at
their original/restored/later-retired checkpoints. This checks application
coverage; other workflow kinds, active leases and whole-profile admission remain
separate gates. It does not establish large application-history performance.
Focused application-inventory gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_application_inventory tests.test_application_inventory_repository -v`.

`BatchService.validate_historical_materials(batch_id)` is an explicit audit of
one batch in a pinned read snapshot. It checks closed origin/item/event/lease
rows, recursive JSON duplicates and nonfinite values, exact integer versions and
positions, normalized manifests/specifications, hashes and deterministic IDs.
Its creation workflow must retain the exact expected input, completed metadata,
artifact and creation/start/finish bindings; a later aware update remains valid.
Batch and checkpoint times retain their existing canonical UTC microsecond form.

Every saved checkpoint is retained for this audit. Each distinct referenced
material is loaded and PDF-validated once, then checked against its creation-time
facts and optional approval record. Every reference still checks its preparation
binding and requires material creation at or before the checkpoint. Every draft
checkpoint requires completed recorded required answers; a later blocked state
cannot hide an invalid earlier draft. Exact reuse may predate the newer batch.
Unapproved, partial-blocked, interrupted and later-retired histories remain valid.
No current selection, questionnaire preparation, approval eligibility or readiness
call occurs. Queued checkpoints without material references need no profile/PDF
walk. The audit returns None, writes nothing and uses fixed fatal
`BatchIntegrityError`, preserving interrupts and caller-owned work.

This explicit path adds strict historical checks; ordinary batch review and reuse
keep their current-fact behavior. Lease shape is checked without admitting active
leases. Reverse inventory, orphan workflows, search/schedule custody and whole-home
conversion remain separate gates. The real-PDF batch gate audits its original and
encrypted-restored batch in quiet read-only subprocesses, checking exact source
bytes, mtime, runtime inventory and no external actions. The installed gate uses
the fresh installation's interpreter for these same checks.
Focused batch-history gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_batch_record_history tests.test_batch_material_history -v`.

`BatchService.validate_historical_inventory()` extends that audit to the complete
batch component in one read snapshot. Five fixed repository projections inventory
batches and their creation workflows, item ownership, checkpoint ownership, lease
membership and every exact-kind batch_create workflow regardless of status.
Closed metadata shapes, opaque identifiers and unique primary identities are
required. Every batch is audited once through the same private historical path;
ownership is projected from those already checked records and compared as exact
sorted tuples with multiplicity. Missing, extra, duplicate and equal-count wrong
ownership fail, including complete valid-looking orphan workflows with no batches.

No second material audit is performed merely to collect ownership. One bundle,
PDF and factual check per distinct material within each batch remains the rule;
cross-batch material reuse is independently audited in each batch. Partial,
retired, queued and interrupted histories retain their previous behavior. The
source and installed real-PDF gate now audits whole original/restored inventories,
with the expected synthetic batch present. Fixed fatal BatchIntegrityError,
interrupt identity, quiet unchanged files and caller-owned transactions remain
covered. The metadata repository method independently pins its five reads.
Lease membership/shape does not admit active leases. Uncheckpointed materials,
all-material/approval inventory, other workflow kinds and search/schedule custody
remain separate conversion gates. This is not a large-history performance result.
Focused batch-inventory gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_batch_inventory tests.test_batch_inventory_repository -v`.

Ordinary `MaterialService.is_approved` uses the same strict approval-record
validator, reading the material and optional approval together in one pinned
transaction. Missing approval remains false; valid optional unanswered questions
do not block readiness, while required unanswered questions do. A corrupt present
approval raises fixed fatal `MaterialApprovalIntegrityError` (a `RepositoryError`)
rather than false or an ordinary preparation blocker. This includes boolean/float
payload-version aliases, duplicate JSON keys and unsupported workflow metadata.
Current fact validation retains precedence: a retired claim can raise
`MaterialBlocked` before inspecting its approval. Explicit historical reads with
`require_current=False` still inspect the record. Nested reads borrow their
caller's transaction, including approval creation and replay, without committing
or rolling it back. These checks establish record consistency and current
readiness, not reconstruction of factual eligibility at an old approval time.
Focused ordinary-read and caller gate:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_material_approval_reads tests.test_material_approval_history tests.test_materials tests.test_applications tests.test_batch_search_history tests.test_search_review_exports -v`.

`tests.test_schema` forces another connection to migrate between validation reads
for both fresh and version-1 databases and retains the eight-worker initialization
test. The deterministic fixture uses WAL through private schema helpers so a
writer can commit during a read; public read-only WAL/sidecar guards are unchanged.

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

The implemented diagnostic boundary is stricter: `--log-events` must precede the
command and emits JSONL on stderr with exactly `schema_version`, `event`, `run_id`,
`at`, `command`, `outcome`, and `recovery`. Run IDs and UTC timestamps are generated
internally; command/outcome values are exact registered enums. Recovery is null
except for a fixed retry instruction after an ambiguous confirmed-decision
outcome. The API accepts no free-text fields, arbitrary metadata, exceptions,
caller identifiers, hashes, or paths. In this mode human stderr is discarded;
use the command's `--json` flag to receive errors and warnings on stdout.

Stdout is the private response channel and must never be combined with normal
events in a support log. Events create no runtime files and make no network
calls. A broken event sink cannot fail or repeat a command; it may leave partial
or absent events, which are observations rather than the durable workflow audit.
This boundary does not sanitize third-party output, shell history, or terminal
recordings. Optional encrypted profile backup/restore is implemented as described
above; whole-portable-home deletion and fixed-schema support export are also
implemented. Full filesystem backup, per-record deletion, and automatic retention
remain planned. Interrupted or indeterminate backup/restore results use the fixed
`backup_outcome_unknown` diagnostic outcome with a content-free same-request
retry instruction. No paths, passphrases, or archive content enter the events.

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

The handoff's top navigation links directly to current verification, limitations,
the next task and its first command. Completed older trials and verification are
preserved in the [September 21 history archive](SESSION_HISTORY_2026-09-21.md).
That archive describes earlier snapshots; it is not current verification or a
source of next-task instructions. Keep current contracts and unresolved limits
in the live handoff.

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
