# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-09-04 16:09 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `71662ef` (`Improve grounded apply workflow and supporting
  documentation`), equal to `origin/codex/phase-0-truth-layer`
- **Milestone:** Phase 0 — explicit CLI review decisions and typed-CLI storage
  alias safety
- **Status:** This bounded milestone is implemented and verified in the working
  tree. Phase 0 remains in progress; the product is not release-ready and must
  not be used with real candidate data.
- **Expected working tree:** Uncommitted changes in `AGENTS.md`, `README.md`,
  `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, this file, `scripts/check`,
  `src/grounded_apply/cli.py`, `src/grounded_apply/config.py`,
  `tests/test_cli.py`, and `tests/test_config.py`. No untracked file is expected.

The tree was clean at HEAD `71662ef` at session start. The previous handoff was
stale: it named HEAD `fd3bdcf`, described already committed service/persistence
work as uncommitted, and required two tasks completed by this milestone. Its
first command passed 223 tests before the new work began.

## Implemented and verified

- `gapply profile decide` is an explicit single-item interface over the existing
  typed review-decision service. It requires a claim ID, stale-safe review token,
  `approve|reject`, opaque actor ID, and opaque idempotency key. Without
  `--confirm`, it performs storage-free request-shape validation and reports that
  no decision or external action occurred. With `--confirm`, it opens existing
  initialized storage and performs exactly one audited atomic transition.
- Confirmed approval and rejection preserve the service's sensitivity,
  contradiction, terminal-history, and exact-idempotent-replay rules. Output is
  allowlisted and omits claim content, evidence text, raw tokens, raw keys, and
  private paths. Public contradictions use a minimized structured response;
  private contradictions remain generic.
- Decision parsing disables option abbreviation. JSON usage errors are stable
  and non-disclosing even when invalid input follows `--decision` or resembles a
  confirmation option. Human errors and every preview, success, contradiction,
  and interruption path state that no external action was taken.
- A successful database commit followed by close, serialization, output, or
  interruption failure is reported as an ambiguous post-commit outcome. It does
  not attempt a second JSON document after partial stdout and directs the caller
  to retry the exact same confirmed request with the same idempotency key.
- At the typed CLI boundary, an existing SQLite database must be a direct regular
  non-symlink file with exactly one hard link. Recognized `-journal`, `-wal`, and
  `-shm` files must also be direct, private, single-link regular files; orphan or
  unsafe sidecars fail closed without mutation. Guards are repeated immediately
  around SQLite connection/inspection and compare file identity.
- Read-only review and doctor inspection refuse every present recognized sidecar
  and inspect the raw SQLite header before SQLite access to reject persistent WAL
  mode. Tests cover active and cleanly closed WAL databases without creating,
  deleting, or changing database or sidecar state.
- `profile init` validates any existing default config as a private direct
  single-link regular file. A missing config is created through an exclusive,
  no-follow descriptor and verified before initialization. Dangling symlinks,
  hard links, directories, and group/other-readable config files fail unchanged,
  including during dry run.
- `profile import --dry-run` accurately reports its boundary: runtime containment
  plus database/sidecar type, link, and orphan safety are checked, while database
  permissions, schema, artifacts, and prior idempotency-key use are not. Doctor
  reports `initialized: null` when unsafe state prevents a trustworthy answer.
- README, development guidance, roadmap status, zero-install command lists, and
  the full-check smoke matrix now describe the implemented decision command and
  the bounded filesystem guarantees.

## Verification

All verification used synthetic `example.com` data, local temporary paths, and
no network access.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_config tests.test_cli -v
PASS — 99/99 focused configuration and CLI tests

./scripts/check
PASS — root/decision help, isolated doctor smoke, and 261/261 unittest cases

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest discover -s tests -v
PASS — 261/261 tests with warnings treated as errors

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
./scripts/gapply profile decide --help
PASS — each command exited successfully

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-final-doctor-20260904 \
  ./scripts/gapply doctor --json
PASS — reported healthy Python/runtime checks and an uninitialized database;
the disposable runtime root was not created

sh -n scripts/check
PASS — shell syntax is valid

git diff --check
git status --short --branch
PASS — no whitespace errors; exactly the ten expected milestone files are
modified and no untracked file exists
```

Complete-diff review and changed-content credential, email, private-path, and
debug scans found no unrelated changes, secrets, real candidate data, private
absolute paths, or debug hooks. The only email match is the synthetic
`avery.quill@example.com` fixture; changed `print` calls are intentional CLI/test
output paths. Ignored generated files are existing Python bytecode caches only.

## Known limitations and release blockers

- The filesystem rules above are composed at the typed `gapply` boundary.
  Direct callers of `SQLiteRepository` or `inspect_schema` do not automatically
  receive the CLI guards; adapter-wide composition remains future work.
- File checks are repeated samples, not a same-UID adversarial lock. A malicious
  local process able to replace files after the final identity sample remains
  outside this boundary.
- Native Windows secure-path behavior is not implemented or verified.
- Mutating commands permit a safe private single-link rollback journal, but a
  genuine process-crash hot-journal recovery has not been integration tested.
  Read-only review and doctor deliberately refuse all sidecars and persistent
  WAL; no repair/checkpoint command exists.
- The decision command does not add claim editing, bulk decisions, force flags,
  sensitivity overrides, or final application submission. Stable entity
  identity and user-facing semantic contradiction resolution remain planned.
- Restricted taxonomy 1 is deterministic lexical defense in depth, not complete
  semantic, secret, or PII classification. Novel phrasing, encodings, broader
  fragmentation/reordering, and non-English content remain incompletely covered.
- Unselected source text remains transient and is not globally classified.
  Redacted logging, backup, export, deletion, and protected-artifact retention
  are still planned.
- Derived claims remain unusable until a registered evaluator recomputes them.
  Installed-wheel entry-point and bundled-migration discovery are unverified.

## Next exact tasks

1. `tests/test_logging.py`: define a bounded structured logging boundary and
   prove that source text, values, credentials, restricted answers, review
   tokens, idempotency keys, and private paths cannot enter normal logs. Start
   with `rg -n "logging|logger|print\(|stderr|error" src tests` and do not add
   backup/export/deletion behavior implicitly.
2. `tests/test_cli.py`: add a subprocess-crash fixture that creates a genuine hot
   rollback journal, then prove a mutating typed CLI command either recovers it
   safely or fails unchanged before documenting recovery semantics.
3. `src/grounded_apply/repositories/sqlite.py`: design an adapter-owned safe-open
   contract so direct repository/schema callers cannot omit database and sidecar
   guards. Preserve existing-only, read-only, and initialization semantics.
4. `pyproject.toml`: verify an installed wheel in an isolated environment when
   Hatchling is available, including bundled migration discovery and the
   `gapply` entry point.

## First command

```bash
./scripts/check
```

## Key decisions

- Decision preview validates syntax only and remains storage-free. `--confirm`
  is the sole mutation gate; it is not abbreviable, and neither review nor decide
  performs an external action or final submission.
- Ambiguous post-commit reporting never guesses whether the write happened. The
  exact request plus idempotency key is the recovery protocol.
- Database, recognized sidecar, and default-config aliases fail closed because a
  private pathname does not make another hard-link or symlink alias private.
- Read-only inspection refuses recovery-capable SQLite states instead of letting
  SQLite mutate them as a side effect of a nominal read.
- The typed CLI repeats file identity checks around SQLite access, but this is a
  bounded POSIX defense, not authentication against a malicious same-UID writer.
- Phase 0 remains in progress. Passing this milestone does not authorize real
  candidate data or imply release readiness.
