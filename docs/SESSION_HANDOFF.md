# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-09-04 12:42 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `fd3bdcf` (`Implement grounded apply workflow updates`), two commits
  ahead of `origin/codex/phase-0-truth-layer`
- **Milestone:** Phase 0 — audited profile-import review decisions
- **Status:** This bounded service/persistence milestone is implemented and
  verified in the working tree. Phase 0 continues; the product is not
  release-ready and must not be used with real candidate data.
- **Expected working tree:** Uncommitted changes in `README.md`,
  `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, this file,
  `migrations/002_profile_import_review_items.sql`,
  `src/grounded_apply/cli.py`, repository schema/SQLite modules, profile service
  modules, and the CLI/profile-import/profile-service/repository/schema tests.

The tree was clean at HEAD `fd3bdcf` at session start. The previous handoff was
stale: it named HEAD `7986f68` and uncommitted taxonomy work that was already
committed. Its required first command passed 196 tests before this milestone.

## Implemented and verified

- Migration 002 advances the SQLite schema to version 2 and adds one durable
  review association per import workflow/proposal position. It uniquely binds
  the claim, evidence, immutable record SHA-256, terminal decision workflow,
  actor, and decision time. Schema-1 databases upgrade forward without rewriting
  migration 001; existing-only/read-only commands still refuse to migrate.
- Import request identity is schema 4 and its result manifest is schema 3. Each
  ordered result entry includes a record-digest-schema-1 hash over the immutable
  claim/evidence projection. Review associations and workflow results must agree
  exactly; claim/evidence UUIDs remain derived from workflow and proposal index.
- Pending review output includes the import workflow ID, proposal index, and a
  stale-safe review token. Listing starts from durable pending associations, so a
  projection altered out of the pending lifecycle fails closed instead of
  disappearing; output retains import/proposal order. It revalidates the exact
  current workflow input, checkpoint, ordered result, association, source
  artifact, unique forward and reverse support link, record digest,
  locator/checksum/bounds, registered ingress, and current value/content/taxonomy
  policy before displaying content.
- `CreateProfileReviewDecision`, `ProfileReviewDecisionResult`, and
  `ProfileService.decide_review_item` provide an explicit typed service boundary.
  Approval/rejection requires the review token, opaque actor, opaque idempotency
  key, and an aware decision time. One transaction writes the decision workflow,
  review row, claim projection, and evidence projection; a failed final audit
  check rolls everything back.
- Approval produces `verified`/`approved` claim state and `confirmed` evidence
  with the same actor/time. Rejection preserves `withdrawn`/`rejected` history
  without a verification actor. Exact terminal retries return the original
  result even if the caller supplies a different `now`; changed actor, decision,
  key, token, or request identity fails closed.
- Confidential and highly sensitive proposals cannot be approved. An active,
  overlapping, same-subject explicitly contradicted record blocks approval.
  Public blockers return a structured `Contradiction`; non-public blockers use a
  generic error so their value, canonical text, and provenance are not disclosed.
  Decision-time checks do not reject a distinct value solely because it differs;
  existing resolution still returns `Contradiction` when multiple approved value
  groups compete for one intent.
- Import replay accepts a valid pending or terminal projection and validates the
  terminal decision audit. `resolve` revalidates every durable imported
  association and terminal audit, including when mutable source-type fields have
  been laundered, before an imported claim can enter a claim packet. Unassociated
  legacy pending imports remain ineligible without blocking an unrelated manual
  resolution; unassociated usable imported states fail closed.
- Decision workflow records contain only version identifiers, IDs, hashes,
  decision, actor, and time. Tests prove raw source, selected evidence, proposed
  value/canonical text, filenames/paths, and raw idempotency keys do not enter the
  audit.
- Signed-zero confidence is canonicalized before record hashing, preventing
  SQLite's `-0.0` to `0.0` normalization from invalidating a new review item.
  Digest-only artifact timestamps must be valid, canonical, and internally equal.
- The CLI review command remains physically read-only and has no decision flag or
  command. Its warning accurately limits unverified/unusable language to pending
  facts, and an import replay reports current `review_required` plus the actual
  pending count rather than hard-coded pending state.
- README, development guidance, and roadmap status describe the schema/version
  break, implemented service boundary, remaining CLI/edit/semantic-conflict gap,
  and record-hash security boundary.

## Verification

All verification used synthetic `example.com` data and no network access.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_schema tests.test_repository \
  tests.test_profile_import tests.test_profile_service tests.test_cli -v
PASS — 186/186 focused persistence/import/service/CLI tests during integration

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_profile_service -q
PASS — 30/30 profile-service tests after final adversarial regressions

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_cli -q
PASS — 44/44 CLI tests, including mixed-state warning and proposal-order checks

./scripts/check
PASS — CLI help + isolated doctor smoke + 223/223 unittest cases

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest discover -s tests -q
PASS — 223/223 tests with warnings treated as errors

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
PASS — each command exited successfully

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-review-decision-final.Anejp9/doctor-home \
  ./scripts/gapply doctor --json
PASS — reported healthy Python/runtime checks and an uninitialized database
without creating profile storage

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-review-decision-final.Anejp9/smoke-home \
  ./scripts/gapply profile init --json
GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-review-decision-final.Anejp9/smoke-home \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-review-decision-smoke-1 --json
GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-review-decision-final.Anejp9/smoke-home \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-review-decision-smoke-1 --json
GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-review-decision-final.Anejp9/smoke-home \
  ./scripts/gapply profile review --json
PASS — initialized schema 2; import/replay returned the same workflow and
ordered claim/evidence IDs; review remained read-only and returned proposal
indexes 0–4 with five 64-character tokens bound to that workflow. The disposable
runtime root was removed after verification.

git diff --check
PASS — no whitespace errors

git status --short --branch
PASS — only the expected milestone paths are modified; migration 002 is the sole
untracked file and must be included when the milestone is committed
```

Complete-diff review and changed-path secret/PII/debug scans found no unrelated
changes, real candidate data, credentials, private keys, or debug artifacts.
Identity-, credential-, government-ID-, and private-path-like strings in tests
are conspicuously synthetic. Ignored generated paths are normal Python bytecode
caches only.

## Known limitations and release blockers

- `gapply profile review` is display-only. The typed service can decide an item,
  but no CLI/API decision surface, explicit interactive confirmation, edit flow,
  or user-facing contradiction-resolution workflow exists.
- Decision-time conflict handling is deliberately narrow. It blocks a
  pre-existing explicit same-subject contradicted state but does not preemptively
  enforce type cardinality from unequal values. Existing resolution can still
  return `Contradiction` for competing approved value groups; stable entity
  identity and user-facing per-claim conflict rules remain planned.
- Migration 002 does not invent review associations or record digests for legacy
  imports. Manifest 1, request-identity 1–3, result-manifest 1–2, and earlier
  policy rows remain unusable and fail review/decision closed. Re-import with a
  fresh opaque key in disposable synthetic state; there is no automatic
  reclassification.
- Record SHA-256 values and review tokens detect inconsistent mutation and stale
  review state. They are unkeyed and do not authenticate the database against an
  attacker who can rewrite all records and recompute every related hash. A
  coherent rewrite of all three source-artifact timestamps is likewise outside
  this consistency boundary; those timestamps do not authorize claim use.
- Restricted taxonomy 1 remains deterministic lexical defense in depth, not
  complete semantic, secret, or PII classification. Novel phrasing, homoglyph or
  entity encodings, broader fragmentation/reordering, and non-English content are
  incompletely classified.
- Unselected raw source is transient and deliberately not globally classified.
  The source digest proves consistency, not origin, authentication,
  confidentiality, or encryption. Redacted logging, backup, export, deletion,
  and protected-artifact retention remain planned.
- Multiply linked SQLite database rejection, installed-wheel migration discovery,
  and native Windows secure file capture remain unverified or unimplemented.
- Derived claims remain unusable until a registered evaluator recomputes them.

## Next exact tasks

1. `tests/test_config.py`: reject a multiply linked profile database without
   modifying either hard-link alias, then preserve that invariant across profile
   init/import/review. Start with
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error -m unittest tests.test_config tests.test_cli -v`.
2. `tests/test_cli.py`: design a bounded explicit review-decision CLI contract
   around the existing typed service, including actor/key/token inputs, preview
   and confirmation semantics, JSON output, sensitive blockers, and no accidental
   terminal submission behavior. Do not add edit or contradiction-resolution
   semantics implicitly.
3. `tests/test_logging.py`: prove source text, values, credentials, restricted
   answers, and private paths do not enter normal logs, backups, exports, or
   deletion metadata as those lifecycle surfaces are introduced.
4. `pyproject.toml`: verify an installed wheel in an isolated environment when
   Hatchling is available, including bundled migration discovery and the
   `gapply` entry point.

## First command

```bash
./scripts/check
```

## Key decisions

- The review association is durable relational state; claim/evidence lifecycle
  fields are a guarded projection, not the sole audit trail.
- A decision token binds the immutable record and exact current import
  workflow/result/association/link identity. Lifecycle fields are excluded from
  the record digest so an authorized decision can change them without changing
  the reviewed fact.
- Review and resolution branch on the durable association as well as mutable
  `source_type`, preventing provenance laundering from bypassing import checks.
- Approval re-runs current closed value/content policy and fails closed on any
  unknown version or malformed provenance. Legacy rows are not repaired or
  silently promoted.
- No registered imported claim type currently has a single-value cardinality
  enforced at decision time. The existing resolver still detects competing
  approved value groups; a future typed registry must define stable subject,
  cardinality, and time rules for earlier, type-specific prevention.
- Phase 0 remains in progress. Passing this milestone does not authorize real
  candidate data or imply release readiness.
