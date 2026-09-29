# Session handoff

This is the single live checkpoint. [ROADMAP.md](ROADMAP.md) owns feature scope;
[README](../README.md) describes the experimental alpha for users.

[Current verification](#current-verification) | [Limits](#limits-and-deferred-work) |
[Next task](#next-exact-task) | [First command](#first-command)

## Release wrap-up and checkout

- **Updated:** September 29, 2026.
- **Scope:** finish the existing usable foundation, make the documentation
  approachable, merge the development branch and publish the repository as an
  experimental alpha. Broader feature development is paused. No new feature
  milestone or unattended development is selected.
- **Starting branch / HEAD:** `codex/phase-0-truth-layer`, `9a3b4bf`, matching
  origin. Local and remote `main` are `b2fc88e`. The 91 pre-existing changed/new
  files from the September 21 usability window are preserved for integration.
- **Current candidate:** `a0bad45` on `codex/phase-0-truth-layer`, pushed to origin.
  `342a688` preserved the previous 91-file implementation and added release docs;
  `a0bad45` fixes one test fixture's dependence on shell umask. No production code
  changed during wrap-up. All local gates and both hosted macOS jobs passed.
  The user selected publication of the macOS-tested alpha with Linux pending.
  Main remains unmerged and the repository private until the release step below.
  There are no remote-only
  branches, tags, releases, issues, pull requests or uploaded Actions artifacts.
- **Release positioning:** usable supervised career-profile and application
  preparation workflow; no demonstrated reduction in active user effort.
  Public source availability is not completion of the product-acceptance trial.
- **Program freeze:** this wrap-up changes documentation, one test fixture and
  the CI deadline (20 to 60 minutes; tests unchanged). Version remains `0.1.0a0`, schema 7, with unchanged production code, dependencies
  and versioned histories.
- **Personal state:** no real CV, job-search runtime or sensitive answer was
  opened. All test inputs and outputs remain fictional and outside Git.
  The personal LinkedIn draft is outside this repository.

## Current verification

The mandatory first command, `git diff --check`, passed. Logs and fictional
outputs are under `/private/tmp/gapply-public-alpha-verification-20260929`.
Environment: macOS 26.6.2 arm64, Python 3.12.14, pypdf 6.10.0,
cryptography 50.0.1, TeX Live 2026. No real candidate runtime was used.

**Full regression: PASS**, 1604 tests, zero skips, 974.405 seconds, exit 0:
`umask 022; PYTHONDONTWRITEBYTECODE=1 ./scripts/check`.
Log: `full-suite-corrected.log`. Production/test bytes match `a0bad45`.
The initial run inherited
`umask 077` from private-log setup and failed the deliberately unsafe-directory
fixture: `mkdir(mode=0o755)` became `0700`. It was stopped (exit 130), not counted
as a pass. The fixture now explicitly sets `0755`; the focused test passes under
both `077` and `022`, and the whole SnapshotTests class passes. Production refusal
behavior was correct; no security check was relaxed. The required encryption gate
also passed under `077` (38 tests, zero skips, 10.549 seconds).

All eleven source gates passed with exit 0. Each uses this exact prefix:
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/`.

| Script and arguments | Result / seconds |
|---|---|
| `check_onboarding.py --with-materials` | PASS / 61.000 |
| `check_materials.py` | PASS / 74.696 |
| `check_backup.py` | PASS / 10.237 |
| `check_pilot.py` | PASS / 35.537 |
| `check_workable.py` | PASS / 17.523 |
| `check_batch.py --with-backup` | PASS / 56.233 |
| `check_search.py --with-backup` | PASS / 74.705 |
| `check_schedule.py --with-backup` | PASS / 76.682 |
| `check_source_window.py --with-backup` | PASS / 29.815 |
| `check_search_filters.py --with-backup` | PASS / 13.733 |
| `check_source_rotation.py --with-backup` | PASS / 29.396 |

Fresh installed base gate, PASS / 9.09 seconds:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/gapply-public-alpha-verification-20260929/build-tools/bin/python scripts/check_package.py
```

Fresh installed full pilot, PASS / 395.60 seconds:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/gapply-public-alpha-verification-20260929/build-tools/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/gapply-public-alpha-verification-20260929/wheelhouse
```

These build/install outside the checkout and validate both optional extras,
migrations, onboarding, Workable, lifecycle, batch/search/daily/window/filter/
rotation and an installed capacity smoke above 16 MiB. Logs: `base-installed.log`
and `installed-pilot.log`. Installed capacity: 18,980,864-byte database,
25,308,011-byte archive, two PDFs, no real network requests.

Sequential maximum-capacity gate, PASS / 83.92 seconds:

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_storage_capacity.py --workspace /private/tmp/gapply-public-alpha-verification-20260929/capacity
```

It restored a 267,415,552-byte database from a 356,554,263-byte archive, audited
three real PDFs, preserved quiet replay and refused invalid mutations. Peak child
RSS was 2,438,774,784 bytes (about 2.27 GiB); all four GETs used fictional fixtures.
Results: `capacity.log` and `capacity/capacity-summary.json`.

`actionlint -shellcheck= -pyflakes= .github/workflows/check.yml` passed with
checksum-verified actionlint 1.7.12. Repository skill validation also passed:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/gapply-public-alpha-verification-20260929/build-tools/bin/python /Users/chenyu/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/grounded-apply
```

Validator-only PyYAML 6.0.3 was installed in the disposable build environment;
application dependencies are unchanged. Log: `skill-validation.log`. All local documentation links/fragments passed
(36 Markdown files, 217 links, 55 fragments before this final checkpoint update).

A fresh agent-operated walkthrough followed the public docs on a new fictional
CV and posting: eight facts retained and approved separately, skipped-fact
pagination survived resumption, a one-page PDF and grounded Python answer were
produced, sponsorship remained NeedInfo, and tracking resumed at ready_for_review
with manual_submission next and no external action. Visual PDF inspection found
no clipping; contributed ownership and expected graduation were preserved.
Evidence: `first-use/result.json`, `first-use/commands.jsonl` and
`first-use/materials/resume.pdf`. This does not measure human effort savings.

Hosted CI for `a0bad45` completed with both macOS 15 jobs (Python 3.12 and 3.13)
passing the base suite, required encryption and both installed-package gates.
Both Ubuntu 24.04 jobs were cancelled by the 20-minute deadline, with the explicit
annotation “The job has exceeded the maximum execution time of 20m0s.” The overall
run is cancelled, not passed:
[release checks](https://github.com/chenyugoal/grounded-apply/actions/runs/36635404168).
The workflow now allows 60 minutes, retaining every test. Its actionlint check
passed again after the timeout edit. The user explicitly chose the macOS-tested
experimental alpha with Linux verification pending; no Linux pass is claimed.

The previous complete verification and capability contracts are preserved in the
[September 21 closeout archive](SESSION_HISTORY_2026-09-21_CLOSEOUT.md).
The earlier [September 21 history](SESSION_HISTORY_2026-09-21.md) remains unchanged.
Historical results do not substitute for the current release checks.

Public-data review has scanned all 20 locally reachable commits, 513 unique Git
blobs, and 254 original tracked/nonignored files. No real credentials, candidate
records, runtime databases, generated resumes or private archives were found.
Secret-shaped fixtures use fictional values. Git history contains normal author
identity metadata and historical maintainer machine paths; publication exposes
that history. Remote inventory matches the inspected main/development branches;
there are no tags, releases, pull requests or Actions artifacts to disclose.

The GitHub API confirms admin/push access and private visibility. Private
vulnerability reporting returns 404 while private; activation and verification
remain required immediately after publication. SECURITY.md supplies the intended
private form plus a content-free fallback for channel failures.

## Delivered scope

- Codex-guided text, selectable-text PDF and static-LaTeX intake; optional
  questions; complete line inventory; publication grouping; paged fact review.
- Reusable approved facts with provenance, explicit approval and retirement.
- Evidence comparisons, exact approved-wording PDF resumes and career answers,
  bounded batch preparation, exports and manual application history.
- Configured Greenhouse, Ashby, Lever and Workable boards, bounded Netflix
  discovery, source planning, saved searches and externally triggered daily runs.
- Optional encrypted database backup/restore, fixed-schema support export and
  confirmed whole-portable-home deletion.
- User-facing README, supported/wishlist table, labeled Mermaid vision schematic,
  [illustration brief](PRODUCT_VISION.md), and [release notes](../CHANGELOG.md).

## Limits and deferred work

- Human first-use acceptance and a comparable active-time savings trial remain
  unmeasured. An agent walkthrough is not a human study.
- Linux verification remains pending after the hosted 20-minute timeouts.
  Hosted macOS base/backup/package checks pass on Python 3.12 and 3.13; full local
  PDF workflow verification uses macOS/Python 3.12.14. Native Windows is absent.
- No browser safe-fill, automatic submission, semantic rewriting, GUI,
  email/calendar integration, OCR or DOCX intake. Wishlist items have no schedule.
- Source accounting does not establish semantic completeness. PDF order, section
  classification and wrapped publications require review; generic import validates
  evidence bounds and types, not semantic equivalence of arbitrary proposed text.
- Discovery covers bounded configured sources; literal location filters can
  match `Remote` in `Not Remote`. Unknown locations stay unknown. Workable totals
  and pagination are not established; broader market coverage remains absent.
- Daily execution requires an authorized external wake-up mechanism. None is
  installed by the CLI or restarted by this wrap-up.
- Storage is bounded to a 256 MiB database and 384 MiB encrypted archive.
  Near-capacity backup/restore previously used about 2.3 GiB of process memory.
  Source documents, exports and backup copies are separately owned files.
- Automatic retention, per-record deletion, production document sharing and
  new-home storage conversion are unfinished. Strict search-checkpoint raw
  row/JSON custody and complete schedule/workflow custody remain deferred.
- Filesystem checks are sampled and do not defeat a same-UID process winning a
  race after the final check. Local storage does not make Codex conversations local.

## Next exact task

Commit the final release documentation and validated CI deadline change, merge
the development branch into `main`, and publish the repository as the explicitly
authorized macOS-tested experimental alpha. Verify public visibility and enable
private vulnerability reporting. Preserve Linux verification as pending. No
feature implementation or time-saving product milestone is selected.

The optional final illustration should replace the Mermaid figure only after
review against `docs/PRODUCT_VISION.md`; do not invent completion of wishlist work.

## First command

After the required repository-resume reads:

```bash
git diff --check
```

If this fails, inspect the exact file/line before editing. Preserve unrelated
work and use the current Git state rather than historical checkpoint assumptions.
