# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-08-11 15:34 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **Checkpoint commit:** This file ships with the integrated foundation commit;
  run `git log -1 --oneline --decorate` for its immutable ID.
- **Milestone:** Phase 0 — repository and truth layer
- **Status:** First foundation slice complete; Phase 0 continues
- **Expected working tree:** Clean after the checkpoint commit

## Implemented and verified

- Zero-install Python 3.12+ package scaffold with `gapply` wrapper and stable JSON
  success/error envelopes.
- Read-only `doctor`, path diagnostics, and idempotent `profile init` with a true
  no-write `--dry-run`.
- XDG-compatible POSIX defaults plus a dedicated `GROUNDED_APPLY_HOME` override;
  relative, broad, insecure, and Git-worktree runtime paths fail closed.
- User-only directory/config/database permissions (`0700`/`0600`) without
  chmodding ambiguous existing override roots.
- Typed claims, evidence views, scopes, sensitivity, approval, provenance,
  `NeedInfo`, contradictions, memory proposals, and JSON serialization.
- Deterministic claim resolution for approved verified facts, including temporal,
  scope, sensitivity, confirmed-evidence, and contradiction policies.
- Derived-claim provenance can be represented and stored, but resolution rejects
  every derived value until a registered evaluator can recompute it.
- SQLite schema v1 with migration checksums, foreign keys, strict constraints,
  nested transactions, future-version refusal, atomic concurrent initialization,
  and resumable workflow records with idempotency keys.
- `ProfileService` validates claim/evidence creation and reconstructs the truth
  graph; only confirmed `supports` links enter a resolved `ClaimPacket`.
- Repository instructions, roadmap, development guide, ADR, security policy,
  Apache-2.0 license, DCO contribution policy, and private-data ignore rules.

## Verification

All verification used synthetic data and no network access.

```text
./scripts/check
PASS — CLI help + isolated doctor smoke + 50/50 unittest cases

./scripts/gapply --help
PASS

PYTHONPATH=src python3 -m unittest discover -s tests -v
PASS — Ran 50 tests, OK

git diff --cached --check
PASS before checkpoint commit

repository secret/PII pattern scan
PASS — no credentials or real candidate data found
```

The test suite covers path and permission safety, read-only diagnostics, stable
CLI envelopes, fresh/repeated/concurrent migrations, future and unversioned
schema refusal, transaction rollback, idempotent workflow creation, truth-policy
failure modes, unregistered derivation rejection, and profile-service round trips.

## Known limitations and deliberate fail-closed behavior

- No resume/profile import, extraction, review UI, or end-user claim mutation CLI
  exists yet. Do not use this checkpoint with real candidate data.
- Derived claims are unsupported for resolution until an explicit rule registry
  and evaluator recompute outputs from approved inputs.
- Memory approval/reuse, redacted logging, backup, export, deletion, and artifact
  lifecycle services remain planned.
- `SQLiteRepository` methods are adapter internals; validated application
  mutations go through `ProfileService`.
- Native Windows runtime-directory conventions are not implemented; current
  defaults are POSIX/XDG.
- Installed-wheel migration discovery is configured through Hatch force-include
  but remains unverified because Hatchling is unavailable in this environment.
- Argparse usage errors are still plain text even when a later argument is
  `--json`.
- Migration files are documented to forbid transaction-control statements, but
  this restriction is not yet mechanically linted.

## Next exact tasks

1. Start with `./scripts/check`; do not proceed if the 50-test baseline regresses.
2. Add conspicuously synthetic profile fixtures under
   `tests/fixtures/synthetic_profile/` and a typed import-proposal service that
   creates `needs_review` claims with exact source spans—never verified claims.
3. Add a file/stdin-based CLI surface for import and review so private fact text
   does not need to appear in shell arguments; preserve `--dry-run`, JSON output,
   and explicit approval.
4. Add idempotent memory-proposal approval through `ProfileService`, including
   contradiction detection and audit fields.
5. Verify an installed wheel in an isolated environment when Hatchling is
   available, specifically bundled migration discovery and `gapply` entry point.

## First command

```bash
./scripts/check
```

## Key decisions

- Repository state and this file, not prior chat history, control resumption.
- Metadata does not prove a derivation; only a registered evaluator may authorize
  a recomputed derived value.
- Rejected, pending, qualifying, or contradicting evidence never masquerades as
  confirmed support in a generation packet.
- Diagnostics are read-only and apply the same safety validation as mutations.
- The zero-dependency bootstrap remains intentional until adopting the target
  `uv`/Typer/SQLAlchemy/Alembic toolchain is justified and verified.
