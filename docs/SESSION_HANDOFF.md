# Session handoff

This is the single live checkpoint. Durable scope is in `ROADMAP.md`; accepted
architecture decisions are in `docs/adr/`.

## Checkpoint

- **Updated:** 2026-09-19 15:40 CDT
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `66f71ad` (local origin matches).
  Starting tree was clean. No commit, push or hosted CI rerun was made.
- **Milestone:** Codex-guided application preparation for industry research and
  engineering roles (ADR 0007) remains implemented and locally verified.
- **Current change:** versioned resume presentation (ADR 0008) is implemented
  and locally verified. New builds use 11-point sans-serif, centered identity,
  section rules, aligned role/education headings, restrained bold labels,
  source-aware bullets, ragged-right wrapping and heading space reservations.
- **CLI:** `materials build --layout-file FILE` accepts a closed schema mapping
  selected claim IDs to heading/bullet/paragraph, without factual edits or TeX.
  `-` reads stdin; only one input may use stdin. Choices bind workflow identity
  and the bundle. Existing version-1 rendering, reads, export, approvals and
  idempotent retries retain their original transformation. Schema remains 4.
- **Skill:** inspect the supplied original as a visual reference, preview styles,
  inspect every PDF page and rebuild through the CLI. Do not silently discard
  relevant evidence to bypass a layout error. The existing lean review default
  remains: conversational views, no duplicate Markdown/profile/session exports.
- **Working tree:** source, tests, synthetic pilot, skill, README, quickstart,
  development/workflow/roadmap/checkpoint docs are modified; ADR 0008 is new.
  All changes are uncommitted. No personal data, PDFs or other generated artifacts
  belong in this checkout.

## Resume findings and environment

The starting checkpoint described the previous skill change as uncommitted;
Git shows it committed in `66f71ad`. The mandatory first command passed before
edits:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_briefing tests.test_launcher -v
PASS — 13 tests, zero skips, 40.344s
```

The bounded scope was presentation and its Codex workflow, preserving approved
facts and historical versions. Initial synthetic header/bold-label extraction
checks found missing spaces across font/box boundaries. Version 2 now emits
explicit PDF interword spaces; the exact-text verifier was not weakened.
Final regression checks pass. No unresolved local failures remain.

Current environment: macOS, Python 3.12.14, SQLite 3.53.1, TeX Live 2026,
pypdf 6.10.0, cryptography 50.0.1, cffi 2.1.1, pycparser 3.0. Local `needspace`
was available and is now documented alongside lmodern, geometry and enumitem.
No new Python runtime dependency was added. The launcher chooses GAPPLY_PYTHON,
then the repository virtualenv, then PATH python3; system python3 is 3.9 and is
not a supported application interpreter. Earlier 3.13.1 verification predates
this renderer change; do not present it as this session's result.

Disposable build/skill tools: `/private/tmp/grounded-apply-milestone-tools-20260918`.
Offline wheels: `/private/tmp/grounded-apply-milestone-wheels-20260918`.
The repository `.venv` is usable; a fresh checkout needs Python 3.12+, optional
extras and local TeX for their respective gates.

## Verification

```text
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_materials.py
PASS — 15 tests, zero skips, 56.010s
./scripts/check
PASS — 366 tests, zero skips, 172.395s; includes CLI smoke checks
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_backup.py
PASS — 28 tests, zero skips, 9.053s
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_pilot.py
PASS — complete synthetic CLI pilot, real PDF, diagnostics, backup/restore,
retirement and deletion; now exercises layout-file preview/build/replay/export
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
PASS — source archive/wheel, fresh offline install, migrations/decisions,
optional encryption and complete installed pilot with layout-file
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python /Users/chenyu/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/grounded-apply
PASS — skill format; relative references also resolve
./scripts/gapply materials build --help
PASS — layout-file option and stdin description present
git diff --check
PASS — complete diff inspected; no personal facts, generated artifacts or unrelated changes
```

Logs: `/private/tmp/gapply-layout-{materials,full,backup,pilot,package}-20260919.log`.
All development gates use synthetic data. No delegated agent run was requested
or used for this change. The private user demonstration was regenerated through
validated CLI services separately; its content and workflow identifiers remain
outside repository documentation.

The six new tests cover presentation schema/type/selection rejection, layout
binding to approval/idempotency, legacy build/replay/export/approval, approved
TeX-source marker handling, real one/two-page headers and long prose, impossible
word overflow and real legacy PDFs. Existing fabricated-text, stale-evidence,
TeX injection and submission/history tests also pass.

Visual QA used `tests.test_materials.layout_fixture(1)` and `(7)` through the
real renderer. Saved synthetic PDFs are `/private/tmp/gapply-layout-synthetic-1.pdf`
and `-7.pdf`. Both pages of the latter were rasterized with bundled pdftoppm
(`-scale-to 1200 -png`) and inspected: readable text, aligned headings and dates,
no clipping/overlap, expected degree status preserved, role headings kept with
following bullets. A separate private one-page demonstration was also inspected.
Exact text extraction remains required; visual inspection is an additional gate.

## Product state and limits

- Normal use is through Codex and `.agents/skills/grounded-apply/SKILL.md`;
  the CLI remains the deterministic contract. See `docs/CODEX_WORKFLOW.md`.
- Role grouping, chronology and relevance are chosen by Codex/user, not inferred
  by the renderer. Four-field headings only replace exact pipe separators with
  layout; all approved text, dates, ownership and metrics remain in the mapping.
- The one/two-page limit remains. Unsupported glyphs, overflow and unexpected
  PDF text fail closed; there is no automatic font shrinking or truncation.
  Source TeX is treated as evidence, never executed. Universal ATS compatibility
  has not been established. Theme customization is not shipped.
- Semantic rewriting, PDF/DOCX ingestion, live fetching/discovery, browser fill,
  messaging, scheduled reminders, automatic retention and GUI remain later work.
- Schema 4/private runtime contracts remain unchanged. External sources/exports
  and backups are caller-owned. The 16 MiB database/deletion bounds, sampled
  same-UID TOCTOU limits, no secure-erasure claim and read-only WAL/sidecar refusal
  remain in force. No personal workflow state belongs in this handoff.
- Hosted CI was not inspected this session. Earlier user-reported evidence passed
  macOS 15/Python 3.12 and 3.13 plus Ubuntu 24.04/Python 3.13. The former Ubuntu/
  Python 3.12 race was corrected before this session, but its hosted rerun remains
  unverified. Local success is not complete OS/interpreter release coverage.

## Next exact tasks

1. Start with `git diff --stat` and review the uncommitted renderer, service, CLI,
   regression tests, pilot and documentation changes before any requested commit.
2. For actual use, follow `docs/CODEX_WORKFLOW.md` with the user's already
   authorized private home. New builds use version 2 automatically; inspect
   every page and obtain the normal exact-bundle approval before marking ready.
3. If minimal profile retrieval is the next requested improvement, start at
   `src/grounded_apply/cli.py::_command_profile_show`: add a claim-ID filter with
   validated source references and unknown/retired/pending tests. It is not yet
   implemented and is not required for the current presentation milestone.

## First command

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_materials.py
```
