# Session handoff

This is the single live checkpoint. Durable scope is in `ROADMAP.md`; accepted
architecture decisions are in `docs/adr/`.

## Checkpoint

- **Updated:** 2026-09-19 12:05 CDT
- **Branch / HEAD:** `codex/phase-0-truth-layer`, `3fc3251` (local origin matches).
  Starting tree was clean. No commit, push, or GitHub rerun was made.
- **Milestone:** Codex-guided application preparation for industry research and
  engineering roles (ADR 0007) is **implemented and locally verified**.
- **Delivered:** repository `grounded-apply` skill and conversational guide;
  read-only `brief` service/CLI; deterministic stage-based next actions;
  explicit/local-virtualenv interpreter selection; synthetic two-job researcher
  pilot and fresh installed-pilot coverage. Schema remains 4. No model provider,
  live fetching, browser fill, message, or external submission was added.
- **Briefing:** validates profile, jobs, materials and application history in
  one read snapshot. Output contains counts, IDs, URLs, current readiness,
  latest/application-bound material references and actions; no raw career text,
  answer bodies, review tokens or PDF bytes. Keyword retrieval counts are not
  fit scores. Seven-day response checks are configurable per call and on demand.
- **Current change:** the skill now defaults to conversational, on-demand fact
  reviews instead of persistent Markdown copies or duplicate session notes.
  Explicitly requested private exports remain possible. Database evidence,
  approvals, material versions and submission history remain authoritative;
  this is workflow guidance, not a per-profile setting or automatic cleanup.
- **Working tree:** the milestone is committed in `3fc3251`. Only the current
  skill/documentation correction is uncommitted: `.agents/skills/grounded-apply/SKILL.md`,
  `docs/CODEX_WORKFLOW.md`, `docs/ROADMAP.md`, and this handoff. No application
  code, schema, configuration, commands or runtime layout changed.

## Resume findings and environment

The starting checkpoint incorrectly described the milestone as uncommitted;
Git shows it in `3fc3251`. The required first command passed unchanged:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_briefing tests.test_launcher -v
PASS — 13 tests, zero skips, 39.521s
```

The bounded follow-up is a skill/workflow correction to ADR 0007. No personal
profile was opened or modified during this development session. Existing truth,
privacy, explicit export and destructive-action rules remain in force.

Prior session environment findings (September 18): the schema snapshot correction
was already committed in `4a86427`. Its original first-command results were:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error -m unittest tests.test_schema -v
FAIL — system python3 is 3.9; datetime.UTC is unavailable
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /Users/chenyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -W error -m unittest tests.test_schema -v
PASS — 8 tests, zero skips, 0.193s
```

The old `.venv` had broken links to a removed Homebrew Python 3.13. A creation
attempt updated its config but could not replace those links. The remaining
directory was preserved at `/private/tmp/grounded-apply-preexisting-venv-20260918`;
a fresh `.venv` was created with the bundled Python 3.12.14 and the pinned optional
dependencies installed offline from wheels. No candidate data was involved.

Current environment: macOS, Python 3.12.14, SQLite 3.53.1, TeX Live 2026,
pypdf 6.10.0, cryptography 50.0.1, cffi 2.1.1, pycparser 3.0. The wrapper chooses
`GAPPLY_PYTHON`, then `.venv/bin/python3`, then PATH's `python3`, checking the
version before imports. A fresh checkout still needs compatible Python and the
optional dependencies/TeX for PDF and backup functions.

Disposable build/skill tools: `/private/tmp/grounded-apply-milestone-tools-20260918`
(requirements-build, both extras, PyYAML 6.0.3). Dependency wheels:
`/private/tmp/grounded-apply-milestone-wheels-20260918`. The initial sandboxed pip
download failed DNS; the approved network retry passed. These are not tracked.

## Verification

Current session (September 19):

```text
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python /Users/chenyu/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/grounded-apply
PASS — skill format; relative references also resolve
./scripts/check
PASS — 360 tests, zero skips, 145.754s; includes CLI smokes
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_materials.py
PASS — 9 tests, zero skips, 30.344s
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_backup.py
PASS — 28 tests, zero skips, 10.225s
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_pilot.py
PASS — complete synthetic CLI pilot, real PDF, diagnostics, backup/restore,
retirement and deletion
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
PASS — source archive/wheel, isolated offline install, migrations, decisions,
optional encryption and complete installed pilot
git diff --check
PASS — complete four-file diff inspected; no personal workflow data or artifacts
```

Logs: `/private/tmp/gapply-lean-review-{full,materials,backup,pilot,package}-20260919.log`.
All runtime checks use synthetic, disposable data. No new implementation tests
or independent agent run are needed for this narrow skill/documentation change.
No behavior-enforcing retention feature is claimed from these checks.

Prior milestone verification (September 18, retained for implementation evidence):

```text
GAPPLY_PYTHON=/Users/chenyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error -m unittest tests.test_launcher tests.test_briefing -v
PASS — 12 tests, zero skips, 36.675s before the forward-test correction
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_briefing.BriefingTests.test_saved_draft_is_reviewable_before_an_application_is_created tests.test_briefing.SearchActionTests -v
PASS — 2 tests, zero skips, 3.363s after the correction
./scripts/check
PASS — final 360 tests, zero skips, 137.772s; includes all CLI smokes and full discovery
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_materials.py
PASS — 9 tests, zero skips, 29.413s
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_backup.py
PASS — 28 tests, zero skips, 8.905s
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_pilot.py --demo-output /private/tmp/grounded-apply-milestone-demo-final-20260918
PASS — two job families, real PDFs, exact degree/publication wording, answers,
approval/manual-submission gates, briefing, encrypted backup/restore,
retirement invalidation, immutable history, diagnostics and confirmed deletion
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
PASS — source archive/wheel, fresh offline installed extras, encryption round trip,
complete extended pilot using installed code and bundled migrations
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python /Users/chenyu/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/grounded-apply
PASS — skill format; relative references also checked and resolve
sh -n scripts/python scripts/gapply scripts/check
PASS
git diff --check
PASS — complete modified/new code, tests, skill and documentation inspected
```

Logs: `/private/tmp/gapply-milestone-{focused,check-final,materials,backup,pilot-final,package-final}.log`.
The first extended pilot exposed a harness assumption that the first global
material belonged to the engineering job; assertions now filter by job. Final
source and installed pilots pass. Final engineering selection also keeps its
project group together instead of placing an independent research bullet under it.

Visual QA command:
`pdftoppm -scale-to 1400 -png /private/tmp/grounded-apply-milestone-demo-final-20260918/resume.pdf /private/tmp/gapply-milestone-resume-final`.
Inspected its one-page PNG: readable text, dates and bullets, no clipping or
overlaps. Validation reports nine factual units, zero unsupported units, critical
fields present and human approval required.

Independent skill forward-test used only `/private/tmp/gapply-forward-20260918`.
An agent operated the skill on a fictional approved researcher profile and saved
research-engineering job. It selected evidence, filled a career question from
an approved claim, exported/visually inspected a real PDF, and recomputed all
seven export-file hashes. It preserved expected-degree/submitted-paper wording,
left sponsorship as NeedInfo, ignored job-text injection, and left the bundle
unapproved with no application/submission. It found saved drafts still suggested
assess_job; the policy and regression fix now return review_material, independently
retested without mutation. Remaining usability limit: `profile show` has no claim
filter, so source-group context may require reading the full small profile.

## Product state and limits

- Normal use is through Codex and `.agents/skills/grounded-apply/SKILL.md`;
  CLI remains the deterministic contract. See `docs/CODEX_WORKFLOW.md`.
- Development gates use only synthetic candidate data and perform no external
  application. Personal workflow state belongs outside this development handoff.
- Input remains UTF-8 text with explicit labels/recognized sections. Review
  skipped lines. Tailoring selects/orders approved wording; new prose remains
  a proposal until it passes the existing import/review contract.
- PDF/DOCX ingestion, live discovery/fetching, semantic fit/rewrite validation,
  browser filling, messaging, scheduled reminders, automatic retention and GUI
  remain later work. No promise of interviews or offers.
- Schema 4/private runtime contracts remain unchanged. External sources, exports
  and backups are caller-owned. The 16 MiB database/deletion bounds, sampled
  same-UID TOCTOU limits, no secure-erasure claim, and read-only WAL/sidecar refusal
  remain in force.
- Hosted CI was not inspected this session. Prior user-reported evidence passed
  macOS 15/Python 3.12 and 3.13 plus Ubuntu 24.04/Python 3.13. The former Ubuntu/
  Python 3.12 race is corrected in HEAD, but its hosted rerun is unverified.
  Local success is not complete OS/interpreter release coverage.

## Next exact tasks

1. Start with `git diff --stat` and inspect the four skill/documentation changes
   before a requested commit/push. The base milestone is committed; this session
   has made no commit/push.
2. For actual use, start at `docs/CODEX_WORKFLOW.md`: obtain an explicit private
   data-home path, user-supplied UTF-8 career inventory, and one or two job
   descriptions/URLs. Review selected facts before approval. Never reuse fictional
   fixture facts as a real user's profile.
3. The next bounded development improvement, if needed after the first trial,
   starts at `src/grounded_apply/cli.py::_command_profile_show`: add a claim-ID
   filter for minimal source-context retrieval, preserving validated evidence
   references, with unknown/retired/pending claim tests. It is not implemented
   or required to use this bounded milestone.

## First command

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_briefing tests.test_launcher -v
```
