# Session handoff

This is the single live checkpoint. Scope and feature status live in
[ROADMAP.md](ROADMAP.md); accepted architecture decisions live in `docs/adr/`.
Preserve all existing tracked and untracked work.

## Checkpoint

- **Updated:** September 20, 2026, 01:57 UTC / September 19, 20:57 CDT.
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `dda08b2`. Origin matched at
  session start. Changes are local and uncommitted; no push, PR or hosted CI run.
- **Working tree:** 22 tracked modifications and 66 untracked text files across
  discovery, batches, searches, daily schedules, review exports, tests and docs.
  No generated/private runtime artifacts belong in the checkout.
- **Current schema:** 7. Exact registered schemas 4–7 remain restorable; restore
  does not migrate. `profile init` explicitly upgrades before current execution.
- **Latest complete full gate:** 781 tests/zero skips in 464.252s on the final
  compact-index production snapshot. Fresh installed acceptance, required PDF
  and encryption gates also pass. No production increment is unfinished and no
  known local failure remains.
- **Milestone:** daily discovery/preparation is implemented locally. Broader
  employer coverage, larger storage lifecycle and actual user-time evaluation
  remain unfinished. No personal search, schedule or candidate runtime was used.
- **Development window completed:** the user's four-hour window ended September
  20 at 01:57 UTC / September 19 at 20:57 CDT. The automation tool confirmed
  `grounded-apply-development-window` is **PAUSED** at the deadline. No development
  continuation or personal job-search wake-up remains active from this task.

## Implemented behavior

- **Public discovery:** configured Greenhouse, Ashby, Lever/global and Lever EU
  boards; bounded Netflix robots/sitemap/JobPosting reads. Immutable capture,
  replay and per-source failures/limits are explicit. The major-tech preset has
  Anthropic/OpenAI feed routes, partial Netflix and manual Google/Apple/Amazon/
  Meta gaps. It does not cover most companies or every opening. See
  [JOB_DISCOVERY.md](JOB_DISCOVERY.md).
- **Batch preparation:** one approved-evidence selection across 1–50 saved jobs,
  per-item overrides, isolated blockers, durable leases/checkpoints and bounded
  resume. Preparation makes drafts, never fact/material approvals or submissions.
  See [BATCH_PREPARATION.md](BATCH_PREPARATION.md).
- **Saved searches:** immutable source/evidence scope, bounded requests/bytes,
  source-atomic capture, frozen round-robin selection and a child batch. Applied,
  excluded and unchanged current drafts skip before the preparation limit.
  Network reads occur with runtime closed; rendering is outside write
  transactions. Parent leases fence child progress. See
  [SEARCH_RUNS.md](SEARCH_RUNS.md).
- **Preferences and fairness:** v2 literal title/location filters preserve v1
  scope/history bytes. Unknown locations stay explicit and default to included;
  no geographic or eligibility inference. Least-attempted exact posting versions
  precede repeated blockers. `source_rotation@1` rotates automatic boards using
  one durable reservation; retries retain order and started legacy runs remain
  unchanged. Manual gaps follow automatic sources.
- **Netflix windows:** `netflix_head1_tail6@1` refreshes one newest advertised
  posting and traverses up to six later entries per saved-search round. Cursor
  custody is versioned; isolated failed details advance, while restrictions or
  refused pre-GET budgets stop without advancing. Standalone discovery keeps its
  first bounded sample. Neither mode promises complete coverage.
- **Daily policy:** timezone/local-day occurrences, bounded catch-up and retry,
  pause/resume, parent fences and exact child recovery. Pending notification IDs
  are acknowledged only after reviewing their exact run, which may be older than
  the latest child. Unchanged rotation stays quiet; deferred sources preserve
  last observed notification health while current reports retain coverage gaps.
  There is no installed daemon or personal wake-up. See
  [DAILY_SEARCHES.md](DAILY_SEARCHES.md).
- **Current-fact performance:** one fresh validated profile snapshot per material
  plan. Search history still audits every historical PDF/bundle/binding/approval;
  current facts resolve only for exact incoming versions eligible for reuse.
  No authority cache persists across calls, rendering or commits. Corruption
  fails closed, including an invalid approval on a partial/stale bundle.
- **Capacity handling:** doctor reports guarded file usage/headroom with an
  advisory from 90% of the 16 MiB snapshot cap. This is not a backup-validity or
  next-operation-fit guarantee. A shared capacity exception preserves actual
  partial drafts and notifications at a mid-run reserve; it does not bypass
  lease, pause, expiry or clock checks. No retention or cap increase was added.
- **Review export:** `searches export --run-id ID --output-dir ABSOLUTE_DIR
  [--dry-run]` gathers one validated read snapshot into a private Markdown/JSON
  index, hash receipt and UUID job folders with existing material files. Current
  partial bundles retain blockers; stale files are omitted. Approval state is
  observed without granting approval. Exact old-run identity is retained; lease
  expiry alone does not change export bytes. Corrupt history fails closed.
  All output is bounded before writing: 50 packages/403 files/32 MiB total,
  8 MiB per index and 2 MiB per material file. All runtime roots and Git worktrees
  are excluded. Exact replay compares every file; extra, changed or incomplete
  contents are never overwritten or repaired. The compact index exposes direct
  PDF/answer/job links with escaped titles, locations and source IDs.
- **Operator workflow:** [DAILY_QUICKSTART.md](DAILY_QUICKSTART.md), the repository
  skill and [CODEX_WORKFLOW.md](CODEX_WORKFLOW.md) explain setup once, unattended
  bounded execution and one consolidated review. Browser filling and final
  external submission are not automated; final submission remains human.

## Verification

Repository Python is 3.12.14: use `sh scripts/python`, not system Python 3.9.
Build tools use Python 3.13.1. No dependency versions changed.

Final compact-index verification commands:

```text
./scripts/check
PASS — 781 tests, zero skips, 464.252s; /private/tmp/gapply-review-index-final-full-20260920.log
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
PASS — fresh offline wheel, full pilot, batch/search/daily/window/filter/rotation acceptance, both extras, encrypted restoration and review-folder replay
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_materials.py
PASS — 15 tests, zero skips, 16.760s
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_backup.py
PASS — 34 tests, zero skips, 10.277s
```

Final logs are `/private/tmp/gapply-review-index-final-{full,package,materials,backup}-20260920.log`.
Before the full gate, `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python
-W error -m unittest tests.test_review_files tests.test_search_review_exports
tests.test_search_cli -v` passed 40 tests/51.430s. After the final escaped source
cell addition, `tests.test_review_files` passed 16/1.030s. Independent review
found no remaining blocker and exercised adversarial Markdown/HTML/control text,
all package states, link targets, private paths, interruption and exact replay.
Initial export diagnostics missed the new command classification and the first
URL helper rejected Netflix query links; both were fixed before full verification.

Final tracked/untracked-file audit covered 88 text files, found no binary or
generated/private artifacts, and resolved all 95 local Markdown links. Four
credential-pattern matches were inspected synthetic invalid-URL/proxy fixtures,
not real secrets. Complete changed code was reviewed across the root and bounded
independent agents; source and test changes stayed frozen during the final gates.

Additional required checks passed:

```text
/private/tmp/gapply-actionlint-1.7.12 -shellcheck= -pyflakes= .github/workflows/check.yml
/private/tmp/grounded-apply-milestone-tools-20260918/bin/python /Users/chenyu/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/grounded-apply
git diff --check
```

The configured-search gate renders sixteen real PDFs across initial, later,
changed and resumed runs. It exports eight initial drafts plus two isolated
blockers, preserves source gaps and unknown questionnaire coverage, makes no
new GET/render on export, leaves runtime bytes unchanged and reconstructs the
same review tree after encrypted restore. Final compact Markdown was inspected
at `/private/tmp/gapply-overview-review-vw0f0oz6/review-final/review.md`; its
`final-verification.json` records ten four-column rows, sixteen local file links,
ten HTTPS job links and grouped blockers. The preview was queued in Codex.

Earlier checkpoint history (all had fresh installed acceptance):

| Increment | Full tests / elapsed |
|---|---|
| Discovery/batches | 490 / 235.447s |
| Saved search | 539 / 411.767s |
| Daily execution | 610 / 539.836s |
| Netflix windows/material-plan snapshot | 659 / 321.107s |
| Preparation filters/fairness | 693 / 355.950s |
| Source rotation | 715 / 378.077s |
| Historical validation/quiet notifications/headroom | 743 / 408.714s |
| Capacity reporting | 753 / 431.501s |
| Review export before compact table | 781 / 465.682s |

Earlier logs use `/private/tmp/gapply-{window,filter,rotation,history,capacity,export}-final-*-20260920.log`.
Batch nine, search sixteen, daily eleven, window nine, filter two and rotation
two PDF pages passed visual review in their respective synthetic gates. The
mandatory session-start discovery/jobs/briefing command passed 67/39.334s.

## Sustained synthetic workload and remaining storage work

A fictional seven-claim workload reached twenty completed daily runs/200 PDFs,
201 jobs and 16,023,552 allocated database bytes. Day 21 refused before a new
occurrence, GET or write because 753,664 bytes remaining were below the 768 KiB
schedule reserve. All material validation, unchanged profile, pending notices,
read-only review and encrypted restore passed. These are one fixture's results,
not a twenty-day capacity guarantee.

A preserved 170-material checkpoint supported a same-history timing comparison:
optimized day 18 took 28.330s versus 67.254s, still produced ten PDFs and retained
all 350 historical PDF checks. Historical current-plan/question calls fell from
350 to ten each. Earlier small-fixture plan/build improvements also passed, but
none of these timings measure the user's five-minute cycle or active-user time.

All 200 fixture PDFs have identical bytes, SHA-256
`37ee1b1e44ed874d231e18434cdc44d048cb5f9e8b131882cbedf3bd6a274b8e`.
The representative one-page PDF passed 150dpi visual inspection at
`/private/tmp/gapply-capacity-qa-20260920-4d0sawg7/page-1.png`; exact dates and
contributor wording remain intact. Source baseline, checkpoint and findings are
under `/private/tmp/gapply-synthetic-daily-capacity-zr0kddk2`; the optimized
continuation is `optimized-profiled-copy`, and `capacity-limit-restored` verifies
exact restored state. Original baseline/archive were preserved.

Actual CLI list/show/preview/export/replay at near-capacity produced ten PDFs and
83 files, with exact stored PDF bytes and an unchanged database. HTTP, DNS and
rendering were prohibited and had zero attempts. Evidence is
`/private/tmp/gapply-capacity-review-export-pwj7t9ir/result.json` and its review
folder. Two harness-only mistakes (an overly strict raw SQLite-header comparison
and wrong diagnostic event-name expectations) were corrected; authenticated
snapshot equality and the complete probes then passed. No product failure was
concealed by those corrections.

[ADR 0010](adr/0010-content-addressed-material-storage.md) is **Proposed**, not
accepted or implemented. An isolated in-memory experiment reconstructed all 200
materials exactly and retained all 34 other tables. Sharing three identical
payloads and compacting yielded 4,706,304 bytes, 70.63% below the source in this
duplicate-heavy fixture. The scratch schema was correctly refused by product
snapshot validation. There is no supported conversion command or capacity change.

## Coverage and known limits

September 19 public read-only observations: Anthropic 611 postings/34 title
matches; OpenAI 818/14, with a response around 13.6 MB near its 16 MiB response
cap. Netflix had 482 advertised entries, seven inspected and 475 unread in the
bounded check. Lever has synthetic verification only. These are dated observations,
not future coverage promises. September 20 Google/DeepMind and Amazon follow-ups
found no newly verified permitted enumeration route; official pages/policies
and the exact gaps are recorded in JOB_DISCOVERY.md. No postings were saved by
these live checks, and no undocumented API or login controls were bypassed.

Hosted CI was not inspected. Previously reported macOS15/Python3.12/3.13 and
Ubuntu24.04/Python3.13 results do not establish the whole matrix; the corrected
Ubuntu/Python3.12 schema-race check remains unverified remotely. No current local
failure is known. Semantic rewriting, PDF/DOCX ingestion, browser fill, messaging,
per-record deletion and automatic retention remain unfinished.

Real candidate data was never accessed in this development window. Preserve
read-only sidecar/WAL refusal, sampled same-UID TOCTOU limits, external-copy
ownership and the absence of secure-erasure or exactly-once delivery promises.
Exported copies do not synchronize later fact/approval changes and are outside
runtime backup/deletion. Keep private stdout separate from fixed diagnostics.

## Next exact task

Start with `sed -n '1,300p' docs/adr/0010-content-addressed-material-storage.md`.
Review its concrete boundary map before accepting the proposal. The smallest
proposed code slice is an isolated exact-byte payload primitive in
`repositories/material_payloads.py` with synthetic in-memory tests in
`tests/test_material_artifact_storage.py`; normal runtime behavior stays at
schema 7. Do not merely add migration 008 or bump the latest version: migration
loading requires a complete version range and writable initialization would
activate an in-place rewrite before safe new-home conversion exists.

Later work needs conversion-only registration, guarded legacy capture, an
isolated-copy adapter, complete historical custody audit, its own receipt and
publisher, and old/new encrypted-restore gates. The payload primitive alone is
not a storage release. Personal daily setup remains a separate user workflow
requiring scope, evidence and timing choices. Do not silently configure it,
publish, submit or continue development beyond the authorized window.

## First command

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_review_exports tests.test_review_files tests.test_search_cli -v
```
