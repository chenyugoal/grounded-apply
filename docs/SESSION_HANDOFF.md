# Session handoff

This is the single live checkpoint for resuming Grounded Apply development.
Durable phase scope belongs in `ROADMAP.md`; architecture decisions belong in
`docs/adr/`.

## Checkpoint

- **Last updated:** 2026-09-04 18:12 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `8cbd276` (`Implement grounded application workflow`), equal to
  `origin/codex/phase-0-truth-layer`
- **Milestone:** Phase 0 — diagnostic events, process-crash recovery evidence,
  adapter-owned safe SQLite opens, and installed-package verification
- **Status:** This milestone is implemented and verified in the working tree.
  Phase 0 remains in progress and the overall release
  request remains incomplete. Do not use real candidate data.
- **Starting state:** Clean tree. The previous checkpoint named `71662ef` and
  described uncommitted work already landed in `8cbd276`. Its exact first command,
  `./scripts/check`, passed 261/261 tests before changes.
- **Expected working tree:** Modified `AGENTS.md`, `README.md`,
  `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, this file, `scripts/check`,
  `src/grounded_apply/cli.py`, `src/grounded_apply/config.py`,
  `src/grounded_apply/repositories/sqlite.py`, `tests/test_repository.py`, and
  `tests/test_schema.py`. New `docs/adr/0002-local-storage-and-diagnostics.md`,
  `requirements-build.txt`, `scripts/check_package.py`,
  `src/grounded_apply/diagnostics.py`, `tests/test_crash_recovery.py`,
  `tests/test_logging.py`, and `tests/test_storage_safety.py`. No commit or push
  was made in this session.

## Implemented in this milestone

- Opt-in `gapply --log-events COMMAND --json` uses fixed-schema JSONL stderr.
  It accepts only exact registered command/outcome enums and internally generated
  invocation metadata; no caller text, metadata, path, exception, token, or key
  fields exist. Stdout remains private. Human stderr is discarded in event mode;
  JSON stdout retains command errors and warnings.
- Ambiguous confirmed-decision outcomes preserve a fixed retry instruction in
  `decision_outcome_unknown`. Failed event writes/flushes cannot fail or repeat
  commands. Events create no files or telemetry and do not replace workflow audit.
- An abrupt subprocess exit creates a genuine hot rollback journal with dirty
  spilled database pages. Tests prove mutating import restores original data
  before proceeding once, exact retry is stable, and read-only or unsafe-journal
  cases refuse without recovery or mutation.
- Public SQLite repository and schema-inspection opens own file/sidecar safety:
  private parent outside Git, direct single-link regular database/sidecar files,
  private sidecars, and no orphans. Read-only opens also refuse all sidecars and
  persistent WAL. The CLI retains full runtime-layout and portable-root checks.
- Initialization prepares a private file before SQLite can create a journal,
  using exclusive no-follow creation or checked no-follow permission repair.
  Existing-only/read-only opens never create or chmod storage. URI and implicit
  temporary targets are refused; explicit in-memory repositories remain allowed.
  Open/setup and context-manager initialization failures close connections.
  Parent-symlink plus `..` paths retain filesystem meaning instead of being
  silently normalized to a different database.
- `scripts/check_package.py` builds a source archive and wheel, checks package
  contents, installs into a fresh virtualenv outside the checkout without network
  or source import paths, verifies bundled migration discovery and the entry
  point, then exercises synthetic import/review/approval/replay. Optional build
  tools are pinned in `requirements-build.txt`; runtime remains dependency-free.
- The full gate now treats warnings as errors and exercises the event option.
  README, development guidance, roadmap, and ADR 0002 document the bounded
  guarantees and distinguish them from real-data release readiness.

## Verification

All candidate inputs are synthetic. Runtime databases, crash fixtures, build
outputs, and installed-package environments are outside the repository.

```text
./scripts/check (session start)
PASS — 261/261 baseline tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_config tests.test_cli tests.test_logging -v
PASS — 106/106 at the initial logging/storage checkpoint

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_storage_safety tests.test_logging tests.test_crash_recovery -v
PASS — 20/20 final focused tests, including path meaning and connection cleanup

./scripts/check
PASS — CLI/help/doctor/event smokes and 281/281 tests with warnings as errors

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 \
  -m unittest discover -s tests -v
PASS — 281/281 tests

/private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py
PASS — source archive, wheel, isolated install, entry point, migrations, and
synthetic workflow on Python 3.13.1, repeated after final code changes

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
./scripts/gapply profile decide --help
PASS — all exited 0

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-release-final-doctor-20260904 \
  ./scripts/gapply doctor --json
PASS — healthy, uninitialized; verified that the runtime root was not created

sh -n scripts/check scripts/gapply
PASS

git diff --check
git status --short --branch
PASS — no whitespace errors; exactly the 18 expected changed/new files
```

Complete-diff review plus changed-content credential, personal absolute-path,
email, and debug-hook scans found no secrets, personal data, debug hooks, or
unrelated files. Build outputs and runtime fixtures stayed outside the checkout.

Initial build-tool installation failed because the sandbox could not resolve the
package index. The authorized network-enabled retry installed only build tools
into a disposable `/private/tmp` virtualenv and succeeded. Package verification
itself downloads nothing. Earlier focused tests exposed three old fixtures that
allowed broad database modes; they now prove strict refusal or use private
fixture modes, preserving original schema/no-mutation assertions.

## Known limitations and release blockers

- Encrypted backup/export, restore, deletion, and protected-artifact retention
  remain planned. This is the next Phase 0 slice; a raw database copy is not a
  completed encrypted backup workflow.
- Resume extraction, claim editing, stable entity identity, and user-facing
  semantic contradiction resolution remain incomplete. Restricted taxonomy 1
  is lexical defense in depth, not comprehensive semantic, secret, or PII
  classification; broad obfuscation and non-English coverage remain incomplete.
- Derived claims remain unusable until a registered evaluator recomputes them.
- The broader synthetic golden corpus and application MVP remain planned:
  job snapshots/requirements, fit matrices, traceable resume/PDF generation,
  questionnaire answers, and application/submission history.
- Filesystem guards are repeated samples, not a same-UID adversarial lock. They
  do not authenticate database contents. Native Windows paths remain unsupported
  and the package gate has run only on local Python 3.13.1.
- Real process-crash rollback is covered; power loss, disk corruption, arbitrary
  journal modes, and an explicit repair command are not. Read-only access refuses
  recovery-capable states.
- Diagnostic events cover the CLI's explicit boundary, not arbitrary third-party
  logging, shell history, terminal capture, or caller-controlled sinks. Keep
  private stdout separate. No persistent log retention is implemented.

## Next exact tasks

1. `GROUNDED_APPLY_DESIGN.md:1120`: design the bounded encrypted-backup/restore
   contract before introducing its service or CLI. Start with
   `rg -n "backup|export|delet|retention|encrypt" GROUNDED_APPLY_DESIGN.md docs src`.
   Specify consistent snapshot capture, an authenticated versioned archive,
   transient secret input, private exclusive output, restore compatibility and
   confirmation, and failure/idempotency behavior. Record the encryption/provider
   choice in an ADR; do not invent cryptography or silently ship plaintext backup.
2. `tests/test_profile_service.py`: define one stale-safe claim correction or
   semantic-contradiction workflow, preserving approved history and provenance.
   Keep it separate from backup and broader entity modeling.
3. `scripts/check_package.py`: establish a clean-checkout CI interpreter/OS matrix
   after the local package gate; do not claim unexecuted platforms as verified.
4. `docs/ROADMAP.md`: finish remaining Phase 0 exit evidence before advancing to
   the first bounded Phase 1 job-snapshot task. The personal-use release target
   is the MVP acceptance list in design section 28.

## First command

```bash
./scripts/check
```

## Key decisions

- ADR 0002 is the durable storage/diagnostic threat-model decision.
- No schema, import policy, review-decision authority, or external action changed.
- A verified wheel is distribution evidence for the current Phase 0 feature set;
  it does not satisfy the personal-use MVP or authorize real candidate data.
