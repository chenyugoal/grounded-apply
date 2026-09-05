# Session handoff

This is the single live checkpoint. Durable scope is in `ROADMAP.md`; accepted
architecture decisions are in `docs/adr/`.

## Checkpoint

- **Updated:** 2026-09-04 22:02 CDT
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `b426e14` (local origin matches).
  The user committed and pushed the previously verified pilot. This session has
  not committed or pushed anything.
- **Current task:** Diagnose the failure email for GitHub Actions run
  [33940364654](https://github.com/chenyugoal/grounded-apply/actions/runs/33940364654/workflow).
  The user supplied the exact annotation: line 28, unrecognized named-value
  `runner` in `runner.temp`. GitHub rejected the workflow before test jobs ran.
- **Root cause:** `.github/workflows/check.yml` used the runner context inside
  job-level `env`, where GitHub does not allow it. The local application tests
  never validated workflow expressions. This is a confirmed workflow defect;
  it is not evidence of an application-test failure or an unsuccessful push.
- **Local fix:** The first shell step writes the isolated runtime path using
  `RUNNER_TEMP` into `GITHUB_ENV`. Later steps inherit it. No application code,
  dependencies, permissions, action pins, matrix targets or submission behavior
  changed. The expression linter reproduces the original failure and accepts
  the corrected workflow.
- **Working tree:** Only `.github/workflows/check.yml`, `README.md`,
  `docs/DEVELOPMENT.md`, and this checkpoint are modified. Starting tree was
  clean. The fix is local and uncommitted. No GitHub rerun or external write was
  performed. The hosted matrix still requires verification after the fix is
  committed and pushed; rerunning b426e14 repeats the invalid workflow.

## Verification

Session-resume command on b426e14:

```text
PATH="$PWD/.venv/bin:$PATH" ./scripts/check
PASS — 345 tests, zero skips, 105.257s
```

Narrow workflow verification:

```text
/private/tmp/gapply-actionlint-w2c997go/actionlint -shellcheck= -pyflakes= .github/workflows/check.yml
BEFORE: exit 1, runner context rejected at line 28
AFTER: exit 0, no findings
```

Actionlint 1.7.12 came from the official release. The Darwin/arm64 archive's
published SHA-256 was verified:
`aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f`.
The binary stays in the disposable directory above, outside the checkout.
README and DEVELOPMENT now document this separate workflow expression check.
A real `bash -e -o pipefail` smoke executed the exact new setup command using a
synthetic runner directory containing spaces. It produced the exact one-line
GITHUB_ENV record and created no runtime directory. GitHub's own context rules
independently confirm the diagnosis.

After the workflow fix:

```text
PATH="$PWD/.venv/bin:$PATH" ./scripts/check
PASS — 345 tests, zero skips, 107.484s (/private/tmp/gapply-ci-fix-check.log)
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_materials.py
PASS — 9 tests, zero skips, 30.539s
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error scripts/check_backup.py
PASS — 28 tests, zero skips, 8.489s
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-backup-wheels-20260904
PASS — source archive/wheel, fresh offline base + backup/materials installation,
complete synthetic CLI pilot, encrypted backup/restore, immutable history,
retirement, support export, deletion, replay and diagnostic separation
git diff --check
PASS — the complete four-file diff was inspected; no application data, secrets,
generated artifacts or unrelated changes were introduced
```

The latter logs are `/private/tmp/gapply-ci-fix-materials.log`,
`/private/tmp/gapply-ci-fix-backup.log` and `/private/tmp/gapply-ci-fix-package.log`.
All test data is fictional and outside the repository. The unauthenticated
browser could not read the private run; the user-provided annotation is the
hosted failure evidence. No successful hosted matrix is claimed.

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
  not a runtime dependency. Hosted OS/interpreter test results remain unverified.
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

1. The local fix and verification are complete. Review
   `.github/workflows/check.yml` and its documentation changes for commit/push.
2. After the correction is committed and pushed, inspect the new GitHub Actions
   run and its four matrix jobs. Do not mark the hosted matrix implemented until
   the corrected revision actually passes there. The current CI covers base and
   encryption/package checks; hosted TeX/pilot coverage is separate future work.
3. For personal pilot use, start with `docs/QUICKSTART.md`, an explicit private
   storage target, a user-supplied UTF-8 resume path and job text. Review selected
   facts before recording approvals. Never use fictional fixture facts as theirs.

## First command

```bash
/private/tmp/gapply-actionlint-w2c997go/actionlint -shellcheck= -pyflakes= .github/workflows/check.yml
```

If the disposable linter has been cleaned up, follow the pinned official release
and checksum instructions in DEVELOPMENT before workflow validation.
