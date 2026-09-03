# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-09-03 17:00 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `9ae8523` (`build phase 0 truth layer foundation`)
- **Milestone:** Phase 0 — safe structured text-import proposals
- **Status:** Service milestone implemented and verified in the working tree;
  Phase 0 continues
- **Expected working tree:** Uncommitted milestone changes in
  `src/grounded_apply/services/`, `tests/fixtures/synthetic_profile/`,
  `tests/test_profile_import.py`, `docs/ROADMAP.md`, and this file

## Implemented and verified

The committed `9ae8523` foundation still provides the zero-install CLI, private
runtime paths, typed truth policy, schema v1, migration checks, repository, and
validated profile service described in the prior checkpoint. This working tree
adds:

- Typed import inputs for atomic claim proposals and exact 0-based, half-open
  Unicode-codepoint spans. A supplied quote must equal its source slice.
- `ProfileService.create_import_proposal`, which writes only `needs_review`,
  approval-pending claims and confirmation-pending supporting evidence. The
  request exposes no trust-state or verification controls.
- SHA-256 anchoring for full source text and each selected evidence span. The
  service persists selected spans, not the full imported text.
- A default-deny import claim-type policy. Sensitive eligibility, sponsorship,
  clearance, legal, disability, veteran/demographic, identity, signature, and
  unknown claim types are rejected without writes. Imported claims cannot be
  classified less restrictively than `personal` before review.
- Atomic and concurrent-safe idempotency. Stored idempotency keys are opaque
  hashes, workflow metadata excludes imported text and proposed values, changed
  input fails closed, and replay manifests are integrity checked.
- A conspicuously fictional `example.com`/555-number profile fixture with exact
  metrics, an ownership qualifier, Unicode offsets, and inert prompt-injection
  text.

## Verification

All verification used synthetic data and no network access.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_profile_import -v
PASS — 17/17 focused import tests

./scripts/check
PASS — CLI help + isolated doctor smoke + 67/67 unittest cases

./scripts/gapply --help
PASS

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-doctor-codex-20260903-1702 \
  ./scripts/gapply doctor --json
PASS — read-only result; no repository or personal runtime data used

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
PASS — Ran 67 tests, OK

git diff --check
PASS
```

Complete-diff review and changed-path credential/PII scans found no secrets or
real candidate data; only the explicitly fictional `example.com`/555 fixture
matched the identity patterns.

The import suite covers exact Unicode source spans and checksums, pending-only
trust state, unsupported sensitive categories, conservative sensitivity,
prompt-injection inertness, full-source minimization, atomic rollback,
identical/changed/concurrent idempotent calls, replay-manifest corruption,
runtime input validation, JSON serialization, and refusal to resolve proposed
claims into a `ClaimPacket`.

## Known limitations and deliberate fail-closed behavior

- This is an application-service boundary, not an end-user resume importer. No
  file/stdin CLI, PDF/DOCX/text extractor, review display, or approval mutation
  exists yet. Do not use this checkpoint with real candidate data.
- Import claim types are an intentionally narrow allowlist. Expansion requires
  an explicit truth/sensitivity decision and adversarial tests.
- Failed synchronous import batches use atomic restart semantics: their workflow
  row, claims, and evidence all roll back, so no durable failure checkpoint is
  retained. A retry starts the batch from the beginning.
- The caller remains responsible for protected raw-artifact storage. This service
  holds full source text only in memory and stores selected evidence spans plus
  hashes and an optional existing artifact ID.
- Derived claims remain unusable until a registered evaluator recomputes them.
- Memory approval/reuse, redacted logging, backup, export, deletion, and artifact
  lifecycle services remain planned.
- Native Windows runtime-directory conventions and installed-wheel migration
  discovery remain unverified.
- Argparse usage errors are still plain text even when a later argument is
  `--json`; migration transaction-control restrictions are not mechanically
  linted.

## Next exact tasks

1. Start with `./scripts/check`; do not proceed if the 67-test baseline regresses.
2. Start in `tests/test_cli.py`: specify a file/stdin-only `profile import` and
   `profile review` CLI contract with JSON envelopes, true no-write `--dry-run`,
   private-text-free arguments, and no approval without an explicit review
   action; then route it through `ProfileService` rather than repository calls.
3. Add idempotent memory-proposal approval through `ProfileService`, including
   contradiction detection and audit fields.
4. Add redacted structured logging and synthetic tests proving source text,
   proposed values, credentials, and sensitive answers never enter normal logs.
5. Verify an installed wheel in an isolated environment when Hatchling is
   available, specifically bundled migration discovery and the `gapply` entry
   point.

## First command

```bash
./scripts/check
```

## Key decisions

- Imported extraction output is untrusted data and can create only reviewable
  proposals; confidence never upgrades trust.
- Text span offsets are versioned as zero-based, end-exclusive Unicode codepoint
  indexes, with both full-source and exact-span hashes.
- The import service fails closed on every unregistered claim type and raises
  public sensitivity to `personal`; future allowlist changes require tests.
- Synchronous import proposal creation is one atomic transaction. Idempotent
  replay validates the original semantic request and persisted result manifest.
- Raw source text and proposed values are hashed in memory for idempotency but
  excluded from workflow metadata.
