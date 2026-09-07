# Session handoff

This is the single live checkpoint. Durable scope is in `ROADMAP.md`; accepted
architecture decisions are in `docs/adr/`.

## Checkpoint

- **Updated:** 2026-09-04 22:18 CDT
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `3350c55` (local origin matches).
  The user committed/pushed the preceding workflow fix. This session has not
  committed, pushed, or triggered a GitHub rerun.
- **Current task:** Fix the concurrent SQLite initialization failure reported
  from Ubuntu 24.04/Python 3.12 after the workflow syntax correction.
- **Hosted evidence:** The user supplied three successful matrix jobs:
  macOS 15/Python 3.12, macOS 15/Python 3.13, Ubuntu 24.04/Python 3.13.
  Ubuntu 24.04/Python 3.12.14 ran 345 tests with 16 optional-provider skips and
  one error in `test_concurrent_initialization_is_idempotent`: schema version
  zero combined with populated user tables. This is an application race, not
  another workflow-expression failure. No new run URL was supplied; these job
  results came from the user's log, not authenticated remote inspection.
- **Root cause:** `_validate_schema_state` previously read `user_version`, table
  existence, and migration history in separate autocommit snapshots outside
  the per-migration write lock. A concurrent commit could mix revisions,
  producing a false unversioned-schema or version/ledger mismatch error.
- **Local correction — implemented and verified:** Validation owns a read
  transaction when none exists, releases it on success or failure before any
  migration write lock, and preserves caller-owned transactions. Existing
  per-migration `BEGIN IMMEDIATE` revalidation and atomic commits remain.
  Schema stays at version 4; no migration, retry workaround, or relaxed
  future/unversioned/checksum/WAL/privacy guard was added.
- **Regression evidence:** A deterministic two-connection test commits the
  other initializer immediately after the version read, for fresh and version-1
  databases through both initialization and query-only validation. All four
  cases fail on the original code and pass with the correction. The fixture
  uses WAL in private schema helpers to permit a commit during the read; public
  read-only adapters still refuse WAL. Transaction-ownership tests also cover
  successful validation and checksum failure. Future/unversioned rejection
  tests now verify that validation leaves no transaction open.
- **Working tree:** Six modified files: `src/grounded_apply/repositories/_schema.py`,
  `tests/test_schema.py`, `README.md`, `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`,
  and this checkpoint. Starting tree was clean. The fix is local/uncommitted.
  Hosted verification of this correction is still pending.

## Verification

Session-resume first command on 3350c55:

```text
/private/tmp/gapply-actionlint-w2c997go/actionlint -shellcheck= -pyflakes= .github/workflows/check.yml
PASS — exit 0, no findings; workflow unchanged in this session
```

Narrow reproduction and validation:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error -m unittest tests.test_schema.SchemaMigrationTests.test_schema_validation_uses_one_snapshot_during_concurrent_migration -v
BEFORE production fix: FAIL — all four subcases reproduce mixed-snapshot errors
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error -m unittest tests.test_schema -v
AFTER: PASS — 8 tests, zero skips, 0.235s (Python 3.13.1 / SQLite 3.47.2)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /Users/chenyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -W error -m unittest tests.test_schema -v
AFTER: PASS — 8 tests, zero skips, 0.166s (Python 3.12.14 / SQLite 3.53.1)
```

During test authoring, the transaction-ownership fixture initially used an invalid
checksum length and hit the SQL CHECK before validation. It now uses a
well-shaped wrong digest; the final test reaches and verifies checksum rejection.

Repeated original eight-worker test, Python 3.13.1:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -W error - <<'PYTEST'
import unittest
from tests.test_schema import SchemaMigrationTests
suite = unittest.TestSuite(SchemaMigrationTests('test_concurrent_initialization_is_idempotent') for _ in range(100))
result = unittest.TextTestRunner(verbosity=0).run(suite)
raise SystemExit(not result.wasSuccessful())
PYTEST
```

PASS — 100 repetitions, 8.914s. The same 100-case suite ran with
`/Users/chenyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`
(Python 3.12.14), with `sys.version` and `sqlite3.sqlite_version` printed first:
PASS — 100 repetitions, 9.111s. These are local macOS results, not Ubuntu runs.

Repository and installed-workflow gates after the fix:

```text
PATH="$PWD/.venv/bin:$PATH" ./scripts/check
PASS — 347 tests, zero skips, 105.857s
PATH="/private/tmp/gapply-schema-race-py312-rawg5z9o/bin:$PATH" ./scripts/check
PASS — 347 tests, 16 expected optional-provider skips, 89.805s
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_materials.py
PASS — 9 tests, zero skips, 30.997s
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_backup.py
PASS — 28 tests, zero skips, 7.972s
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-backup-wheels-20260904
PASS — source archive/wheel, fresh offline base + backup/materials installation,
complete synthetic CLI pilot, real PDF, encrypted backup/restore, immutable
history, retirement, support export, deletion, replay and diagnostic separation
git diff --check
PASS — complete source/test/documentation diff inspected; no secrets, personal
data, generated artifacts, debug output or unrelated changes introduced
```

The Python 3.12 gate used a newly created dependency-free disposable virtualenv.
The 16 skips do not verify optional features; the separate required gates and
installed pilot do. Logs are `/private/tmp/gapply-schema-race-check.log`,
`/private/tmp/gapply-schema-race-py312-check.log`,
`/private/tmp/gapply-schema-race-materials.log`,
`/private/tmp/gapply-schema-race-backup.log`, and
`/private/tmp/gapply-schema-race-package.log`. All test data is fictional and
outside the repository. SQLite's documented transaction/snapshot semantics were
checked against https://www.sqlite.org/isolation.html and
https://www.sqlite.org/lang_transaction.html.

## Product state and limitations

The complete bounded local pilot is committed in b426e14 and described by ADR
0006 and `docs/QUICKSTART.md`: selected UTF-8 resume onboarding, explicit fact
approval, immutable user-supplied job text/URL, approved-evidence retrieval and
gaps, exact-text LaTeX/PDF and career answers, material approval, saved material
lookup, append-only manual application history/submission snapshots, withdrawal/
replacement, encrypted database backup/restore, support export and whole-home
deletion. PDF visual QA passed in the preceding development session; the
fictional bundle remains at `/private/tmp/grounded-apply-pilot-final-demo-20260905`.

- The local platform is macOS/Python 3.13.1 with TeX Live 2026. `.venv` contains
  cryptography 50.0.1, cffi 2.1.1, pycparser 3.0 and pypdf 6.10.0. Reportlab is
  not a runtime dependency. Hosted results are partial, as recorded above; the patched revision is not yet verified there.
- Real candidate data has never been loaded in this task. No real profile or
  external application was created. Private data belongs outside the checkout.
- Only UTF-8 text input is supported. Name/contact labels and per-fact approval
  are explicit. Tailoring selects/orders approved wording; no model, discovery,
  live job fetch, semantic rewriting, browser fill or submission is implemented.
- Schema 4 stores pilot material bytes and history, retaining backup/deletion
  coverage. External sources, exports and backups remain caller-owned. Database
  backup and deletion inventory have the documented 16 MiB bounds.
- Retired claims block future use while immutable submission history remains.
  Current authority comes from validated profile/packet services, not raw rows.
- Filesystem guards are sampled checks, not same-UID authentication or secure
  erasure. Partial deletion operations are never silently resumed.

## Next exact tasks

1. Start with `git diff -- src/grounded_apply/repositories/_schema.py tests/test_schema.py`.
   The local fix and verification are complete; review the six-file change for
   commit/push. No commit or push was performed in this session.
2. After pushing the correction, inspect all four jobs in the new GitHub Actions
   run. Record the exact revision and outcomes here. Do not mark the hosted
   matrix implemented until the patched revision passes all targets. Rerunning
   3350c55 may intermittently pass but does not contain the race correction.
   Current CI covers base and encryption/package checks; hosted TeX/pilot
   coverage is separate future work.
3. For personal pilot use, start with `docs/QUICKSTART.md`, an explicit private
   storage target, a user-supplied UTF-8 resume path and job text. Review selected
   facts before recording approvals. Never use fictional fixture facts as theirs.

## First command

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error -m unittest tests.test_schema -v
```
