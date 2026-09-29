# Session handoff

The single live development checkpoint. The [README](../README.md) is the user
entry point, [roadmap](ROADMAP.md) owns scope, and [development guide](DEVELOPMENT.md)
owns reproducible commands. Older session notes are recoverable through Git
history rather than maintained as additional status documents.

## Current state

- **Updated:** September 29, 2026.
- **Release:** public experimental alpha, version `0.1.0a0`, schema 7.
  [GitHub repository](https://github.com/chenyugoal/grounded-apply), default `main`,
  private vulnerability reporting enabled. No PyPI package or tagged release.
- **Development:** broader feature work remains paused. This session cleans
  redundant documentation and removes the fully merged development branch.
- **Branch audit:** after `git fetch origin --prune`, both
  `git rev-list --count main..codex/phase-0-truth-layer` and
  `git rev-list --count main..origin/codex/phase-0-truth-layer` returned zero.
  No other worktree used the branch. The local tip `a7cb8ae` and remote tip
  `a0bad45` are preserved in `main`; remote dry-run and actual deletion succeeded,
  followed by local `git branch -d`. Only `main` remains locally and on origin.
- **Cleanup:** removed the two September 21 session archives; condensed the
  roadmap, maintainer guide and reference; retained user guides, design and ADRs.
  The earlier documents remain available at `b27b140` and in file history.
- **Working tree:** this documentation cleanup is committed on `main`, based on
  `b27b140`. Use `git log -1` for its commit and `git status` for later changes.
  Production code, tests, scripts, dependencies and migrations are unchanged.
- **Privacy:** all verification uses fictional data outside Git. No personal
  job-search runtime is part of this handoff.

## Current verification

The resume command `git diff --check` passed. This cleanup's local logs are in
`/private/tmp/gapply-cleanup-verification-20260929`; temporary logs are supporting
local evidence, not repository dependencies. Environment: macOS 26.6.2 arm64,
Python 3.12.14, pypdf 6.10.0, cryptography 50.0.1, TeX Live 2026.

**Full regression: PASS**, 1,604 tests, zero skips, 974.138 seconds, exit 0.
All eleven source gates and all three fresh installed-package gates also passed.
Sequential maximum-capacity verification passed as well. No known local check
failure remains.

Full regression command:

```bash
umask 022
PYTHONDONTWRITEBYTECODE=1 ./scripts/check
```

Source gate command prefix:
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/`.

| Gate | Result |
|---|---|
| `check_onboarding.py --with-materials` | PASS |
| `check_materials.py` | PASS |
| `check_backup.py` | PASS |
| `check_pilot.py` | PASS |
| `check_workable.py` | PASS |
| `check_batch.py --with-backup` | PASS |
| `check_search.py --with-backup` | PASS |
| `check_schedule.py --with-backup` | PASS |
| `check_source_window.py --with-backup` | PASS |
| `check_search_filters.py --with-backup` | PASS |
| `check_source_rotation.py --with-backup` | PASS |

Fresh package command prefix:
`PYTHONDONTWRITEBYTECODE=1 /private/tmp/gapply-public-alpha-verification-20260929/build-tools/bin/python scripts/check_package.py`.

| Additional arguments | Result |
|---|---|
| None (base installation) | PASS |
| `--backup-wheelhouse /private/tmp/gapply-public-alpha-verification-20260929/wheelhouse` | PASS |
| `--pilot-wheelhouse /private/tmp/gapply-public-alpha-verification-20260929/wheelhouse` | PASS |

The full installed pilot includes both extras and the above-16-MiB capacity
smoke. Commands and timings are in `source-gates.jsonl` and `package-gates.jsonl`.
Reproducible environment setup is in [DEVELOPMENT.md](DEVELOPMENT.md).

Maximum-capacity command, PASS / 83.64 seconds:

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_storage_capacity.py --workspace /private/tmp/gapply-cleanup-verification-20260929/capacity
```

It exactly restored a 267,415,552-byte database from a 356,554,263-byte archive,
with three real PDFs, quiet replay, historical audits and mutation refusals.
Peak child RSS was 2,439,462,912 bytes (about 2.27 GiB). All four GETs used
fictional fixtures; no real network requests occurred. See `capacity.log` and
`capacity/capacity-summary.json` in the cleanup verification directory.

Documentation review passed: 34 Markdown files, 223 local links and 50 fragments.
`git diff --check` passed. `actionlint -shellcheck= -pyflakes= .github/workflows/check.yml`
and repository skill validation passed. Production/test/script/migration and
dependency files compare unchanged against `b27b140` with `git diff --exit-code`.

Hosted CI at `b27b140` **passed all four jobs**: Ubuntu 24.04 and macOS 15, each
with Python 3.12 and 3.13. Every job passed the base suite, required encryption,
base installed package and offline installed backup extra:
[verified run](https://github.com/chenyugoal/grounded-apply/actions/runs/36638357341).
The previous Ubuntu timeout is resolved by the longer job deadline. These hosted
checks do not exercise the complete PDF workflow; local macOS verification does.

The release privacy audit inspected all then-reachable commits and tracked files
without finding real credentials, candidate records or runtime artifacts. The
release's fictional first-use walkthrough produced a reviewed one-page PDF,
preserved ownership and graduation qualifiers, left sponsorship as `NeedInfo`,
and stopped at manual submission. It does not measure human effort savings.
Release verification details remain in Git history at `b27b140`.

## Limits and deferred work

- Independent human first-use acceptance and comparative active-time savings
  remain unmeasured. No browser filling, semantic rewriting, GUI, email/calendar
  integration, OCR or DOCX intake is implemented.
- Full PDF integration is verified locally on macOS/Python 3.12.14; native Windows
  setup and broader PDF integration coverage remain unfinished.
- Intake requires review of extraction, section classification and wrapped
  publications. Typed imports and exact evidence bounds do not prove arbitrary
  proposed text is semantically equivalent to its source.
- Discovery is bounded to configured sources. Literal location filters can match
  `Remote` in `Not Remote`; unknown locations remain unknown. Workable pagination
  and total coverage are not established. Daily runs need an external wake-up.
- Database snapshots are bounded to 256 MiB and encrypted archives to 384 MiB;
  near-capacity backup/restore used about 2.3 GiB of memory. Source documents and
  exports need separate backups. Automatic retention, per-record deletion,
  production document sharing and conversion remain unfinished.
- Full search-checkpoint and schedule/workflow historical custody is deferred.
  Filesystem checks are sampled and cannot defeat every same-user race.
  Local storage does not make Codex conversations local.

## Next exact task

**Cleanup complete; broader development stays paused.** No new feature milestone
or unattended follow-up is selected. Source behavior and supported alpha scope
are unchanged; the platform documentation now reflects the completed hosted checks.

If the user supplies a final illustration, start with `docs/PRODUCT_VISION.md`,
check current/wishlist labels and human submission, then replace the README
Mermaid figure with the reviewed asset and accessible alternative text.

## First command

After the required repository-resume reads:

```bash
git diff --check
```

Inspect any failure before editing; preserve unfamiliar changes. Use current Git
state rather than a historical checkpoint to decide what exists.
