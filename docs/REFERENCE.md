# Grounded Apply reference

Command behavior, import schemas, and storage contracts for the experimental
alpha. Start with the [Codex workflow](CODEX_WORKFLOW.md) for conversational use,
[quickstart](QUICKSTART.md) for setup, or [roadmap](ROADMAP.md) for supported scope
and unfinished work. Run command examples from the repository root.

## Workflow references

| Task | Guide |
|---|---|
| Import, retain and review career facts | [Profile setup](PROFILE_SETUP.md) |
| Configure and preview public job sources | [Job discovery](JOB_DISCOVERY.md) |
| Prepare several saved jobs together | [Batch preparation](BATCH_PREPARATION.md) |
| Save discovery and preparation settings | [Saved searches](SEARCH_RUNS.md) |
| Trigger and review recurring runs | [Daily searches](DAILY_SEARCHES.md) |
| Check the verified release scope | [Current checkpoint](SESSION_HANDOFF.md#current-verification) |

`./scripts/gapply brief --json` gives a read-only next-action view across saved
jobs and applications. It orders work by workflow stage, not hiring probability.
Response-check suggestions neither send messages nor schedule reminders.

## Storage boundaries

Current profiles use schema 7. The supported database snapshot limit is 256 MiB;
`./scripts/gapply doctor --json` reports usage and warns from ninety percent.
Headroom is not a guarantee that the next operation will fit or that a backup is
valid. A run that stops for capacity retains prepared drafts in its report.
Near-capacity encrypted backup and restore used about 2.3 GiB of memory in
synthetic testing; see [ADR 0011](adr/0011-supported-profile-capacity.md).
Source documents, exports, configuration and logs are outside the encrypted
database backup. Keep those files separately if needed.

Internal preparation for exact-byte document sharing and historical audits exists,
but production conversion and deduplication are unfinished. Historical validation
does not authorize current reuse of retired claims or grant material readiness.
Full search-checkpoint and schedule custody remain unfinished. See
[ADR 0010](adr/0010-content-addressed-material-storage.md) and the
[development guide](DEVELOPMENT.md) for the internal boundaries. Ordinary profile
initialization never applies a conversion-only migration.

## Standalone location selection

`jobs discover` accepts repeated `--location-contains`
terms and `--missing-location include|exclude` for preview and capture. Either
explicit option selects data schema 2. With neither option, exact schema-1 JSON
and human output are preserved. `--missing-location` alone is valid, even with
no location terms.
See the [operator flow](JOB_DISCOVERY.md#location-selection).

The new report retains existing `jobs` and `sources` structures and adds
`location_contains`, `missing_location`, `selections` and the method label
`title_location_substring_or@1`. Each input source has a selection row, including
all-zero rows for manual, failed or empty sources. Location terms must already be
trimmed: at most 20, each 1–128 codepoints, following existing preparation-filter
validation. No normalization or deduplication is added. Literal casefold substring
OR applies within locations; title and location selection apply together before
the quota. Missing locations default to include and bypass location-term matching,
while remaining subject to title filtering. `Remote` can match `Not Remote`;
this tests published text, not geographic or workplace eligibility.

Each `selections` row contains `source_id` and these counts:

| Field | Meaning |
|---|---|
| `valid_count` | Valid normalized records available for selection |
| `title_filtered_count` | Records excluded by the title filter |
| `location_filtered_count` | Remaining records with known locations that fail location terms |
| `unknown_excluded_count` | Remaining unknown locations excluded by explicit policy |
| `unknown_included_count` | Eligible unknown locations before the selected-job quota |
| `selected_count` | Records selected within the quota |
| `selected_unknown_count` | Selected records whose location is unknown |
| `limit_deferred_count` | Eligible records left out by the selected-job quota |

`valid_count` equals title-filtered + location-filtered + unknown-excluded +
selected + limit-deferred. Unknown-included is a pre-quota count; selected-unknown
is a subset of selected. Source `filtered_count` totals title, location and
unknown-location exclusions, not quota-deferred records. `observed_count` remains
raw provider observations, not valid records or coverage.
Provider errors and `source_limit_reached` remain visible even when no job matches.

Arguments validate before source-file/stdin, runtime or transport access. Preview
needs no profile; capture checks authorized storage before network and preserves
existing immutable snapshot/replay behavior. The opt-in human report shows
published locations and retained unknowns. No adapter, network budget, saved
search, preparation-filter policy or persistence behavior changes.

## PDF prerequisite presence

`doctor --materials [--json]` checks PDF prerequisites before any profile or
storage setup. It is separate from the unchanged default
`doctor` runtime/database check. `pypdf` presence is enough for the PDF-intake
prerequisite; PDF material output also needs `pdflatex` on PATH.

Schema-1 data uses `check_method: dependency_presence@1`. `dependencies` reports
`pypdf` and `pdflatex` as `present`, `missing` or `unknown`. `pdf_intake` names
`["pypdf"]`; `pdf_materials` names `["pypdf", "pdflatex"]` in their respective
`prerequisites` arrays, with a derived `status`. Any missing prerequisite makes
that capability `missing`; otherwise an unknown probe makes it `unknown`.
Only all-present prerequisites produce `present`. Read the intake status
separately: a missing TeX executable alone does not prevent PDF input.

Exit 0 means both dependencies were found. Missing or unknown requested materials
prerequisites return exit 2 with fixed `MaterialsPrerequisitesUnavailable`;
no paths or underlying probe exceptions enter the response. The report sets
`functional_tests_run: false`, `profile_read: false` and `read_only: true`.
It locates the module/executable without importing `pypdf`, running TeX, parsing
a source, opening runtime storage, contacting a provider or installing anything.
It adds no writes. Presence cannot establish working PDF rendering, complete TeX
packages/fonts, layout quality or release readiness. See [setup](QUICKSTART.md#start)
for installation; a batch dependency-failure hint points to this opt-in check.

## Runtime and material versions

Prerequisite: Python 3.12 or newer. The repository launcher uses `GAPPLY_PYTHON`
when supplied, otherwise `.venv/bin/python3` when present, otherwise `python3`
on PATH. Unsupported interpreters stop with setup guidance. For example,
`GAPPLY_PYTHON=/absolute/path/to/python3.12 ./scripts/gapply --help` needs no
shell activation. The base CLI has no runtime package
dependencies and does not require installation. Encrypted backup/restore requires
the optional `backup` extra; PDF generation/verification requires the `materials`
extra plus `pdflatex`, `lmodern`, `geometry`, `enumitem`, and `needspace`. The
quickstart explains the isolated environment. See the
[checkpoint](SESSION_HANDOFF.md#current-verification) for verified platforms.

New resumes use a clean sans-serif layout with section rules, compact role and
education headings, and source-aware bullets. Codex can choose heading, bullet,
or paragraph presentation with `materials build --layout-file FILE`; the closed
JSON format is in [the quickstart](QUICKSTART.md). Approved wording and PDF
text checks remain mandatory. Existing version-1 and version-2 materials retain
their original layout and approvals; a new layout produces a new draft requiring
review.

Material planning selects `approved_text_selection@3` with
`grounded-apply.latex-resume@3` only when a
selected resume claim has type `research_description`. It adds a fixed Research
section; Publications remains separate. Unaffected selections keep version 2,
including profiles with unselected research and research used only in answers.
Stored versions 1 and 2 retain their rendering and replay behavior; old claims
are not relabeled and old materials are not rebuilt automatically.

For setup and installation, use the [quickstart](QUICKSTART.md#start).
Contributor checks and toolchain status live in the
[development guide](DEVELOPMENT.md#canonical-commands).

Optional diagnostic events use `./scripts/gapply --log-events doctor --json`.
Stderr then contains only fixed-schema command events; stdout remains private
and may include paths or candidate data. No files or telemetry are created.
Events exclude arguments, exception text, source content, tokens, and keys.
Use command `--json` to retain normal errors and warnings on stdout in this mode.

### Portable data deletion

`./scripts/gapply delete --target-home ABSOLUTE_HOME --receipt EXTERNAL_ABSOLUTE_PATH`
previews deletion of a dedicated portable runtime. To perform it, repeat the
same command with `--preview-token TOKEN_FROM_PREVIEW --confirm`. `--json` is
supported. No environment default selects the deletion target. The receipt must
be a new file under a private directory outside the target.

The command refuses changed previews, unknown files, links, unsafe permissions,
SQLite sidecars, databases over 256 MiB, config/restore receipts over 16 MiB,
and nonempty artifact/cache/output directories. Inventory hashing reads bounded
chunks instead of loading whole files. An external
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

The archive contains one consistent database, up to 256 MiB, including approved
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
See [ADR 0003](adr/0003-encrypted-profile-backup.md) for format and limits.

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
backup/restore and deletion. Questionnaire checks require a human answer for
explicit requests to sign, including when career facts are selected; ordinary
certification, single sign-on and sign-language career questions remain usable.
The older structured-import example is:

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

By default, `--source-file` and `--proposals-file` accept a regular UTF-8 file or `-`
for stdin, but only one input may use stdin. Resume import also accepts an
original PDF/TeX source with `--source-format auto` and the displayed
`--document-sha256`; manifest spans and its source hash refer to extracted text.
Incomplete extraction requires an explicit `--allow-partial` choice. See
[profile setup](PROFILE_SETUP.md) for the complete-inventory workflow.
Candidate text and proposed values have
no inline command-line option. Dry-run performs persistence-independent request
validation and reports `storage_checked: false`; it checks runtime containment
plus database/sidecar type, link, and orphan safety, but does not create or open
the repository or inspect its schema or database permissions. A real import
requires an earlier `profile init`, remains
idempotent under its opaque key, and creates only pending, unusable records.
The strict proposal manifest is schema version 2. It carries the SHA-256 of the
exact source-text UTF-8 bytes (extracted text for PDF/TeX) as a consistency assertion, plus span conventions and
proposals; it cannot supply a source path, artifact ID, extractor identity, or
trust state. The application recomputes the digest, derives a path-free
`sha256:<digest>` resume source reference and deterministic digest-only artifact, and
assigns the registered manifest-ingress identifier. A matching digest proves
that the two inputs belong together; it does not authenticate who authored the
source or proposals.

For the exact closed fields and an original-document preview, use the
[complete-inventory operator recipe](PROFILE_SETUP.md#complete-inventory-manifest-for-operators).
Project supported extraction rows instead of copying their domain metadata,
preserve all other proposals, and add only explicitly classified exact inventory
spans. Keep remaining gaps and headings separate from the manifest; this does
not rewrite the extraction report or approve facts.

### Explicit publication grouping

`profile group-publication` prepares one or more explicitly chosen groups of
consecutive `publication` proposals. It requires the displayed
extractor version with no default, plus extracted-text and original-document
hashes for all formats. It re-reads the original through the same bounded adapter,
refuses stale hashes and non-whitespace gaps, and retains the complete contiguous
evidence span. Proposed value and canonical text change only whitespace; words,
punctuation, bullets and status qualifiers remain intact. No layout relationship
or publication status is inferred.

Repeat `--indexes` for each explicitly
chosen work; every index refers to the original displayed extraction. A single
occurrence preserves the existing schema-1 report, including `grouped_indexes`
and `grouped_manifest_index`. Two or more groups use report schema 2, replacing
those singular fields with `groups: [{indexes, manifest_index}]`; all other
report fields retain their meanings. The import manifest remains schema 2.

The command accepts groups in either request order. Report `groups` follows that
request order; the manifest and `index_mapping` always follow original source
order. Reversing the options produces the same manifest and mapping. Adjacent
groups stay separate; overlap and duplicate selections refuse rather than merge.
Bounds are 1–500 groups, each with 2–1,000 consecutive ascending integer indexes,
and at most 1,000 members total. These shapes are checked before input is read.
One document read, extraction and final whole-manifest preview covers the call.

`data.manifest` is the existing schema-2 import format. All other supported
proposals stay in order; each chosen group is replaced once. The report retains
`index_mapping` rows of `original_index` and `manifest_index`, both proposal
counts, the original inventory and gap counts separately from document
incompleteness. Existing claim/evidence/batch limits apply; grouping
does not truncate or silently omit a proposal. The command reads no profile and
stores no runtime state, retention choice or approval. See the [operator flow](PROFILE_SETUP.md#publication-grouping-for-operators)
for saving only the manifest to a private temporary file and importing it with
the same original document/hash, complete-facts policy and ordinary pending review.

This helper adds no extractor, claim type, stored schema or material version.
The original PDF hash remains an invocation guard, not durable original-document
provenance. Generic pending import verifies exact evidence and typed/content
bounds, not semantic equivalence of canonical wording to evidence: a shortened
title can pass preview despite qualified evidence. The helper's whitespace-only
transformation preserves its own output, and explicit fact approval remains required.

### Retained user statements

`profile import --source-kind user-statement` takes exact chosen answers through
the same schema-2 proposal manifest, pending review and per-fact approval flow.
Codex asks a few questions, shows the proposed wording and obtains the retention
choice before import. Use `--retain-all-facts` for complete-facts policy 3; it
removes the legacy percentage cap without weakening typed or sensitive-content
checks. Answering alone does not authorize storage or approval.

```bash
./scripts/gapply profile import --source-kind user-statement \
  --source-file /absolute/private/chosen-answers.txt \
  --proposals-file /absolute/private/answer-proposals.json \
  --retain-all-facts --idempotency-key answers-1 --dry-run --json
```

After the retention choice and successful preview, repeat without `--dry-run`
to retain pending facts. Both inputs are UTF-8 text from regular files or stdin;
at most one may use `-`. Statement intake uses the default `--source-format text`
and does not interpret PDF/TeX or accept document-extraction options. The source
contains chosen exact statements, not a raw interview transcript. The manifest
binds its source hash and exact spans; the application assigns the statement
extractor, source identity and origin. No interview answers, transcript or
progress are stored by `profile interview` itself.

The registered extractor is `grounded-apply.profile-user-statement.manifest@1`.
Its source reference is `user-statement:sha256:<digest>` and its digest-only
artifact ID is `profile-user-statement-source:sha256:<digest>`. These identities
are application-owned and path-free; they establish the recorded origin, not
independent proof that the statement is true.

The default `--source-kind resume` retains its current identities and replay
behavior. These are complete supported version combinations, not independent
switches available to a proposal author:

| Import origin | Request identity | Source identity | Record digest | Stored source type |
|---|---:|---:|---:|---|
| Resume (default) | 4 | 1 | 1 | `imported_resume` |
| User statement (explicit) | 5 | 2 | 2 | `user_statement` |

The statement digest binds origin. Managed statement imports require the same
provenance validation on review, decisions, replay, resolution, retirement and
current/historical material use. Changing origin is a new request and cannot
reuse the prior idempotency key. Older resume claims and ordinary generic user
statements are not converted. Manifest, result-manifest, record-ID, database
schema and material versions remain unchanged by this origin extension.

### Shared import and review contracts

Value vocabulary 3 adds the scalar `research_description` while preserving
vocabularies 1 and 2 and their value
shapes. Explicit extractor version 3 uses it only under Research, Research
Experience and Research Projects headings; proposal order, exact spans and
qualifiers stay intact. The CLI default remains extractor 2.
Existing imports retain their recorded vocabulary and claim
types; repeat an earlier extraction with its displayed version. Manifest version
2 and the stored evidence contract are unchanged; changing an existing fact
still requires explicit import/review.

Codex requests extractor 4 for new intake while retaining the displayed version
for earlier selections.
Version 4 adds four exact neutral section boundaries: Research Interests,
Academic Research, Selected Research and Professional Memberships, matched with
casefolding and trailing-colon normalization. These headings clear the preceding
section's type; following nonlabel text stays unclassified until another supported
section. Headings and text remain in the inventory for explicit source-based
classification. The three existing research headings keep version 3's mapping.
Versions 1/2/3 retain their outputs, indexes and replay; no stored vocabulary,
content policy, manifest, evidence or material version changes.

The four-label set is not general heading detection. Document-reading issues
and unclassified career content are separate: `document.incomplete: false`
establishes neither semantic completeness nor a supported type for every line.
`--allow-partial` permits reported document gaps, not inferred classification.

Every allowed claim type has a closed versioned value shape. Vocabulary 1 retains
the original career types; vocabulary 2 adds explicit candidate name and contact
fields. Workflows bind the smallest vocabulary needed. Before any
storage transaction, the service snapshots all nested request state and
validates the whole batch under legacy content policy 2 or explicitly selected
complete-facts policy 3. Policy 3 permits full CV fact retention without source-
percentage/reconstruction heuristics; all field/size and restricted-content rules
remain. See [profile setup](PROFILE_SETUP.md). All restricted-text matchers
are derived from one immutable taxonomy version 1 covering work authorization
and immigration, security clearance, veteran and disability status,
criminal/legal attestations, conflicts of interest, demographic
self-identification, government identifiers, and authentication credentials.
The taxonomy uses deterministic high-confidence context, exact-assignment, and
label rules, plus fixed-point percent decoding and bounded fragmentation checks.
Its version and canonical SHA-256 are recorded in the request identity (resume
schema 4; the statement-origin extension uses schema 5).
These checks are defense in depth, not complete semantic, secret, or PII
classification. Manifest, request, value, content-policy, restricted-taxonomy,
locator, result-manifest, source-identity, record-ID, and record-digest versions
are bound into the workflow audit identity. Claim and evidence IDs are
deterministically bound to the creating workflow and proposal position. Each
immutable claim/evidence projection has a stable digest in result-manifest schema
3 and a durable schema-2 review association. An idempotent retry revalidates the
checkpoint, source artifact, ordered result records, association, links, and
stored content before returning them.

Raw source text is used in memory for digest, span, context, and policy-2 whole-source
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
remain supported. See [ADR 0002](adr/0002-local-storage-and-diagnostics.md).

`profile review` is physically read-only. Its output contains untrusted candidate
content for inspection, clearly marks every item unusable, and exposes the
creating workflow ID, proposal index, and a stale-safe review token; it cannot
record an approval or rejection. Before display it revalidates the exact current
import workflow/result identity, path-free source artifact, registered ingress,
unique one-to-one evidence link, immutable record digest, locator bounds,
selected-text checksum, and current content policy. Generic claim/evidence
mutation APIs reject `imported_resume`; only the import workflow can create that
provenance.

Paged review uses `profile review --limit N [--after CLAIM_ID] --json`, where
N is 1–50 and `--after` requires `--limit`. Without paging or a direct selector,
the complete queue, JSON shape, item order and human output remain unchanged. Codex uses five facts per conversational batch
unless the user prefers another size.

Legacy claim IDs can contain spaces, Unicode or control characters. Preserve
the exact ID. For IDs that cannot pass directly as a command argument, use
`--after-json JSON_STRING` instead of `--after`; it requires `--limit` and accepts
one JSON-encoded string. The two anchor options are mutually exclusive.
`data.page.next_after` remains the original ID, so encode that value once when
using the JSON option. Human-readable continuation commands quote it safely.

In a paged response, `data.pending_count` remains the total pending count and
`data.items` contains only the displayed page. The additional `data.page` fields
account for the remainder:

| Field | Meaning |
|---|---|
| `limit` | Requested maximum number of pending facts to display, from 1 to 50 |
| `returned_count` | Pending facts displayed in this page |
| `pending_before_count` | Pending facts at or before the supplied anchor, including the anchor if it is still pending |
| `pending_after_count` | Pending facts after the displayed page |
| `next_after` | Last displayed claim ID when later pending facts exist; otherwise null |

The three counts sum to the total pending count. A decided anchor remains a
valid continuation point; pagination follows the validated durable claim order,
not an offset into a shrinking pending queue. Unknown anchors fail. Omitting
`--after` restarts at the first pending fact, including earlier skips. A null
`next_after` does not mean review is complete when earlier facts
remain pending. Counts concern retained claims, not unclassified source lines
or extraction gaps.

The complete pending queue and any supplied anchor are validated before slicing,
with counts and selection from one read snapshot. Off-page corruption still
blocks display. Paging stores no cursor or session state and changes no fact,
decision or review-token rule. Skipping or stopping records no decision; retain
explicit per-item approval and rejection through `profile decide`.

`profile review --claim-id ID --json` returns only that pending item's existing
claim, evidence and review token in
`data.items`, with the global `data.pending_count` and `read_only: true`; it adds
no `page` or selector metadata. `--claim-id-json JSON_STRING` is the alternative
for exact legacy IDs that cannot cross the command line directly, including NUL.
Decode/encode the ID once and shell-quote safely; do not trim it or assume a UUID.
The two selectors are mutually exclusive with each other and all pagination
options (`--limit`, `--after`, `--after-json`). Input validation precedes runtime
resolution. Unknown or nonpending targets fail with a fixed private message.

The complete pending queue is audited in one read snapshot before lookup; a
direct request cannot hide corruption elsewhere. The returned item keeps all
existing review rules, including a null token for supported generic pending
claims. No approval, decision or cursor is created. Default full/paged report
shapes, ordering and tokens remain unchanged. See the
[operator example](PROFILE_SETUP.md#review-one-pending-fact).

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
intentionally unsupported. Current resume imports use manifest 2, request identity 4,
result manifest 3, record digest 1, legacy content policy 2 or explicit complete-facts
policy 3, restricted taxonomy 1, and
database schema 7. Migration 005 adds durable preparation queues; migration 006
adds verified saved-search records. Migration 007 adds verified daily schedules,
occurrences and notification acknowledgments. Existing homes
upgrade through `profile init`; version-4 through version-6 backups restore without implicit
migration and can then be upgraded explicitly. Migration 002 deliberately does not
invent review associations or record digests for earlier imports. Regenerate
manifest-v1 input; for an earlier manifest-v2 workflow, use a fresh opaque
idempotency key in disposable synthetic state. There is no automatic policy
migration or reclassification. Unsupported earlier-policy rows remain unusable and
fail review/decision closed rather than being promoted or repaired.

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
[`AGENTS.md`](../AGENTS.md).
