# Session handoff

The single live development checkpoint. The [README](../README.md) is the user
entry point, [roadmap](ROADMAP.md) owns scope, and [development guide](DEVELOPMENT.md)
owns reproducible commands. Older session notes are recoverable through Git
history rather than maintained as additional status documents.

## Current state

- **Updated:** September 30, 2026.
- **Release:** public experimental alpha, version `0.1.0a0`, schema 7.
  [GitHub repository](https://github.com/chenyugoal/grounded-apply), default `main`,
  private vulnerability reporting enabled. No PyPI package or tagged release.
- **Development:** broader feature work remains paused. The current request is
  final proofreading, commit and push of the README presentation update; no
  roadmap feature is selected.
- **Documentation:** the README opens with the user-supplied `product-vision.png`
  and descriptive alternative text. Removed its Mermaid flowchart, tightened
  the introduction and current feature summary, and clarified supported storage
  capacity. The vision brief now describes one coherent complete experience;
  current alpha capabilities remain explicit in README prose and the roadmap.
  The development guide records the image and brief locations.
- **Working tree:** this checkpoint belongs to the README illustration commit
  on `main`, based on `150abc5`. The user authorized committing and pushing the
  reviewed documentation and image to `origin/main`. Use `git log -1` for the
  containing commit and `git status` for later changes. The supplied image is
  unchanged (1,547,537 bytes, 1672 × 941 pixels). Production code, tests, schema,
  dependencies, supported commands and roadmap status are unchanged.
- **Privacy:** all verification uses fictional data outside Git. No personal
  job-search runtime is part of this handoff.

## Current verification

The resume command `git diff --check` passed. Verification logs and exact
command/outcome JSONL are in
`/private/tmp/gapply-readme-verification-20260930-qet2n5_7`; these temporary
artifacts are local evidence, not repository dependencies. Environment: macOS,
Python 3.12.14, pypdf 6.10.0, cryptography 50.0.1 and TeX Live 2026. Tests use
fictional data and make no live job-board or model requests.

**Documentation checks: PASS**, 34 Markdown files, 231 local references
(including images), 50 fragments, zero issues:

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python /private/tmp/gapply-readme-verification-20260930-qet2n5_7/check_docs.py
```

The same check verifies hero placement, Mermaid removal, the aligned brief,
PNG dimensions and unchanged image SHA-256:
`3742a8bf710622d4d08ace9de7d3f540e86e4de2b2edf803c49a952dc687d91f`.
The original illustration was visually inspected. Pandoc successfully rendered
standalone GFM HTML with the embedded image using:

```bash
pandoc --standalone --embed-resources --from=gfm --to=html5 --metadata title='Grounded Apply README preview' --css=/private/tmp/gapply-readme-verification-20260930-qet2n5_7/readme-preview.css README.md -o /private/tmp/gapply-readme-verification-20260930-qet2n5_7/readme-preview.html
```

Browser screenshot inspection of this temporary HTML was unavailable: the
browser URL policy blocks `file:` URLs. No browser workaround was attempted.

**All eleven source gates: PASS**, each exit 0. Command prefix:
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

**All three fresh installed-package gates: PASS**, each exit 0. Command prefix:
`PYTHONDONTWRITEBYTECODE=1 /private/tmp/gapply-public-alpha-verification-20260929/build-tools/bin/python scripts/check_package.py`.

| Additional arguments | Result |
|---|---|
| None (base installation) | PASS |
| `--backup-wheelhouse /private/tmp/gapply-public-alpha-verification-20260929/wheelhouse` | PASS |
| `--pilot-wheelhouse /private/tmp/gapply-public-alpha-verification-20260929/wheelhouse` | PASS |

The installed pilot includes both extras, the complete PDF workflow and the
above-16-MiB capacity smoke. Reproducible optional-tool setup remains in
[DEVELOPMENT.md](DEVELOPMENT.md). Timings and commands are in `source-gates.jsonl`
and `package-gates.jsonl`.

**Workflow validation: PASS**, exit 0:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/gapply-public-alpha-verification-20260929/actionlint -shellcheck= -pyflakes= .github/workflows/check.yml
```

**Full regression: PASS**, 1,604 tests, zero skips, 980.037 seconds, exit 0
(984.997 seconds for the complete command). Run with `umask 022`:

```bash
PYTHONDONTWRITEBYTECODE=1 ./scripts/check
```

**Sequential maximum-capacity gate: PASS**, 84.582 seconds, exit 0, run after
all other gates finished:

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_storage_capacity.py --workspace /private/tmp/gapply-readme-verification-20260930-qet2n5_7/capacity
```

It exactly restored a 267,415,552-byte database from a 356,554,263-byte archive,
with three real PDFs, quiet replay, historical audits and mutation refusals.
Peak child RSS was 2,434,629,632 bytes (about 2.27 GiB). All four GETs used
fictional fixtures; there were no network requests. Measurements are in
`capacity/capacity-summary.json`.

All 17 repository verification invocations passed, with no skipped gates or
known local test failures. The documentation diff received independent review
with no actionable issues. Final `git diff --check`, local-link/image validation
and complete diff/working-tree privacy inspection passed. Temporary HTML
inspection confirmed one embedded PNG with descriptive alt text and both tables;
browser screenshot verification remains unavailable as described above.
The final proofread preserved the user's PDF/pasted-text/questions introduction,
clarified that PDF intake requires selectable text, and made minor wording
corrections. Documentation/image checks, HTML rendering and `git diff --check`
were repeated before committing. The full regression and 17 gates above ran
earlier in this same documentation work session; application code and verified
test inputs have not changed since. Hosted release CI remains historical
evidence at `b27b140`; a new hosted result must be checked separately.

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
  near-capacity backup/restore used over 2 GiB of memory. Source documents and
  exports need separate backups. Automatic retention, per-record deletion,
  production document sharing and conversion remain unfinished.
- Full search-checkpoint and schedule/workflow historical custody is deferred.
  Filesystem checks are sampled and cannot defeat every same-user race.
  Local storage does not make Codex conversations local.

## Next exact task

**README update complete; broader development stays paused.** Start with
`git status --short --branch`, then `git log -1 --oneline` to inspect the
containing commit and any later work. No new feature milestone or unattended
follow-up is selected.

## First command

After the required repository-resume reads:

```bash
git diff --check
```

Inspect any failure before editing; preserve unfamiliar changes. Use current Git
state rather than a historical checkpoint to decide what exists.
