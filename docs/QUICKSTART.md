# Use the local application pilot

This is a local command-line workflow. Codex can operate it with you: import a
resume once, review the facts, then reuse selected approved facts for each job.
It does not discover openings, rewrite your career history, fetch a job page, or
submit an application. No model or network call occurs in these commands.

## Start

Python 3.12+ is required. The locally verified environment is macOS with Python
3.13.1, TeX Live 2026 (`pdflatex`, `lmodern`, `geometry`, `enumitem`), pypdf 6.10.0,
and cryptography 50.0.1. PDF input is not supported yet; export your resume to
UTF-8 plain text first. The output template supports text handled by pdfLaTeX;
unsupported glyphs, overflow, or extraction differences block generation.

From the repository, prepare an isolated dependency environment once:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-backup.txt -r requirements-materials.txt
source .venv/bin/activate
./scripts/gapply --help
```

The `.venv` on the current development machine is already prepared. Activate it
in each new shell, then use `./scripts/gapply`. For an installed wheel use the
`backup,materials` extras and the `gapply` entry point instead.

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

Extracting does not store the resume. Inspect the numbered proposals, then select
only the facts you want stored. Each selected fact retains its exact source span.
Selecting most of the raw source is intentionally refused by the minimization
guard; start with the relevant subset. Do not pad the source to bypass it.

```bash
./scripts/gapply profile extract --source-file /absolute/private/resume.txt
./scripts/gapply profile onboard --source-file /absolute/private/resume.txt \
  --source-sha256 HASH_FROM_EXTRACTION --select 0,1,2,3,4 \
  --idempotency-key onboarding-1 --dry-run --json
./scripts/gapply profile onboard --source-file /absolute/private/resume.txt \
  --source-sha256 HASH_FROM_EXTRACTION --select 0,1,2,3,4 \
  --idempotency-key onboarding-1 --json
./scripts/gapply profile review --json
```

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

## Prepare one job

Copy the job's visible text into a UTF-8 file. Use its HTTPS URL without tracking
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
Outputs are at most two pages. Long or unsupported content must be revised or
selected differently through approved facts; do not edit generated files and
assume the old approval covers them.

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
database, including PDFs and application history, up to 16 MiB. Original files,
exports, config, caches, and external backups are excluded. Restore previews a
new home; confirmation requires its archive hash. See the README for commands.

`export --redacted /absolute/private/support.json` writes only fixed-schema
version/count diagnostics. For whole-home deletion, use `delete --target-home
ABSOLUTE_HOME --receipt EXTERNAL_ABSOLUTE_FILE`, inspect the preview, and confirm
with its token. External source files, exports, and backups remain yours to
manage. No automatic retention or secure erasure is claimed.
