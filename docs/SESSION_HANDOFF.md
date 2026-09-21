# Session handoff

This is the single live checkpoint. Scope and feature status live in
[ROADMAP.md](ROADMAP.md); accepted architecture decisions live under `docs/adr/`.

## Checkpoint

- **Updated:** September 20, 2026, 15:16 UTC / September 20, 10:16 CDT.
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `4c16826`. All changes remain
  local and uncommitted; no push or PR. All fifty-seven preceding changed text
  files were preserved; no unrelated work was found.
- **Development window:** finished in its final interval at **15:16 UTC /
  10:16 CDT**, before the authorized 15:41 UTC deadline. The existing
  grounded-apply-development-window heartbeat is **PAUSED**. The app confirmed
  the update; its saved status was reread and prompt/name/recurrence/target were
  verified unchanged. No further automated implementation is pending. Resume
  development only on a new user request; do not repeat completed window checks.
  No personal runtime or job search was used.
- **Current scope:** strict search-run origin and creation-workflow custody.
  Production and registered schema remain 7; no migration 008, conversion command
  or production document sharing.
- **Current status:** implemented and verified; all 1226 tests pass in 820.712s.
  The 24 new tests, 61 existing search/scope regressions, source search/daily/pilot,
  encryption, real-PDF, near-capacity restore and fresh installed-package gates
  pass. Source and tests are frozen. Complete root and independent review found no remaining issues.
- Repository/build interpreters are Python 3.12.14. No dependencies changed;
  no new Python 3.13 or hosted-matrix result is claimed. All synthetic runtime
  data and gate outputs remain external to the checkout.

## Window outcome

- Added guarded historical snapshot capture, an owned read-only current-schema
  snapshot repository and explicit migration execution policy, with production
  and registered schema held at 7. Exact-byte payload helpers remain isolated
  preparation; conversion and production document sharing are unavailable.
- Added historical material creation/approval/application-use checks, strict
  approval/build/application/batch records and material/application/batch
  inventories. These retain legitimate later-retired and partial history while
  refusing unsupported facts, invalid required-answer approvals and orphan or
  misowned records. Ordinary approval/application reads share relevant checks.
- Closed saved-search configuration and run-origin record/workflow gaps, including
  ambiguous JSON, scalar aliases, identity substitutions, unexpected metadata,
  clock bindings and transaction cleanup. Valid replay, recovery and v1/v2 scopes
  remain compatible. Explicit signature requests retain human-answer boundaries.
- Verified all 1226 tests, fresh installed package and synthetic lifecycle/search/
  daily/batch/window/filter/rotation pilots, real PDFs, encrypted restoration and
  near-capacity ownership/readiness preservation. All data was synthetic and
  external; changes remain local and uncommitted. No push, publication or deploy.
- Full checkpoint/schedule custody, other aggregate inventories, active-lease
  admission and safe explicit new-home conversion remain unfinished. Large
  material-history scaling, active user-time improvement and the hosted/Python
  3.13 matrix are unmeasured or unverified. Existing memory and sampled same-UID
  TOCTOU limits remain documented.

## Current increment

The pure search_run_history helper validates the closed four-field run origin
before parent lookup, and then binds it to the checked parent and nineteen-field
search_run_create workflow. Recursive duplicate/nonfinite-safe JSON, literal
integer input version 1, parent/manifest/idempotency hashes, requested/returned
and deterministic run identity, one-run artifact ownership and completed/default
metadata must agree. Canonical UTC-microsecond run creation remains required;
workflow creation/start/finish retain its exact string. Update ordering compares
aware instants, preserving valid later/equivalent-offset times and harmless JSON.

SearchService._validated owns or borrows one snapshot around its existing read
path. Narrow origin/storage catches use fixed SearchIntegrityError. Parent _scope
remains outside those catches, preserving its saved-search failure and missing-
parent contract. Missing-run and invalid caller errors, current-material behavior,
caller work and unexpected errors/interrupts retain their boundaries. Review
identified the new outer transaction entry/exit SQLite path before gate freeze;
it now has a SQLite-only fixed-error catch, with integration coverage. Existing
history, transition, window, rotation, child-link and lease rules are unchanged.
No parent/run chronology rule or additional profile/PDF/network work was added.
The installed-package gate explicitly requires the new helper module.

Ten pure and fourteen service tests cover closed rows/workflows, exact identity/
digests, typed aliases/rehashed corruption, recursive JSON, completed metadata,
raw clocks, aware updates, saved corruption after exact snapshot validation,
public get/list/replay/resume refusal, original legacy/rotated checkpoint prefixes
through interruption/resume, file bytes/mtime, owned/borrowed transaction cleanup,
post-write rollback and fixed errors. Pure10 and integration14 passed on their
first runs; no product or fixture failure was waived. Existing61 regressions pass.

## Verification

```text
Baseline — the preceding frozen source passed all 1202 tests in 816.548s, including the 61 regression tests below; /private/tmp/gapply-search-scope-full-20260920.log
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_run_record -v
PASS — 10 tests, 0.007s; /private/tmp/gapply-search-run-record-20260920.log
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_run_history -v
PASS — 14 tests, 5.161s; /private/tmp/gapply-search-run-history-focused-20260920.log
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_scope_record tests.test_search_scope_history tests.test_searches tests.test_search_cli -v
PASS — 61 tests, 77.902s; /private/tmp/gapply-search-run-regression-20260920.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_backup.py
PASS — 38 tests, zero skips, 14.119s; /private/tmp/gapply-search-run-backup-20260920.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_materials.py
PASS — 15 tests, zero skips, 21.004s; /private/tmp/gapply-search-run-materials-20260920.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_pilot.py
PASS — complete real-PDF lifecycle and original/restored/later-retired inventories; /private/tmp/gapply-search-run-pilot-20260920.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_search.py --with-backup
PASS — sixteen real PDFs, replay/recovery/private review exports/exact encrypted restore; 13.641s kickoff; /private/tmp/gapply-search-run-search-20260920.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_schedule.py --with-backup
PASS — eleven real PDFs, bounded catch-up/quiet acknowledgment/exact encrypted restore; 14.877s kickoff; /private/tmp/gapply-search-run-schedule-20260920.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_storage_capacity.py --workspace /private/tmp/gapply-search-run-capacity-20260920
PASS — 88.324s, both capacities and exact encrypted restore/replay; /private/tmp/gapply-search-run-capacity-20260920.log
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
PASS — isolated wheel/CLI/schema checks and all synthetic installed pilots; /private/tmp/gapply-search-run-package-20260920.log
PYTHONDONTWRITEBYTECODE=1 ./scripts/check
PASS — 1226 tests, 820.712s; /private/tmp/gapply-search-run-full-20260920.log
/private/tmp/gapply-actionlint-1.7.12 -shellcheck= -pyflakes= .github/workflows/check.yml
PASS
git diff --check
PASS — complete source/test and final checkpoint review
```

All 51 preceding program and 22 runtime/script hashes matched their freezes
at the saved-scope checkpoint. Only searches.py and check_package.py changed
among those programs; search_run_history.py and two tests are new. All 23 current
runtime/script files and all 54 program/test/script files match these freezes:
/private/tmp/gapply-search-run-runtime-freeze-20260920.json and
/private/tmp/gapply-search-run-frozen-20260920.json. Independent hygiene review
confirmed sixty UTF-8 .py/.md files only, no unexpected artifacts and no unrelated
changes. All 54 program and 23 runtime/script hashes match after gates; 49 local
Markdown links resolve. Both schema bounds remain 7; migrations are unchanged.

Working tree: sixty source/documentation files, local and uncommitted. New in this
increment: services/search_run_history.py, tests/test_search_run_record.py and
tests/test_search_run_history.py. Modified: services/searches.py and one required
wheel-module entry in scripts/check_package.py, plus current docs. Service paths
are under src/grounded_apply. Prior material/application/batch/snapshot/migration-
policy and saved-scope work remains preserved. Use git status --short for the
complete inventory. No migration, dependency, public CLI syntax, personal runtime,
push or PR change.

## Preserved delivered behavior and limits

The saved-search configuration increment passed all 1202 tests in 816.548s,
24 new tests, 37 search regressions and source/installed search/daily/encryption/
PDF/capacity gates. Its closed five-field origin/nineteen-field workflow audit
checks strict recursive JSON, normalized typed manifests, identity/digest/artifact
bindings, completed metadata, canonical raw creation clocks and aware updates in
one owned or borrowed snapshot. Valid v1/v2 scopes, harmless encodings, later
updates, fixed failures and caller transaction ownership remain compatible.
Source and fresh installed pilots exercise replay and exact encrypted restoration;
all 51 program hashes matched after verification. Logs use
/private/tmp/gapply-search-scope-*-20260920.log. Review tightened alias fixtures by
rebinding hashes before their successful rerun; no test failure was waived.

The material-component inventory milestone preceding this change
passed all 1178 tests in 809.519s, 20 new tests, 73 material-history regressions,
source/installed pilots, encryption/PDF/batch and near-capacity gates. Four fixed
metadata-only projections account for every material, composite claim link,
approval and material build/approval workflow, including complete/incomplete
orphans and material saved before interrupted batch checkpointing. Exact ownership
and multiplicity are checked. Each material receives one PDF/profile walk; saved
approvals require historical eligibility, while unapproved required-partial,
approved optional-partial and later-retired output remain valid history. Source,
restored and later-retired pilots and both capacity checkpoints run the aggregate
audit without changing source bytes or readiness. Logs use
/private/tmp/gapply-material-inventory-*-20260920.log; source/test and independent
reviews passed with 47 program hashes unchanged. This supplies internal custody,
not conversion or document sharing.

The preceding linked material-build validator checks closed records, strict JSON,
raw/decoded values, typed versions, identities/digests, completed metadata and raw
creation/start/finish bindings with aware update ordering. Both transformations,
harmless encodings, original offset clocks and later updates remain valid. The
creation/approval/use factual paths share one loaded bundle/PDF and profile walk;
ordinary get and record-only contracts are unchanged. Its 27 new tests, 61
regressions, 1158-test full suite (786.753s), source/installed pilots and encryption/
PDF/capacity gates passed. A timestamp fixture was corrected to satisfy an existing
SQL check before exercising the new refusal; no product defect was concealed.
Logs use `/private/tmp/gapply-material-build-*-20260920.log`; the final integration
log is `/private/tmp/gapply-material-build-history-focused-final-20260920.log`.


The preceding batch inventory accounts for every batch, item, event, lease and
batch_create workflow against already audited ownership in one snapshot. Complete
and incomplete orphans, duplicate/missing/extra rows and equal-count wrong owners
fail; partial/interrupted/later-retired history remains valid. It adds no extra
per-batch PDF/fact walk and checks lease membership/shape, not active admission.
Its 18 new tests, 71 regressions, 1131-test full gate (774.540s), source/installed
original/restored inventories and encryption/PDF/capacity gates passed; logs use
`/private/tmp/gapply-batch-inventory-*-20260920.log`.


The preceding per-batch audit validates strict records/workflows, every historical
checkpoint and material creation/facts/required answers, preserving partial,
unapproved and later-retired history. Each distinct material has one PDF/facts
read per batch; optional approval-record custody shares the loaded bundle.
Its 26 new tests, final 71-test regression, 1113-test full suite (769.921s),
source/installed original/restored nine-PDF and encryption/PDF/capacity gates
passed after restoring generator-based decoding for ordinary batch reads.
Historical audits alone retain decoded checkpoint states. Initial fixture issues
were corrected; no failures were waived. Logs use
`/private/tmp/gapply-batch-history-*-20260920.log`; final full/installed/capacity/
real-PDF logs carry `-final-20260920`, and the final focused log is
`/private/tmp/gapply-batch-history-final-focused-20260920.log`.


The preceding application-component inventory audits each application once and
compares exact event/submission/workflow ownership against four metadata-only
projections, refusing duplicates and unvisited valid-looking orphan workflows.
Its 18 new tests, 1087-test full gate and source/fresh installed real-PDF
original/restored/later-retired inventories passed, with encryption/PDF/capacity
gates; logs use `/private/tmp/gapply-application-inventory-*-20260920.log`.


The preceding strict application-record increment closes JSON type aliases,
duplicate/nonfinite values, malformed workflow metadata/clocks and unlinked
replay ownership. Harmless encoding, raw hashes/timestamps, later replay and
caller key conflicts remain valid. Its 19 new tests, 1069-test full gate and
installed/encryption/PDF/capacity gates passed; logs use
`/private/tmp/gapply-application-record-*-20260920.log`.

The preceding material-use increment checks creation, approval and every ready/
applied event clock in one read snapshot. Every earlier ready event is audited;
all recorded packet members, retirement, context and required answers retain
creation authority. Later retirement stays valid history. Its 27 new tests,
1050-test full gate, source/installed real-PDF original/restored/later-retired
application audits and encryption/PDF/capacity gates passed; logs use
`/private/tmp/gapply-application-history-*-20260920.log`.

The preceding ordinary application chronology increment validates aware event
ordering and saved approval before ready/submitted events, preserving original
strings and hashes. Fourteen new chronology regressions, the 1023-test full gate,
installed/encryption/PDF/capacity gates passed; logs use
`/private/tmp/gapply-application-chronology-*-20260920.log`.

The preceding combined approval-eligibility audit rejects recorded required
NeedInfo after strict record and creation/approval factual validation, retaining
optional unanswered rows and absent-approval history. Fact-only and record-only
audits still permit intact partial records. Its 12 tests, 1009-test full gate,
installed/encryption/PDF/capacity gates passed; logs use
`/private/tmp/gapply-approval-eligibility-*-20260920.log`. One private helper reads
one bundle/PDF/profile snapshot without rerunning current answers or readiness.


The preceding approval-facts increment preserves original creation authority and
checks all packet members, retirement and context at saved approval, including
answer-only mappings. Half-open effective windows and aware times apply. Intervening
singular peers fail; unrelated, later-created and retired-before-approval peers do
not expand output. Its 18 new tests, 50 creation/history regressions, 997-test full
gate and installed/encryption/PDF/capacity gates passed; logs use
`/private/tmp/gapply-approval-facts-*-20260920.log`. An initial synthetic fixture
error was corrected before those passes; no production defect was concealed.


The preceding ordinary-read increment integrates strict approval-record validation
into is_approved in one read transaction. Missing approval stays false, valid
legacy/modern/optional-partial readiness remains usable, required partial stays
false, and stale-current MaterialBlocked keeps precedence. Corrupt present records
raise fatal MaterialApprovalIntegrityError. Its fifteen new service/export tests,
979-test full gate and installed/encryption/PDF/capacity gates passed; logs use
`/private/tmp/gapply-approval-read-*-20260920.log`.

The preceding approval-record increment supplies the separate explicit
validate_historical_approval_record audit and pure shared validator. Its 17 tests,
964-test full gate, installed/encryption/PDF/capacity gates passed. Present and
absent records are audited at both capacity checkpoints; exact approval rows and
restored approval-ID/readiness sets match. It establishes record consistency,
not actor authentication or factual eligibility at the historical approval time.
Logs use `/private/tmp/gapply-approval-record-*-20260920.log`.

The signature increment supplies seven integration/CLI tests and four historical
compatibility tests: standalone sign/e-sign requests stop before packet resolution,
compact career noun phrases remain usable, required unanswered signatures block
approval, old closed unanswered blockers remain history, and unsafe rehashed drafts
fail factual custody. Its 947-test full gate and installed/encryption/PDF/capacity
gates passed; logs use `/private/tmp/gapply-signature-*-20260920.log`.


The preceding increment supplies `MaterialService.validate_historical_facts`:
one read transaction checks PDF/bundle/workflow bindings, recorded packets,
original validated claim/evidence authority and timestamps, retirement audits,
unchanged historical peers, selected-unit order, requirement links and registered
presentation rules. Later retirement remains history; generic records changed
without reconstructible originals fail closed. No automatic selection, current
reuse or approval is granted. Full custody remains unfinished. Its 46 new tests
and preceding 936-test full, installed/encryption/PDF/capacity gates passed;
logs use `/private/tmp/gapply-history-*-20260920.log`.

The capacity fixture now includes one explicitly approved synthetic material
plus unapproved drafts. At 267,415,552 database bytes, 274 jobs and three real PDFs,
capture + record comparison + aggregate material/factual/approval-eligibility
auditing took 20.863s wall and 1,229,324,288 bytes peak RSS in the latest run. These are tested-host observations, not latency
or large-material-history guarantees. Exact optional approval rows, restored
approval IDs/readiness, source bytes/mtime/inventory, quiet replay, mutation
refusal and zero network use passed. Evidence:
`/private/tmp/gapply-search-run-capacity-20260920/capacity-summary.json`.

The prior preparation includes 30 payload-helper tests, 18 migration-policy
tests, 28 guarded-capture tests and 23 snapshot-repository/lifetime tests.
The helper verifies kind/digest/length/exact
bytes and strict UTF-8 with a 2 MiB per-kind bound; it owns no schema, file or
transaction and is unused by production storage. Historical text compatibility
and all factual/approval custody remain separate. Migration registration and
ordinary support are independently bounded, both 7; whole-range preflight rejects
conversion-only steps and exact-target races. Guarded historical capture uses
explicit runtime paths and an exact source-version subset of 4–7, bounded
read-only capture, exact snapshot validation, sampled identity checks and bounded
resources. `SQLiteRepository.from_snapshot` owns a validated current-schema,
read-only memory copy with bounded construction and safe cleanup; no extra image
or expiring query handler is retained. Normal backup/repository opens stay
current-only. The preceding 890-test/installed/capacity checkpoint logs use
`/private/tmp/gapply-snapshot-repo-*-20260920.log`; guarded capture's earlier
867-test checkpoint uses `/private/tmp/gapply-capture-*-20260920.log`.

Committed `bde1758` provides configured Greenhouse/Ashby/Lever feeds, bounded
Netflix windows, durable batch/search/daily orchestration, rotation, literal
preferences, blocker isolation, quiet notices and private one-folder review.
See [DAILY_QUICKSTART.md](DAILY_QUICKSTART.md), [JOB_DISCOVERY.md](JOB_DISCOVERY.md),
[SEARCH_RUNS.md](SEARCH_RUNS.md) and [DAILY_SEARCHES.md](DAILY_SEARCHES.md).
Committed `4c16826` supplies 256 MiB supported snapshots/automated storage and
384 MiB encrypted archives. [ADR 0011](adr/0011-supported-profile-capacity.md)
retains the original 267,415,552-byte database, 356,554,263-byte archive and
approximately 2.3 GiB peak lifecycle memory observations. Whole-buffer encryption,
manual paths exceeding supported capacity, independent external review copies,
and sampled same-UID TOCTOU remain limitations. Byte capacity does not establish
thousands-of-PDF performance or measured user-time savings. The full hosted
matrix remains unverified. Google, Apple, Amazon and Meta are manual discovery
gaps; no semantic rewriting, browser safe-fill, messaging or automatic submission.
[ADR 0010](adr/0010-content-addressed-material-storage.md) retains the prior
200-PDF/70.63% sharing feasibility experiment, not available conversion.

## Next exact task

1. This development window is complete: all gates passed and the automation is
   PAUSED, verified at 15:16 UTC. No work or confirmation is pending for this
   window. Preserve the local uncommitted work; do not restart its completed
   preparation, implementation or verification on a stale scheduled wake-up.
2. In the next development session after this window, start in
   src/grounded_apply/services/searches.py::_history and new
   services/search_checkpoint_history.py/tests/test_search_checkpoint_record.py.
   Implement only strict search-event row/raw-JSON validation before current
   _state/transition checks. Close the eight event fields, exact nonnegative
   integer position, recursive duplicate/nonfinite JSON, opaque IDs, digest
   scalars and existing canonical event clocks. Preserve streaming traversal,
   legacy states with or without source_order/windows, harmless formatting and
   existing hash/transition/request-budget/window/rotation semantics. No scheduler,
   lease-admission or execution policy changes.
3. The next gap is reproduced: a 0.326s zero-request external manual-source probe
   added a duplicate phase key to the first search_run_events.state_json without
   changing parsed values or hashes. After restoring the exact immutable trigger,
   from_snapshot, _validated, _history and ordinary get all accepted it. Reads
   left source bytes unchanged and made zero transport calls. Do not repeat this
   completed probe as a substitute for the next bounded implementation.
4. Full search/schedule/component inventory, active-lease admission, complete
   conversion custody and explicit safe new-home conversion remain unfinished.
   Keep production and registered schema 7; no available deduplication claim.

## First command

For a new explicitly requested development session, run the next baseline:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_run_record tests.test_search_run_history tests.test_searches tests.test_search_cli -v
```
