# Use the local application pilot

For normal use, ask Codex to follow [CODEX_WORKFLOW.md](CODEX_WORKFLOW.md) or invoke
`$grounded-apply`. The commands below are the tool contract Codex operates; you
do not need to type them yourself.

This is a local command-line workflow. Codex can operate it with you: import a
resume once, review the facts, then reuse selected approved facts for each job.
It does not rewrite your career history, fetch arbitrary job pages, or
submit an application. Profile/material commands make no model or network calls;
the optional [public discovery](JOB_DISCOVERY.md) command reads supported ATS
feeds and a bounded Netflix sitemap route.

## Start

Python 3.12+ is required. The latest locally verified environment is macOS with Python
3.12.14, TeX Live 2026 (`pdflatex`, `lmodern`, `geometry`, `enumitem`, `needspace`), pypdf 6.10.0,
and cryptography 50.0.1. Resume intake accepts text, selectable-text PDFs, and
static LaTeX; see [profile setup](PROFILE_SETUP.md) for extraction limits.
The output template supports text handled by pdfLaTeX;
unsupported glyphs, overflow, or extraction differences block generation.

From the repository, prepare an isolated dependency environment once:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-backup.txt -r requirements-materials.txt
source .venv/bin/activate
./scripts/gapply --help
```

Use a Python 3.12+ executable for the first command; some systems' `python3`
still names an older interpreter. Never assume a previous task's `.venv` exists.
The repository launcher selects `GAPPLY_PYTHON` if explicitly set, then
`.venv/bin/python3` if present, then `python3` on PATH. Activation is optional
once `.venv` is prepared. For example:

```bash
GAPPLY_PYTHON=/absolute/path/to/python3.12 ./scripts/gapply --help
```

For an installed wheel use the
`backup,materials` extras and the `gapply` entry point instead.

Before creating a profile, Codex can check PDF dependency presence in the selected
interpreter and executable path:

```bash
./scripts/gapply doctor --materials --json
```

PDF intake needs `pypdf`; PDF material output needs both `pypdf` and `pdflatex`.
Read the separate `pdf_intake` and `pdf_materials` statuses: missing TeX alone
does not block PDF intake when `pypdf` is present. Each dependency is `present`,
`missing` or `unknown` if its probe failed. Exit 0 means both were found; exit 2
means a requested materials prerequisite is missing or unknown, not that the
base application is broken. The command installs nothing, executes no TeX,
reads no profile/document and creates no runtime. Presence does not verify
parsing, rendering, TeX packages, fonts or layout. Follow the setup above for
missing dependencies; use ordinary `doctor --json` separately for stored-profile
checks. See the [presence-check contract](REFERENCE.md#pdf-prerequisite-presence).

Choose a dedicated private directory **outside this repository** for your data.
The examples below are placeholders: replace `/absolute/private` with an existing
private (`0700`) parent, and create input files there with private permissions.
Do not put resumes, jobs, questions, exports, or runtime data in the checkout.

```bash
export GROUNDED_APPLY_HOME=/absolute/private/job-search
./scripts/gapply profile init --dry-run --json
./scripts/gapply profile init --json
```

`GROUNDED_APPLY_HOME` must remain the same across commands. Initialization creates
its private subdirectories. An existing root must already be private. Keep source
documents and exported copies outside this managed home.

## Approve your reusable facts

The extractor recognizes explicit `Name:`, `Email:`, `Phone:`, `Location:`, and
`Website:` labels plus headings such as `Experience`, `Projects`, `Education`,
and `Skills`. Keep employers, titles, dates, and bullets in their original order.
For example, these lines describe a fictional candidate:

```text
Name: Avery Quill
Email: avery.quill@example.com

Experience
Example Robotics LLC — Software Engineer
January 2022–March 2025
- Built a Python service for fictional warehouse robots.
```

Extracting does not store the resume. Inspect the full inventory, including
unclassified or blocked lines, then choose all supported facts or a subset.
Each selected fact retains its exact extracted-text span. Complete-facts retention
has no source-percentage cap; unknown content remains a visible review task.

New Codex-guided intake explicitly requests extractor 4. It keeps version 3's
research context and stops four neutral headings from inheriting the preceding
section's fact type. Those headings and following unclassified text remain
visible for review. See [profile setup](PROFILE_SETUP.md#keep-unsupported-sections-separate)
for the exact labels and limits. The CLI default remains 2; versions 1, 2 and 3
retain their proposals, indexes and replay behavior. Retention and approval stay
separate from extraction.

```bash
./scripts/gapply profile extract --source-file /absolute/private/resume.txt --extractor-version 4
./scripts/gapply profile onboard --source-file /absolute/private/resume.txt \
  --source-sha256 HASH_FROM_EXTRACTION --extractor-version 4 --select all \
  --idempotency-key onboarding-1 --dry-run --json
./scripts/gapply profile onboard --source-file /absolute/private/resume.txt \
  --source-sha256 HASH_FROM_EXTRACTION --extractor-version 4 --select all \
  --idempotency-key onboarding-1 --json
./scripts/gapply profile review --limit 5 --json
```

Use the original `.pdf` or `.tex` path directly and also pass the displayed
`--document-sha256` for those formats. Review extraction warnings first; partial
documents require an explicit `--allow-partial` choice. For numeric selections,
pass the displayed extractor version and `--retain-all-facts` when retaining a
large fraction of the CV. Keep the displayed version when resuming an earlier
selection; versions 1, 2 and 3 preserve their original classifications.
A document with no reading gaps can still contain unclassified lines;
`--allow-partial` does not classify them or establish semantic completeness.
If a publication title and status are split, the [explicit grouping flow](PROFILE_SETUP.md#keep-a-wrapped-publication-together)
keeps the selected fragments together without dropping other proposals.
Repeat `--indexes` to select each work separately using the original indexes;
the helper returns one complete manifest.
It returns a private import manifest;
it does not retain or approve facts or require a manually exported text CV.
An optional guided setup starts with `./scripts/gapply profile interview --json`;
it stores no answers. See [profile setup](PROFILE_SETUP.md) for deeper rounds.

Codex starts with five retained facts at a time; adjust the batch size when
useful. Continue to the next batch with:

```bash
./scripts/gapply profile review --limit 5 --after CLAIM_ID_FROM_NEXT_AFTER --json
```

Codex handles the cursor from `data.page.next_after`; you need not manage IDs.
The response shows the total pending plus displayed, earlier and later pending
counts. Approving or rejecting the last displayed claim does not invalidate
continuation. Skipped facts stay pending. A null next cursor means the end of
this pass; if earlier facts remain pending, omit `--after` to revisit them.
Stop whenever you want; a later session can continue from the prior claim or
restart the remaining queue. Paging stores no review position or decisions.
Limits are 1–50, and `--after` requires `--limit`. Without either option,
`profile review --json` still returns the full pending queue. See
[profile setup](PROFILE_SETUP.md#review-in-manageable-batches) for the full flow.

For each fact you have checked against its evidence, use its displayed ID and
review token. `--confirm` records your approval. Use `reject` for incorrect facts.
An unconfirmed decision checks argument syntax only; the review response is the
content preview. A key identifies one operation; keep it unchanged for retries.

```bash
./scripts/gapply profile decide --claim-id CLAIM_ID --review-token REVIEW_TOKEN \
  --decision approve --actor-id local-user --idempotency-key review-1 --confirm --json
./scripts/gapply profile show
```

To correct an approved fact, import and approve the replacement, then use
`profile retire --claim-id OLD_ID --replacement-claim-id NEW_ID --actor-id
local-user --idempotency-key correction-1`. Review the preview and repeat with
its `--preview-token TOKEN --confirm`. Omit the replacement for withdrawal.
Original history remains, and retired facts cannot authorize new materials.

## Build a profile through questions

Start with `./scripts/gapply profile interview --json`. Codex asks a few
questions, shows your exact answers as proposals, and asks which answers you
want retained. You may skip or stop. Codex prepares private UTF-8 text containing
only the chosen statements and the schema-2 manifest with their exact spans.
It does not save a transcript or infer missing facts.

```bash
./scripts/gapply profile import --source-kind user-statement \
  --source-file /absolute/private/chosen-answers.txt \
  --proposals-file /absolute/private/answer-proposals.json \
  --retain-all-facts --idempotency-key answers-1 --dry-run --json
./scripts/gapply profile import --source-kind user-statement \
  --source-file /absolute/private/chosen-answers.txt \
  --proposals-file /absolute/private/answer-proposals.json \
  --retain-all-facts --idempotency-key answers-1 --json
./scripts/gapply profile review --limit 5 --json
```

These facts retain user-statement origin and stay pending until you approve
them individually with the decision flow above. The command accepts text files
or stdin, not PDF/TeX interpretation; at most one input may use `-`. Omitted
`--source-kind` still means resume import. Existing facts are not reclassified.
You can resume from retained facts, but the interview saves no question position
or unretained answers. See [profile setup](PROFILE_SETUP.md#start-or-deepen-the-profile-with-questions).

## Prepare one job

To discover and prepare together under one agreed source/evidence scope, ask
Codex to use [saved search runs](SEARCH_RUNS.md). It configures the scope, runs
the bounded discovery-to-draft workflow, and resumes any saved progress. You do
not need to supply each discovered job or repeat the shared claim selection.
For a recurring run, give Codex the daily time and timezone and follow
[daily searches](DAILY_SEARCHES.md). The saved schedule keeps limits and recovery
state; an authorized wake-up mechanism must invoke it on the local host.

For several saved jobs, ask Codex to use [batch preparation](BATCH_PREPARATION.md).
It can reuse one agreed evidence selection, prepare independent drafts, and
return a consolidated review queue. The individual commands below remain useful
for inspecting or refining one item.

To resume an existing search, run `./scripts/gapply brief --json` first. It shows
saved jobs, recorded stages, material readiness, missing-evidence counts, and
next actions without storing anything. Use `--job-id JOB_ID` for one job or
`--follow-up-days 14` for a different response-check interval. These suggestions
are on demand; they do not schedule reminders or send messages.

For supported public boards, Codex can use `jobs discover` to capture jobs first;
see [source setup and coverage](JOB_DISCOVERY.md). For a manual capture, copy the
job's visible text into a UTF-8 file. Use its HTTPS URL without tracking
parameters, credentials, or fragments. Saving it records your supplied text and
capture time; it does not verify that the opening is still live.

```bash
./scripts/gapply jobs add --url https://example.com/jobs/engineer \
  --source-file /absolute/private/job.txt --idempotency-key job-1 --json
./scripts/gapply jobs show --job-id JOB_ID
./scripts/gapply jobs assess --job-id JOB_ID
```

The matrix quotes requirements and retrieves approved facts sharing terms. It
shows unanswered gaps and uncertainty. A shared keyword does not prove fit or
years of experience. Choose which facts to emphasize after reading the matrix.

Build with the selected claim IDs in the desired order **within each section**.
Keep an employer/title, its dates, and its bullets together. Section order is
fixed. The unique approved name and email are included automatically; select
other contact claims explicitly. The program preserves text and source bullet
markers; it does not infer employer/project associations or reorder chronology.

```bash
./scripts/gapply materials build --job-id JOB_ID --claim-ids ID1,ID2,ID3 \
  --idempotency-key resume-job-1 --dry-run --json
./scripts/gapply materials build --job-id JOB_ID --claim-ids ID1,ID2,ID3 \
  --idempotency-key resume-job-1 --json
./scripts/gapply materials export --material-id MATERIAL_ID \
  --output-dir /absolute/private/job-1-materials --json
```

Open `resume.pdf` and review the text, dates, ownership, layout, and job relevance.
Use `materials list --job-id JOB_ID` to find saved drafts and approved versions.
The directory also contains LaTeX, extracted text, claim mappings, validation,
answers, and a file-hash receipt. It must be a new destination; exact unchanged
export retries are allowed, but changed or partial exports are never overwritten.
New builds use 11-point sans-serif text, section dividers, aligned role/education
headings, and readable bullets. Plain-text bullet markers and approved evidence
starting with `\resumeItem{` are recognized; `\resumeSubheading` marks supported
role/education headings. Source TeX is never executed. Other text remains a
paragraph unless you supply presentation choices:

```json
{"schema_version": 1, "presentations": {"ID1": "heading", "ID2": "bullet", "ID3": "paragraph"}}
```

Pass that private file with `--layout-file FILE` on both preview and build, or use
`--layout-file -` for stdin (only one input can use stdin). Keys must be selected
claim IDs. Values cannot contain prose, markup, fonts, or factual edits; contact
styles cannot be overridden. Headings are supported for employment descriptions,
employment titles, education, degrees, portfolio items, and version-3 research
facts. A heading containing exactly four nonempty ` | `-separated fields lays
them out as two rows, in original reading order. Other headings stay intact on
one or more lines. Keep related
headers and bullets adjacent; associations are not inferred.

Selecting approved research facts for the resume adds a separate Research
section, keeping Publications separate. Having research facts elsewhere in the
profile, or using them only in
answers, does not change the resume's presentation version. Existing saved
materials are not rebuilt or relabeled automatically.

Outputs are at most two pages. Long prose wraps; unrenderable content fails
without truncation or automatic font shrinking. Inspect every page before
delivery. Correct presentation first; if the content still needs editing, use
the approved-fact review path. Do not silently discard relevant facts to bypass
overflow or edit exported files behind an approval. A different selection or
layout needs a new idempotency key and fresh material review. Existing version-1
and version-2 materials and retries retain their original layout and approval.
An omitted layout is equivalent to an empty presentations object.

After reviewing the exact bundle, approve its displayed hash:

```bash
./scripts/gapply materials approve --material-id MATERIAL_ID \
  --bundle-sha256 BUNDLE_HASH --actor-id local-user \
  --idempotency-key approve-job-1 --confirm --json
```

## Optional questionnaire answers

Create a private JSON file selecting evidence for each career question:

```json
{"questions":[{"id":"experience","text":"Describe your experience building Python services.","claim_ids":["CLAIM_ID"],"required":true}]}
```

`./scripts/gapply answers --job-id JOB_ID --questions-file FILE` produces exact
approved text for review without storing answers. To retain these answers with
the material version, supply `--questions-file FILE` to `materials build` and
review `answers.json` before approval. Use a new build key for changed questions.
Required unanswered questions block approval. Sensitive, legal, eligibility,
identity, salary, and signature questions require you to answer directly at the
application site. They are never inferred or stored as reusable answers here.

## Keep the application record

```bash
./scripts/gapply applications add --job-id JOB_ID --actor-id local-user \
  --idempotency-key application-job-1 --json
./scripts/gapply applications transition --application-id APPLICATION_ID \
  --to shortlisted --actor-id local-user --idempotency-key shortlist-job-1 --json
```

Inspect the transition preview, then repeat the same command with
`--preview-token TOKEN --confirm`. Move through `shortlisted`, `preparing`, and
`ready_for_review`, using a new operation key for each state. The last transition
also requires `--material-id MATERIAL_ID` and a current approved material.

Submit the application yourself at the employer's site. Only afterward, preview
and confirm the `applied` transition using the reviewed material:

```bash
./scripts/gapply applications transition --application-id APPLICATION_ID \
  --to applied --material-id MATERIAL_ID --confirm-submitted \
  --actor-id local-user --idempotency-key applied-job-1 --json
```

Repeat with its preview token and `--confirm` to record it. This command never
submits anything. The immutable snapshot retains the exact job, material, answer
bundle, and approval recorded by Grounded Apply; answers or uploads you change
at the external site are outside that record. Use `applications list` or
`applications show --application-id ID` for history and `transition --help` for
later states. Withdrawing a fact blocks future use, while past submission history
remains available.

## Protect and remove your data

Use `backup --encrypt /absolute/private/backup.gapply` regularly; it prompts for a
passphrase. Keep the passphrase separately. Backup covers the complete pilot
database, including PDFs and application history, up to 256 MiB. Original files,
exports, config, caches, and external backups are excluded. Restore previews a
new home; confirmation requires its archive hash. See the
[backup and restore examples](REFERENCE.md#encrypted-profile-backup-and-restore).

`export --redacted /absolute/private/support.json` writes only fixed-schema
version/count diagnostics. For whole-home deletion, use `delete --target-home
ABSOLUTE_HOME --receipt EXTERNAL_ABSOLUTE_FILE`, inspect the preview, and confirm
with its token. External source files, exports, and backups remain yours to
manage. No automatic retention or secure erasure is claimed.
