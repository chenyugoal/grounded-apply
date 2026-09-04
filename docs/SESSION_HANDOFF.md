# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-09-03 19:45 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `5ab2fcc` (`Implement grounded application workflow improvements`)
- **Milestone:** Phase 0 — versioned profile-import schemas and content boundary
- **Status:** This narrow milestone is implemented and verified in the working
  tree. Phase 0 continues; the product is not release-ready and must not be used
  with real candidate data.
- **Expected working tree:** Uncommitted changes in `README.md`,
  `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, this file,
  `src/grounded_apply/cli.py`, `src/grounded_apply/services/__init__.py`,
  `src/grounded_apply/services/profile.py`, the new
  `src/grounded_apply/services/profile_import_validation.py`,
  `tests/test_cli.py`, and `tests/test_profile_import.py`.

The tree was clean and matched `origin/codex/phase-0-truth-layer` at session
start. The required first command passed 106 tests before this work began.

## Implemented and verified

- A closed schema-version-1 registry now covers all 14 allowed import claim
  types. Scalar strings and structured employment/project values have exact
  shapes, bounded text, valid date/range rules, and an explicit ownership enum;
  there is no generic JSON fallback.
- Import preview and persistence snapshot JSON values, `Scope`, and
  `TextSourceSpan` before validation, hashing, or storage. Value-schema version 1
  and content-policy version 1 participate in request identity and are recorded
  in workflow audit input.
- Service-owned validation runs before the storage transaction over values,
  canonical text, selected evidence, bounded same-line/preceding-label context,
  provenance metadata, and subject/scope identifiers. It rejects recognizable
  work-authorization/immigration answers, government identifiers, credential
  assignments, private keys, and common token formats without echoing content.
- Per-component screening handles control-character and punctuation obfuscation
  and every percent-decode layer through a fixed point. Bounded cross-field
  checks run within a proposal, while an indexed matcher detects recognized
  assignment labels split across two batch components. The indexed path avoids
  factorial or multiplicative full-text scans.
- The source boundary is shared by CLI and service at 16 MiB of UTF-8. Canonical
  text, per-item/batch evidence, line count, proposal count, component, and
  caller-controlled persisted-metadata volume all have service-owned limits.
- Whole-source minimization rejects 80% selected alphanumeric span coverage,
  exact raw/decoded reconstruction in individual value/canonical/evidence/
  metadata channels, aligned-layer exact reconstruction across combined
  value/canonical/evidence content in serialization order and its reverse,
  token-weight coverage, and an adaptive sampled-window comparison.
  Source-multiset projection prevents punctuation or repeated-character padding
  from selecting a misleading comparison size.
- Rejections remain pre-transaction and do not reserve idempotency keys. Imports
  that pass still create only pending, unusable claims and evidence.
- Adversarial tests cover every registered schema, exact boundaries, mutation
  snapshots, no-write failures, work authorization, government IDs,
  credentials/tokens, encoded and fragmented content, whole-source splitting,
  padding, metadata replication, false-positive controls, and worst-shape
  assignment indexing.

## Verification

All verification used synthetic `example.com` data and no network access.

```text
./scripts/check
PASS — CLI help + isolated doctor smoke + 159/159 unittest cases

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_profile_import -q
PASS — 73/73 profile-import tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_cli -q
PASS — 33/33 CLI tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest discover -s tests -v
PASS — 159/159 tests with warnings treated as errors

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
PASS

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-release.BS77J0/doctor-home \
  ./scripts/gapply doctor --json
PASS — read-only uninitialized result; the runtime path was not created

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-release.BS77J0/smoke-home \
  ./scripts/gapply profile init --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-release-smoke-1 --dry-run --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-release-smoke-1 --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile review --json
PASS — 5 pending/unusable claims and 5 pending exact evidence spans

git diff --check
PASS
```

Complete-diff review and changed-path credential/PII scans found no secrets or
real candidate data. Identity-, credential-, and government-ID-like strings are
conspicuously synthetic test fixtures, deny-pattern definitions, or assertions.

## Known limitations and release blockers

- This milestone is deterministic defense in depth, not complete sensitive-data
  classification. Clearance, veteran status, disability, criminal/legal
  attestations, conflicts, demographics, and other restricted categories can
  still be hidden under an allowed type. They need a versioned declarative
  restricted-content policy and negative controls before real-data use.
- Sampled near-duplicate windows are not proof: adversarial mutation at known
  sample positions can evade them. Components with different percent-encoding
  depths can also evade combined exact reconstruction when no single aligned
  decode layer matches, and arbitrary cross-channel reorderings can evade the
  serialization-order and reverse-order combined checks. Assignment labels split
  across three or more components, homoglyph/entity encodings, and broader label
  layouts are not exhaustively classified.
- Proposal-authored `source_ref` and `extraction_method` are syntax/content
  bounded but are not bound to an application-owned artifact or registered
  extractor identity. This is the next provenance boundary.
- Review is display-only. There is no approve/edit/reject decision,
  contradiction resolution, evidence revalidation, or audit decision record.
- Raw source remains caller-managed and in memory. Unselected content is not
  globally sensitive-pattern scanned or persisted. Redacted logging, backup,
  export, deletion, and protected artifact retention remain planned.
- A deliberately pathological but valid 1,000-proposal assignment-heavy preview
  takes about 13 seconds on the current development host. Inputs are bounded and
  the prior multiplicative scan is removed, but a total delimiter/work budget is
  a future hardening opportunity.
- Existing synthetic workflow idempotency keys created before schema/content
  policy versioning conflict by design because request identity changed.
- Native Windows runtime conventions, installed-wheel migration discovery, and
  multiply linked SQLite database rejection remain unverified or unimplemented.
- Derived claims remain unusable until a registered evaluator recomputes them.

## Next exact tasks

1. Start with `tests/test_cli.py`: replace proposal-authored extractor/source
   identity with an application-owned source digest and registered extractor
   identifier, while retaining private-path-free provenance and replay
   integrity.
2. Start with `tests/test_profile_import.py`: move restricted-content screening
   into a versioned declarative taxonomy and add clearance, veteran, disability,
   criminal/legal, conflict, and demographic cases plus false-positive controls.
3. Start with `tests/test_profile_service.py`: add an explicit idempotent review
   decision service that revalidates evidence checksum/locator integrity,
   detects contradictions, records actor/time/audit fields, and cannot approve
   sensitive or malformed proposals.
4. Start with `tests/test_config.py`: reject multiply linked profile databases
   without modifying either alias, then preserve that invariant across import
   and review.
5. Add redacted-logging and lifecycle tests proving source text, values,
   credentials, sensitive answers, and private paths do not enter normal logs,
   backups, exports, or deletion metadata.
6. Verify an installed wheel in an isolated environment when Hatchling is
   available, specifically bundled migration discovery and the `gapply` entry
   point.

## First command

```bash
./scripts/check
```

## Key decisions

- The import manifest remains untrusted data. It can select only registered
  claim types and cannot grant approval, verification, evidence confirmation,
  public sensitivity, or external-action authority.
- Content validation is explicitly high-confidence and fail-closed for its named
  categories, while documented as non-exhaustive outside them.
- Raw source is used only for digest, exact spans, bounded selected context, and
  whole-source minimization; it is not stored or globally classified.
- Whole-source exact checks evaluate raw and every aligned percent-decode layer;
  broader token/window heuristics apply per channel, while combined content gets
  serialization-order and reverse-order exact checks to avoid ordinary-content
  false positives.
- The service snapshots nested caller-owned objects once so validation,
  idempotency hashing, and persistence operate on the same state.
- Phase 0 remains in progress. Passing this milestone does not authorize real
  candidate data or imply release readiness.
