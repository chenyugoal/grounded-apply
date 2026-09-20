# Session handoff

This is the single live checkpoint. Scope and feature status live in
[ROADMAP.md](ROADMAP.md); accepted architecture decisions live in `docs/adr/`.

## Checkpoint

- **Updated:** September 20, 2026, 05:37 UTC / September 20, 00:37 CDT.
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `bde1758`. The previous daily
  workflow milestone was committed before this session; the starting tree was
  clean. This capacity increment has 24 tracked modifications and two untracked
  text files (ADR 0011 and the capacity script), all local and uncommitted. No
  push or PR; generated/private runtime artifacts remain outside the checkout.
- **Current scope:** user accepted raising supported capacity after the earlier
  four-hour window. Increase supported snapshots/automated storage from 16 MiB
  to 256 MiB, verify resource costs, and retain backup/restore/deletion and safe
  partial progress. Deduplication remains a separate schema-conversion proposal.
- **Current status:** implemented and locally verified. All 791 regression
  tests pass with zero skips, as do full-size capacity, fresh installed-pilot,
  required PDF and encryption gates. No known local failure remains.
- **Current schema:** 7, unchanged. Archive format 1 and exact registered schema
  4–7 restoration remain unchanged. No conversion is needed for the increase.
- No personal runtime was accessed; all generated data is fictional and outside
  the repository. No personal search/schedule was created or activated.
- The earlier development window ended September 20 at 01:57 UTC. Its temporary
  `grounded-apply-development-window` heartbeat remains PAUSED; this request did
  not restart it. No future development continuation was scheduled.

## Current increment

[ADR 0011](adr/0011-supported-profile-capacity.md) supersedes the original
lifecycle bounds: 256 MiB database snapshots/automated writes, 384 MiB encrypted
archives, thirty-second cooperative capture and validation budgets. Existing
256/512/768 KiB checkpoint reserves and the ninety-percent advisory remain.
Profile input, HTTP response, PDF and export limits are independent and unchanged.

Snapshot capture checks the deadline after serialization; validation measures
reference-schema construction and deserialization and checks completion. Native
operations are not forcibly preempted. Encryption uses the existing Argon2id and
Fernet recipe. Canonical Base64 checks use aligned 64 KiB chunks rather than a
second retained full decoded archive. Existing format/authentication rules hold.

Deletion hashes in 64 KiB chunks with no-follow/private/single-link and sampled
identity checks. The database bound is 256 MiB; config and restore receipts each
retain 16 MiB, with 288 MiB aggregate inventory. Preview confirmation, exact
replay, receipt audit and refusal of partial/changed targets remain intact.

The limit is supported backup/automated-workflow capacity, not a universal write
quota. Older manual job capture and standalone material build paths can exceed
it; doctor reports exceeded capacity. No automatic retention or history deletion
was added. Larger archives are unsupported by older executables retaining the
16/24 MiB limits. Same-UID TOCTOU, sidecar/WAL and private-output rules remain.

## Verification

Repository Python is 3.12.14 (`sh scripts/python`), build tools Python 3.13.1.
No dependency versions changed. Startup protocol found the previous checkpoint's
uncommitted-state description stale and verified clean HEAD `bde1758` instead.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_search_review_exports tests.test_review_files tests.test_search_cli -v
PASS — 40 tests, 52.924s; /private/tmp/gapply-capacity-session-start.log
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_backup tests.test_backup_schema_compatibility tests.test_migration_capacity tests.test_storage_capacity tests.test_schedule_capacity tests.test_deletion -v
PASS — 67 tests, 33.196s; /private/tmp/gapply-capacity-final-focused.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_storage_capacity.py --workspace /private/tmp/gapply-storage-capacity-full-20260920
PASS — 59.300s; /private/tmp/gapply-storage-capacity-full-20260920.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_backup.py
PASS — 38 tests, zero skips, 11.807s; /private/tmp/gapply-capacity-final-backup.log
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_materials.py
PASS — 15 tests, zero skips, 17.599s; /private/tmp/gapply-capacity-final-materials.log
./scripts/check
PASS — 791 tests, zero skips, 483.974s; /private/tmp/gapply-capacity-final-full.log
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
PASS — fresh offline wheel, all existing real-PDF pilot/search/daily/window/filter/rotation gates, and new >16 MiB preparation/restore smoke; /private/tmp/gapply-capacity-final-package.log
```

The first focused run exposed two tests whose oversized-material fixtures assumed
16 MiB. They were corrected to inject small allowances and retain actual partial
progress/rollback assertions. Production behavior was not changed to accommodate
those fixtures. Migration and reporting tests likewise retain small explicit
allowances. Larger-profile tests separately exercise the real production limit.
Independent review found no actionable defect in production or the capacity gate.
`/private/tmp/gapply-actionlint-1.7.12 -shellcheck= -pyflakes= .github/workflows/check.yml`
and `git diff --check` pass. Final file review covered 26 text
files and 86 valid local Markdown links, with no generated/private artifacts or
credential-pattern hits. Source/test files remained frozen during final gates.

### Capacity and memory evidence

The reproducible external capacity gate creates fictional jobs through validated
services and real PDFs through the actual daily CLI. At 18,976,768 database bytes
it prepares two PDFs across three daily dates, with quiet unchanged runs. It then
uses 252 further bounded job inputs to approach the new limit and prepares a
third PDF. Final database size is **267,415,552 bytes** (about 255 MiB); encrypted
archive size is **356,554,263 bytes**. Source bytes, approved profile, material
identities and schema remain unchanged across exact encrypted restore/replay.
There are zero live network requests and no material approvals or submissions.

At that size, measured wall times were 2.618s for the new daily draft, 2.411s for
backup, 2.985s for restore preview, 3.138s for confirmed restore and 3.058s for
exact replay. Peak RSS was 2,471,247,872 bytes for backup and 2,437,218,304 bytes
for restore: about 2.3 GiB. Operations ran sequentially in separate processes.
These are tested-host observations, not latency/memory guarantees. Encryption
remains in memory; streaming lifecycle operations are still future work.

Evidence: `/private/tmp/gapply-storage-capacity-full-20260920/capacity-summary.json`.
The fixture primarily measures byte capacity with large job text and three PDFs;
it does not establish thousands-of-material history performance or a guaranteed
retention duration. The installed gate now includes the >16 MiB smoke workflow;
its near-limit probe remains a separate command to control memory cost.

## Preserved daily milestone and known limits

Committed `bde1758` supplies configured Greenhouse/Ashby/Lever feeds, bounded
Netflix discovery/windows, durable batch/search/daily orchestration, source
rotation, literal preferences, blocker isolation, quiet notices and private
one-folder review export. See [DAILY_QUICKSTART.md](DAILY_QUICKSTART.md),
[JOB_DISCOVERY.md](JOB_DISCOVERY.md), [SEARCH_RUNS.md](SEARCH_RUNS.md) and
[DAILY_SEARCHES.md](DAILY_SEARCHES.md). The earlier final full gate passed 781
checks and fresh installed/PDF/encryption gates before this increment.

The major-tech catalog has Anthropic/OpenAI routes and partial Netflix; Google,
Apple, Amazon and Meta remain manual gaps. No most-company/all-opening coverage
promise. No semantic rewriting, browser fill, external messaging or automatic
submission was added. Personal daily setup remains a separate user workflow.

Earlier synthetic history reached 20 days/200 real PDFs at 16,023,552 bytes,
stopping before day 21 under the former cap. It is historical evidence, not the
new capacity boundary. Its same-history optimization reduced one ten-PDF tick
from 67.254s to 28.330s while retaining all 350 historical PDF checks. That does
not measure user active time. Earlier artifacts are under
`/private/tmp/gapply-synthetic-daily-capacity-zr0kddk2`.

[ADR 0010](adr/0010-content-addressed-material-storage.md) remains Proposed. Its
isolated duplicate-heavy experiment reconstructed 200 materials exactly and
reduced allocation by 70.63%; no production sharing, conversion or migration
exists. The proposal now references ADR 0011's independent capacity policy.

Hosted CI was not inspected; no claim of the full OS/interpreter matrix. The
previously corrected Ubuntu/Python 3.12 schema-race check still lacks new hosted
verification. External review copies remain independent snapshots outside
runtime backup/deletion. Keep private stdout separate from fixed diagnostics.

## Next exact task

Start with `sed -n '1,300p' docs/adr/0010-content-addressed-material-storage.md`.
The next material-storage slice is an isolated exact-byte payload helper in
`repositories/material_payloads.py` and in-memory corruption/rollback tests in
`tests/test_material_artifact_storage.py`. Review its conversion boundary before
acceptance; the helper alone must not be presented as available deduplication.
Keep production schema 7 until a separately gated new-home conversion can
preserve every historical material, approval, submission and workflow identity.
No implicit rewrite, personal migration or automation activation is authorized.
A separate streaming-backup proposal should be assessed if peak lifecycle memory
becomes a practical constraint. Do not implement bespoke encryption primitives.

## First command

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_backup tests.test_backup_schema_compatibility tests.test_migration_capacity tests.test_storage_capacity tests.test_schedule_capacity tests.test_deletion -v
```
