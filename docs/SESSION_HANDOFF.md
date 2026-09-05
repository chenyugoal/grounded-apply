# Session handoff

This is the single live checkpoint. Durable scope is in `ROADMAP.md`; decisions
are in `docs/adr/`.

## Checkpoint

- **Updated:** 2026-09-04 21:46 CDT
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `9cab31f` (one commit ahead of
  locally recorded origin; no fetch, commit, push, or hosted run this session).
- **User objective:** Keep developing until usable for a personal job search.
  ADR 0006 bounds the immediate local application pilot. Continue autonomously
  through the complete synthetic workflow and release verification; foundation
  progress alone does not complete the request.
- **Starting tree:** Only this checkpoint was modified by the preceding readiness
  assessment. Baseline `./scripts/check`: 309 discovered, 296 passed, 13 optional
  crypto skips, 30.736s.
- **Implemented and verified in the working tree:** The complete ADR 0006 local
  application pilot, in addition to deletion, retirement/replacement, selected
  UTF-8 onboarding, and vocabulary-2 name/contact claims with vocabulary-1 replay.
  Jobs preserve user-supplied text/URL and exact requirements; matching retrieves
  approved evidence/gaps without a hiring score. Materials retain exact approved
  text, source bullets and explicit selected order, LaTeX/PDF/text, mappings,
  validation and reviewed digest. Career answers are copied from approved packets;
  sensitive/unknown questions require human input. Applications have validated
  append-only events and immutable human-confirmed submission snapshots. Support
  export includes fixed counts/versions only. All pilot bytes live in schema-4
  SQLite and survive encrypted backup/restore; explicit external copies remain
  outside managed backup/deletion.
- **Final verification complete:** Read-only `materials list [--job-id]` finds
  saved drafts and marks retired-evidence versions needs_review. All 345 tests
  pass with both optional providers and no skips. Required PDF/encryption gates,
  final fresh installed-package complete pilot, and visual QA pass. The local
  pilot is usable on the verified environment; the user's development objective
  is met. The quickstart and prepared `.venv` are ready. No personal profile was
  initialized and no external application was submitted.
- **Working tree:** Existing modified files are README, development guide, this
  checkpoint, package checker, CLI/diagnostics, SQLite/schema, profile/import
  validation, and affected schema/import tests. New files include ADRs 0004–0006,
  migrations 003/004, deletion service/adapter/tests, profile lifecycle/tests,
  resume extraction/tests, pilot services/tests/renderer/export adapter,
  requirements-materials.txt, check_materials.py, check_pilot.py and QUICKSTART.md.
  Preserve all work; inspect `git status --short`. `.venv` is ignored and contains
  only optional dependencies, no runtime/candidate data.

## Verification

All fixtures/runtime data are synthetic; runtime/build/log files are under
private temporary directories outside the repository.

Final release evidence on the completed working tree:

```text
PATH="$PWD/.venv/bin:$PATH" ./scripts/check
PASS — 345 tests, 100.143s, zero skips (warnings treated as errors)
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_materials.py
PASS — 9 tests, 28.397s, zero skips, real pdflatex/pypdf
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_backup.py
PASS — 28 tests, 7.879s, zero skips, real cryptography
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-backup-wheels-20260904
PASS — source archive/wheel, isolated offline base installation, both optional
extras, complete CLI pilot including materials list, backup/restore, history,
retirement, support export, safe deletion, replay, and diagnostic separation
git diff --check
PASS — complete diff and working-tree inventory inspected; no candidate data,
runtime databases, generated PDFs, logs, credentials, or unrelated edits staged
```

Logs are `/private/tmp/gapply-complete-check.log`,
`/private/tmp/gapply-complete-materials.log`,
`/private/tmp/gapply-complete-backup.log`, and
`/private/tmp/gapply-complete-install.log`; no logs were added to the checkout.
Dependency-free discovery also passed: 345 tests, 329 passed/16 optional skips,
86.787s. That separate base run is not optional-provider release evidence.
No known test failures remain. Changes are uncommitted and include untracked
new implementation/test/documentation files; `git status --short` is authoritative.

Earlier checkpoints and issues resolved during this session:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error -m unittest tests.test_deletion tests.test_logging -v
PASS — 18 tests
./scripts/check (deletion milestone)
PASS — 319 discovered, 306 passed, 13 optional crypto skips, 30.414s

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error -m unittest tests.test_profile_lifecycle tests.test_schema tests.test_profile_service tests.test_deletion tests.test_logging -v
PASS — 58 tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error -m unittest tests.test_resume_extraction tests.test_profile_import tests.test_profile_lifecycle -v
PASS — 106 tests, 21.601s

./scripts/check (schema 3/onboarding milestone)
PASS — 327 discovered, 314 passed, 13 optional crypto skips, 31.881s
Initial run had one stale literal schema-version assertion in test_storage_safety;
it was changed to LATEST_SCHEMA_VERSION before the passing run.

PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-release-tools-20260904/bin/python -W error scripts/check_backup.py
PASS — 28/28 with real provider, no skips, 8.381s (schema 3)
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py --backup-wheelhouse /private/tmp/grounded-apply-backup-wheels-20260904
PASS — source/wheel, isolated installed CLI/import/approval/replay, offline backup
extra, encryption/restore/provenance round trip (schema 3)
```

Later schema-4 evidence (before the final materials-list convenience change):

```text
./scripts/check
PASS — 342 discovered, 326 passed, 16 optional crypto/PDF skips, 72.273s
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-release-tools-20260904/bin/python -W error scripts/check_backup.py
PASS — 28 real-provider tests, no skips, 8.275s
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_materials.py
PASS — 7 real/fake adapter tests, no skips, 22.599s
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error -m unittest tests.test_materials tests.test_applications -v
PASS — 13 tests, no skips, 58.935s (adds conflicting name, changed export, retired readiness cases)
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_pilot.py --demo-output /private/tmp/grounded-apply-pilot-final-demo-20260905
PASS — full CLI, real PDF, answers, manual application, diagnostics, encrypted
backup/restore, retirement, immutable history, deletion and exact retries
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-backup-wheels-20260904
PASS — fresh offline installed base + backup/materials extras, complete pilot
```

PDF visual QA: rendered all pages (one letter page) with bundled pdftoppm and
inspected `/private/tmp/grounded-apply-pilot-final-demo-20260905/preview.png`.
Clear name/contact, correct employer/date/bullet order, source bullet preservation,
no clipping/overflow or invented text. The deliberately short fictional fixture
uses only the upper portion of a page. Initial PDF extraction split Avery as
“A very”; explicit glyph spacing plus plain extraction fixed it. Validation now
rejects unexpected text as well as missing units. Initial CLI harness compared a
dynamic review_required field after approval; it now checks stable IDs/audit plus
the correct current review state. No failures were bypassed.

Local interpreter is Python 3.13.1 on macOS. `.venv` contains cryptography 50.0.1,
cffi 2.1.1, pycparser 3.0 and pypdf 6.10.0, installed from disposable offline
wheels. `pdflatex` is TeX Live 2026 at
`/usr/local/texlive/2026/bin/universal-darwin/pdflatex`. Reportlab is not a runtime
dependency. No hosted CI or alternate-platform evidence was obtained.

## Current limitations / decisions

- Real data remains outside the repository; no real candidate data was loaded.
- Deletion only recognizes the current portable layout with empty managed file
  directories. Generated pilot bytes live in SQLite, retaining
  backup/deletion coverage. External exports/backups/sources remain caller-owned.
- Retirements are an audited effective-use projection. Original imported claim
  rows stay unchanged so import/approval retries keep their original identities.
  Use `validated_profile`/`resolve` for current authority, not raw repository rows.
- Extraction is conservative UTF-8 text only. Name/contact require explicit
  labels. Users select proposal indexes against a source hash, then approve each
  fact separately. Existing minimization and sensitive-content guards still apply.
- No model/network/browser/submission action has been introduced. Semantic
  assistance, richer formats, hosted support, and background discovery are future
  work; the bounded pilot must describe these limitations honestly.
- Filesystem guards remain sampled checks, not same-UID authentication or secure
  erasure. Incomplete deletion receipts are never silently resumed.

## Next exact tasks

1. `docs/QUICKSTART.md`: begin the user's first assisted pilot when they provide
   a private UTF-8 resume path and job text. Establish the explicit private storage
   target and selected facts before persisting personal data; show evidence before
   recording approval. Do not reuse synthetic fixtures as candidate facts.
2. The next bounded development candidate after pilot feedback is richer resume
   onboarding. Begin with `src/grounded_apply/services/resume_extraction.py` and
   `tests/test_resume_extraction.py`; agree the exact input format before adding
   a parser dependency. Discovery/browser/semantic rewriting are separate work.

## First command

```bash
PATH="$PWD/.venv/bin:$PATH" ./scripts/check
```
