# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-09-03 23:42 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `7b247ee` (`Implement grounded application workflow improvements`)
- **Milestone:** Phase 0 — application-owned profile-import provenance
- **Status:** This narrow milestone is implemented and verified in the working
  tree. Phase 0 continues; the product is not release-ready and must not be used
  with real candidate data.
- **Expected working tree:** Uncommitted changes in `README.md`,
  `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, this file,
  `src/grounded_apply/cli.py`, `src/grounded_apply/services/__init__.py`,
  `src/grounded_apply/services/profile.py`,
  `tests/fixtures/synthetic_profile/import_proposals.json`, `tests/test_cli.py`,
  `tests/test_profile_import.py`, and `tests/test_profile_service.py`.

The tree was clean and matched `origin/codex/phase-0-truth-layer` at session
start. The previous handoff was stale: it named HEAD `5ab2fcc` and changes that
were not present. The required first command passed 159 tests before this work
began.

## Implemented and verified

- Profile-import manifest schema 2 requires a source SHA-256 consistency
  assertion. The service recomputes the digest from the exact source text and
  owns the source reference, deterministic artifact ID, registered ingress /
  extractor ID, and their schema versions. Caller-supplied provenance authority
  and unknown manifest fields are rejected.
- Source files are captured through one bounded descriptor. Symlinks and
  non-regular files are rejected, `O_NOFOLLOW` and `O_NONBLOCK` are required,
  and device, inode, type, size, modification time, and change time must remain
  stable through the read. Errors do not disclose private paths. Standard input
  remains supported.
- Imports record a strictly validated digest-only source artifact. It stores no
  source path, original filename, or raw source text. Artifact collisions,
  corruption, and missing workflow-referenced artifacts fail closed rather than
  being repaired silently.
- Workflow audit identity contains only application-owned versions, hashes,
  counts, and digest references. The raw idempotency key is hashed; raw source
  text and private paths are excluded. Claim and evidence IDs are bound to the
  workflow and proposal position, and replay validates their exact derivation.
- Generic claim/evidence APIs cannot create `imported_resume` records. Only the
  validated import workflow can persist that source type, preventing callers
  from bypassing import provenance and pending-review invariants.
- Idempotent replay validates the complete succeeded checkpoint, result
  manifest, record identities, claim/evidence semantics, exact source locator,
  link, and artifact contract. Cross-workflow and cross-position substitution
  fail closed.
- Review-list construction revalidates pending/unverified state, evidence/link
  cardinality, digest artifact, registered ingress, locator bounds, and exact
  span checksum. Legacy or mixed legacy/schema-2 queues fail closed without
  echoing paths or corrupt stored timestamp values.
- Documentation and synthetic adversarial tests were updated for the schema-2
  hard break, digest semantics, private-path non-disclosure, secure capture,
  provenance forgery, replay substitution, and corrupt-record failure modes.

## Verification

All verification used synthetic `example.com` data and no network access.

```text
./scripts/check
PASS — CLI help + isolated doctor smoke + 186/186 unittest cases

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_profile_import -q
PASS — 88/88 profile-import tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_cli -q
PASS — 43/43 CLI tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_profile_service -q
PASS — 10/10 profile-service tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest discover -s tests -v
PASS — 186/186 tests with warnings treated as errors

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
PASS

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-release.ctUMI2/doctor-home \
  ./scripts/gapply doctor --json
PASS — read-only uninitialized result; the runtime path was not created

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-release.ctUMI2/dry-run-home \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-release-smoke-1 --dry-run --json
PASS — schema-2 preview with derived provenance; runtime path was not created

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-release.ctUMI2/smoke-home \
  ./scripts/gapply profile init --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-release-smoke-1 --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-release-smoke-1 --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile review --json
PASS — replay returned identical workflow/claim/evidence IDs; review returned
5 pending, unusable claims with exact evidence spans

git diff --check
PASS
```

Complete-diff review and changed-path credential/PII scans found no secrets or
real candidate data. Identity-, credential-, and government-ID-like strings are
conspicuously synthetic test fixtures, deny-pattern definitions, or assertions.

## Known limitations and release blockers

- The source digest is a consistency and correlation identifier, not proof of
  origin, authentication, confidentiality, or encryption.
- Raw source remains caller-managed and exists transiently in memory. The
  application persists only the digest artifact plus validated proposed claim
  values and selected evidence excerpts; protected artifact retention, redacted logging,
  backup, export, and deletion policies remain planned.
- Manifest schema 1 and its request identity have no migration. Legacy import
  provenance is deliberately not trusted automatically, and one legacy row can
  make the current all-or-nothing review queue fail closed. Use fresh schema-2
  synthetic runtime data and idempotency keys during development.
- Version fields currently act as rejection gates. A future version needs an
  explicit dispatcher and migration rather than changing current semantics in
  place.
- Sensitive-content classification remains incomplete. Clearance, veteran
  status, disability, criminal/legal attestations, conflicts, demographics, and
  other restricted categories can still be hidden under an allowed claim type.
  Fragmented labels, mixed encoding depths, homoglyphs, entity encodings, and
  arbitrary channel reorderings are not exhaustively classified.
- Review is display-only. There is no approve/edit/reject decision,
  contradiction resolution, revalidation at decision time, or audit decision
  record. Review also does not yet require a surviving workflow/result-manifest
  row or recompute record IDs from that workflow; the idempotent replay path is
  currently the surface that performs those cross-record checks.
- Secure file capture depends on POSIX `O_NOFOLLOW` and `O_NONBLOCK`; native
  Windows behavior remains unimplemented. Standard input is the portable
  fallback.
- A deliberately pathological but valid 1,000-proposal assignment-heavy preview
  takes about 13 seconds on the current development host. Inputs are bounded,
  but a total delimiter/work budget remains a future hardening opportunity.
- Installed-wheel migration discovery and multiply linked SQLite database
  rejection remain unverified or unimplemented.
- Derived claims remain unusable until a registered evaluator recomputes them.

## Next exact tasks

1. `tests/test_profile_import.py`: replace the remaining ad hoc restricted-text
   screening with a versioned declarative taxonomy; add clearance, veteran,
   disability, criminal/legal, conflict, and demographic cases plus explicit
   false-positive controls.
2. `tests/test_profile_service.py`: add an idempotent review-decision service
   that revalidates evidence checksum/locator integrity, detects contradictions,
   records actor/time/audit fields, and cannot approve sensitive or malformed
   proposals.
3. `tests/test_config.py`: reject multiply linked profile databases without
   modifying either alias, then preserve that invariant across import/review.
4. `tests/test_logging.py`: prove source text, values, credentials, restricted
   answers, and private paths do not enter normal logs, backups, exports, or
   deletion metadata as those lifecycle surfaces are introduced.
5. `pyproject.toml`: verify an installed wheel in an isolated environment when
   Hatchling is available, including bundled migration discovery and the
   `gapply` entry point.

## First command

```bash
./scripts/check
```

## Key decisions

- The import manifest remains untrusted data. Its digest is checked against the
  captured source but does not authenticate who supplied either input.
- Raw source is not persisted. The application owns the digest reference,
  digest-only artifact, ingress identity, locator schema, and record identity.
- `imported_resume` is a reserved source type that only the validated import
  workflow may create.
- Legacy provenance and unknown versions fail closed; they are not promoted or
  repaired automatically.
- Phase 0 remains in progress. Passing this milestone does not authorize real
  candidate data or imply release readiness.
