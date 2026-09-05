# Session handoff

This is the single live checkpoint for resuming Grounded Apply development.
Durable phase scope belongs in `ROADMAP.md`; architecture decisions belong in
`docs/adr/`.

## Checkpoint

- **Last updated:** 2026-09-04 19:27 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `b191c16` (`Document storage safety, diagnostics, and package
  verification`), equal to `origin/codex/phase-0-truth-layer`
- **Milestone:** Phase 0 — encrypted profile backup/restore and release CI setup
- **Status:** Encrypted profile backup/restore is implemented and verified in the
  working tree. CI is configured and syntax-checked, but hosted matrix results
  remain unverified. Phase 0 and the personal-use release request are incomplete.
  Continue using synthetic data only.
- **Starting state:** Clean tree. The previous handoff named `8cbd276` and
  described changes already committed in `b191c16`. Its exact first command,
  `./scripts/check`, passed all 281 baseline tests before changes.
- **Working tree:** Modified `AGENTS.md`, `README.md`, `docs/DEVELOPMENT.md`,
  `docs/ROADMAP.md`, this file, `pyproject.toml`, `scripts/check`,
  `scripts/check_package.py`, `src/grounded_apply/cli.py`,
  `src/grounded_apply/config.py`, `src/grounded_apply/diagnostics.py`, and
  `src/grounded_apply/repositories/sqlite.py`. New
  `.github/workflows/check.yml`, `docs/adr/0003-encrypted-profile-backup.md`,
  `requirements-backup.txt`, `scripts/check_backup.py`,
  `src/grounded_apply/services/backup.py`,
  `src/grounded_apply/repositories/backup_crypto.py`,
  `src/grounded_apply/repositories/backup_files.py`,
  `src/grounded_apply/repositories/snapshots.py`, and `tests/test_backup.py`.
  No commit, push, hosted CI run, or release was made in this session.

## Implemented in this milestone

- Optional `backup --encrypt ABSOLUTE_PATH` captures a consistent SQLite image
  using a guarded read-only connection and a pinned read transaction. Plaintext
  stays in memory until an explicitly confirmed restore. Snapshot size is bounded
  to 16 MiB; backup and SQL-validation work have five-second budgets.
- The `backup` extra uses `cryptography` 50.0.1 Fernet with fixed Argon2id
  parameters (64 MiB, 3 iterations, 4 lanes, random 16-byte salt). Format 1
  authenticates its scope and header inside the ciphertext, rejects noncanonical
  tokens and unknown formats, and has no plaintext or age-format fallback.
- Passphrases use a no-echo terminal prompt or explicit bounded UTF-8 stdin;
  no argument, environment, file, configuration, database, or diagnostic field
  stores them. Source databases are never rewritten. Exact named-destination
  retries authenticate and return the original ciphertext when the snapshot is
  unchanged; other existing output fails closed.
- `restore` inspects without writing by default. `--confirm` requires the exact
  archive hash from preview and a new absolute target home. Validation checks
  current schema, exact SQL objects against local migrations, integrity, foreign
  keys, and absence of nondigest artifact references before materializing data.
  Restore never approves claims or overwrites an existing profile.
- Private directories and mode-0600 files are created exclusively. A completion
  receipt is written last. Exact retries require the whole target to remain
  unchanged; partial, changed, linked, or unsafe targets fail without repair or
  deletion. Files and containing directories are fsynced. This is not evidence
  of power-loss durability or protection from a malicious same-UID process.
- CLI and fixed-schema diagnostics preserve content-free recovery advice after
  interruptions or ambiguous lifecycle outcomes. Failures never log candidate
  text, passphrases, paths, or archive contents.
- `scripts/check_backup.py` requires the provider and rejects skips. The optional
  installed-package gate accepts a local dependency wheelhouse, installs the
  extra offline in a fresh environment, and checks backup, preview, confirmed
  restore, replay, and preserved import/review/approval provenance.
- `.github/workflows/check.yml` configures Python 3.12/3.13 on Ubuntu 24.04 and
  macOS 15. Actions are pinned to verified upstream commits, permissions are
  read-only, credentials are not persisted, and all runtime/build data stays in
  runner temporary directories. Hosted execution remains unverified.

## Verification

All runtime data, passphrases, and fault-injection inputs are synthetic. Temporary
databases, archives, logs, build environments, and dependency wheels stayed
outside the repository.

```text
./scripts/check (session start)
PASS — 281/281 baseline tests

PYTHONDONTWRITEBYTECODE=1 \
  /private/tmp/grounded-apply-release-tools-20260904/bin/python -W error \
  scripts/check_backup.py
PASS — 28/28 focused tests with the real encryption provider, no skips

./scripts/check
PASS — CLI/help/doctor/event smokes; 296 passed, 13 optional crypto tests skipped
(309 tests discovered). Base Python 3.13.1 has no cryptography installation.

PATH=/private/tmp/grounded-apply-release-tools-20260904/bin:$PATH ./scripts/check
PASS — 309/309 tests, no skips, warnings treated as errors

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 \
  -m unittest discover -s tests -v
PASS — 296 passed, 13 optional crypto tests skipped (309 discovered)

PYTHONDONTWRITEBYTECODE=1 \
  /private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py
PASS — source archive, wheel, fresh base installation, CLI, bundled migrations,
and synthetic import/review/approval/replay

PYTHONDONTWRITEBYTECODE=1 \
  /private/tmp/grounded-apply-release-tools-20260904/bin/python scripts/check_package.py \
  --backup-wheelhouse /private/tmp/grounded-apply-backup-wheels-20260904
PASS — all base package checks plus offline extra installation and encrypted
backup/restore/provenance/replay on local Python 3.13.1

sh -n scripts/check scripts/gapply
PASS

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
./scripts/gapply profile decide --help
./scripts/gapply backup --help
./scripts/gapply restore --help
GROUNDED_APPLY_HOME=/private/tmp/gapply-final-smoke-20260904 \
  ./scripts/gapply doctor --json
GROUNDED_APPLY_HOME=/private/tmp/gapply-final-smoke-20260904 \
  ./scripts/gapply --log-events doctor --json
PASS — all exited 0; doctor reported uninitialized and created no runtime

ruby -ryaml: YAML.load_file('.github/workflows/check.yml'), validate event keys,
and bash -n on every embedded run script
PASS — YAML structure and embedded shell syntax; not a hosted Actions execution

git diff --check
PASS — no whitespace errors

Changed/new Python AST parsing, whitespace, and private-key/token marker scan
PASS — exactly 21 expected changed/new files; no runtime artifacts or credential
markers; complete code/test/doc/workflow review found no unrelated changes
```

Initial focused tests found three fixture-authoring errors (two repository
method names and a checksum violating the fixture's own SQL constraint); these
were corrected before the passing runs. Final review also fixed a stdin boundary
case where a maximum-size passphrase plus CRLF could hide unread trailing input;
the bounded read now includes an overflow byte and the regression passes.
The sandbox's first dependency install
could not resolve the package index. The authorized network-enabled retry
installed only the optional crypto dependencies into the disposable build
environment; the pinned wheel download also stayed in `/private/tmp`.

## Known limitations and release blockers

- Backup scope is one current-schema profile database. Source documents,
  filesystem artifacts, generated material, browser state, config, caches, and
  logs are excluded. Non-digest artifact rows are refused. Full-vault backup,
  redacted support export, deletion, and retention remain unfinished.
- The passphrase must be retained separately. Python cannot guarantee secret
  zeroization or prevent swap/core-dump capture. Archive length and Fernet
  creation time are visible. Runtime database encryption remains an OS concern.
- Resume extraction, claim editing, stable entity identity, and user-facing
  semantic contradiction handling remain incomplete. Restricted taxonomy 1 is
  lexical defense in depth, not comprehensive semantic/secret/PII classification.
- Derived claims remain unusable until a registered evaluator recomputes them.
- The broader synthetic golden corpus and Phase 1 application MVP remain planned:
  job snapshots/requirements, fit matrices, traceable resume/PDF generation,
  questionnaire answers, and application/submission history.
- CI configuration does not establish Linux or alternate-interpreter support.
  Local evidence is Python 3.13.1 on macOS. Native Windows paths remain unsupported.
- Filesystem guards remain repeated samples, not authentication or an atomic
  same-UID lock. Crash interruption may leave private incomplete restore output;
  retry refuses it. Deletion/repair and power-loss testing remain separate work.

## Next exact tasks

1. `GROUNDED_APPLY_DESIGN.md:1120`: specify the next bounded deletion/retention
   contract in an ADR. Start with
   `rg -n "delet|retention|backup" GROUNDED_APPLY_DESIGN.md docs src`.
   Bind an explicit portable-home target to a stale-safe inventory preview and
   confirmation; define refusal for links, unknown files, changed state, and
   partial failures, plus a content-free auditable result outside deleted data.
   Do not promise secure erasure or introduce recursive deletion before its
   service-owned policy and synthetic adversarial tests exist.
2. `tests/test_profile_service.py`: implement one stale-safe claim correction or
   semantic-contradiction workflow while preserving approved history and import
   replay identities. Keep it separate from deletion and broad entity modeling.
3. `.github/workflows/check.yml`: after integrating this revision, execute the
   hosted matrix and record exact run/commit evidence. Investigate failed targets
   before calling them supported; static YAML validation is insufficient.
4. `docs/ROADMAP.md`: finish remaining Phase 0 evidence before the first bounded
   Phase 1 job-snapshot task. The personal-use release target remains design
   section 28; packaging and encrypted backup alone do not satisfy it.

## First command

```bash
./scripts/check
```

## Key decisions

- ADR 0003 records the bounded archive/provider/restore contract; ADR 0002's
  storage and diagnostic threat-model limits continue to apply.
- Base runtime dependencies remain empty; cryptography is optional and tested
  separately. A base-suite skip never counts as encryption evidence.
- No schema migration, import policy, claim authority, model call, external
  application action, or submission behavior changed.
