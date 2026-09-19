# Development guide

This guide describes the foundation and local application pilot. It separates
commands that work now from the target toolchain described in the product design.

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
| Environment/path diagnostics | `./scripts/gapply doctor --json` | Supported now |
| Search resumption | `./scripts/gapply brief [--job-id ID] [--follow-up-days 7] [--json]` | Read-only validated snapshot; stage-based next actions, no scheduling or messages |
| Content-free command event stream | `./scripts/gapply --log-events doctor --json` | Supported; opt-in JSONL on stderr, private response on stdout |
| Structured profile proposal import | `./scripts/gapply profile import --source-file FILE --proposals-file FILE --idempotency-key KEY [--dry-run] [--json]` | Supported |
| Pending profile review | `./scripts/gapply profile review [--json]` | Supported, read-only |
| One profile review decision | `./scripts/gapply profile decide --claim-id CLAIM_ID --review-token TOKEN --decision approve\|reject --actor-id ACTOR_ID --idempotency-key KEY [--confirm] [--json]` | Supported; storage-free syntax preview unless confirmed |
| Full test suite | `PYTHONPATH=src python3 -m unittest discover -s tests -v` | Supported now |
| Installed-package gate | `python scripts/check_package.py` | Supported with optional build tools; verified on Python 3.13.1 |
| Encrypted profile backup | `gapply backup --encrypt ABSOLUTE_PATH [--dry-run] [--passphrase-stdin] [--json]` | Optional backup extra |
| New-home restore | `gapply restore --archive ABSOLUTE_PATH --target-home NEW_ABSOLUTE_PATH [--archive-sha256 HASH --confirm] [--passphrase-stdin] [--json]` | Write-free inspection unless explicitly confirmed |
| Required encryption gate | `python -W error scripts/check_backup.py` | Optional provider required; skipped tests fail the gate |
| Installed encryption gate | `python scripts/check_package.py --backup-wheelhouse ABSOLUTE_PATH` | Offline dependency wheels required; installs and exercises the extra |
| Portable-home deletion | `./scripts/gapply delete --target-home ABSOLUTE_HOME --receipt EXTERNAL_ABSOLUTE_PATH [--preview-token TOKEN --confirm] [--json]` | Read-only inventory preview unless confirmed; synthetic verification |
| Exact resume extraction | `./scripts/gapply profile extract --source-file FILE [--json]` | No storage |
| Selected onboarding | `./scripts/gapply profile onboard --source-file FILE --source-sha256 HASH --select 0,1,4 --idempotency-key KEY [--dry-run] [--json]` | Pending proposals only; source hash binds selection |
| Current verified projection | `./scripts/gapply profile show [--json]` | Read-only; includes effective retirements |
| Claim withdrawal/replacement | `./scripts/gapply profile retire --claim-id ID [--replacement-claim-id ID] --actor-id ACTOR --idempotency-key KEY [--preview-token TOKEN --confirm] [--json]` | Audited preview/confirmation |
| Job capture | `./scripts/gapply jobs add --url URL --source-file FILE --idempotency-key KEY [--dry-run] [--json]` | User-supplied UTF-8 snapshot, no fetch |
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

Schema 003 stores append-only effective claim retirements; schema 004 stores jobs,
requirements, material bytes/mappings/approvals, application events, and submission
snapshots. Original import records remain intact for exact provenance replay.
Current use must go through `ProfileService.validated_profile`/`packet_for_claim`;
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

Material approval covers one exact bundle digest, including any answer specs.
Required unresolved answers block readiness. The application state records past
events; `currently_ready` separately revalidates a ready-for-review material.
Recording applied requires explicit confirmation of a human submission. Snapshots
retain the exact tracked bundle but cannot observe edits made at an external site.
No sensitive-answer memory, network fetching, browser automation, or final submit
action is implemented. Current snapshots keep immutable historical bytes after
retirement, while current material inspection/export/readiness fails closed.

### Optional encrypted profile lifecycle verification

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
there is no plaintext backup staging file. The archive cap is 24 MiB, the database
cap 16 MiB, and capture/validation have five-second work budgets. Unknown schemas,
SQL objects differing from local migrations, corrupt databases, broken foreign
keys, and non-digest filesystem artifact references fail closed.

Passphrases are 12–1024 UTF-8 bytes through no-echo terminal input (twice on
creation) or explicit bounded stdin, with no inline, environment, or file-secret
option. Python does not guarantee memory erasure or protection against swap/core
dumps. Archive size and Fernet creation time are visible. The schema-4 database
includes generated PDF/LaTeX/text, job snapshots, answer bundles, and immutable
application history. Source documents, exported copies, config, browser state,
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

The adapter uses directory descriptors and individual unlink/rmdir operations;
there is no recursive deletion. A private external JSONL receipt is fsynced before
removal, with completion appended last. It holds only hashes, counts, an opaque
operation ID, timestamp, and phase. Exact complete retries require an absent
target; partial receipts or targets are never silently resumed. Interrupted or
indeterminate deletion emits the fixed `deletion_outcome_unknown` event and
content-free receipt-inspection advice. External sources/backups and the receipt
remain. Manual retention, logical deletion, and sampled same-UID TOCTOU limits
apply; secure erasure and power-loss durability are not promised. See ADR 0004.

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

Manifest schema version 2 uses a closed, versioned value registry. Vocabulary 1
retains the career types below. Vocabulary 2 adds the five explicit contact/name
types; the dispatcher validates against the smallest vocabulary used by each
workflow. Vocabulary-1 replay identities remain unchanged. There is no generic
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
or PII classifier. Manifest schema 2, request-identity schema 4, value schema 1 or 2,
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
constructor are unsupported. Current imports use manifest 2, request identity 4,
result manifest 3, record digest 1, content policy 2, restricted taxonomy 1, and
database schema 4. Migration 002 creates the association/decision table but does
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
