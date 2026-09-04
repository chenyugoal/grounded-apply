# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-09-04 11:28 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `7986f68` (`Implement evidence-backed job application workflows`),
  one commit ahead of `origin/codex/phase-0-truth-layer`
- **Milestone:** Phase 0 — versioned restricted-text taxonomy
- **Status:** This narrow milestone is implemented and verified in the working
  tree. Phase 0 continues; the product is not release-ready and must not be used
  with real candidate data.
- **Expected working tree:** Uncommitted changes in `README.md`,
  `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, this file,
  `src/grounded_apply/services/__init__.py`,
  `src/grounded_apply/services/profile.py`,
  `src/grounded_apply/services/profile_import_validation.py`, and
  `tests/test_profile_import.py`.

The tree was clean at HEAD `7986f68` at session start. The previous handoff was
stale: it named HEAD `7b247ee` and uncommitted paths that were not present. The
required first command passed 186 tests before this work began.

## Implemented and verified

- Profile-import content policy 2 derives all restricted-text matchers from one
  immutable taxonomy version 1. Its nine stable categories are work
  authorization/immigration, security clearance, veteran status, disability
  status, criminal/legal attestations, conflicts of interest, demographic
  self-identification, government identifiers, and authentication credentials.
- Each category declares context patterns, label patterns, normalized assignment
  aliases, and any case-sensitive patterns. Import-time validation rejects an
  incomplete, duplicate, malformed, empty-matching, or inconsistently versioned
  taxonomy declaration. The exported registry and canonical taxonomy SHA-256
  are pinned by tests.
- Request-identity schema 3 binds taxonomy version 1 and its canonical SHA-256,
  along with content-policy version 2, into the workflow input and request hash.
  A realistic workflow created under request schema 2/content policy 1 cannot
  replay under the current identity or create records.
- Exact-assignment, context, fixed-point percent-decoding, Unicode/control
  normalization, and bounded cross-component checks now cover all nine
  categories on recursive values, canonical text, selected evidence, and
  persisted import metadata. Rejections happen before storage and do not echo
  the rejected value.
- Answer-only evidence cannot detach from an exact restricted label on the same
  line or the nearest nonblank line within the bounded lookbehind, including
  across blank extraction separators, long same-line spacing, and encoded or
  control-obfuscated labels. Same-line prefixes over the 512-codepoint bound fail
  closed rather than losing their leading context. General restricted content
  on an unrelated unselected line remains outside classification and is not
  stored.
- Adversarial tests cover common positive phrasings, normalized assignment
  aliases, label/question forms, same-line and blank-line extraction shapes,
  and explicit false-positive controls for customs clearance, accessibility,
  research, workflow, analytics, cryptographic-signature, and similar legitimate
  resume language.
- README, development guidance, and roadmap status now describe the exact
  version matrix, compatibility break, implemented taxonomy boundary, and
  remaining semantic-classification limitations.

## Verification

All verification used synthetic `example.com` data and no network access.

```text
./scripts/check
PASS — CLI help + isolated doctor smoke + 196/196 unittest cases

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_profile_import -q
PASS — 98/98 profile-import tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_cli -q
PASS — 43/43 CLI tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_profile_service -q
PASS — 10/10 profile-service tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest discover -s tests -v
PASS — 196/196 tests with warnings treated as errors

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
PASS

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-taxonomy-smoke.mu8Ze3/doctor-home \
  ./scripts/gapply doctor --json
PASS — read-only uninitialized result; the runtime path was not created

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-taxonomy-smoke.mu8Ze3/dry-run-home \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-taxonomy-smoke-1 --dry-run --json
PASS — manifest-2 preview with derived provenance; runtime path was not created

GROUNDED_APPLY_HOME=/private/tmp/grounded-apply-taxonomy-smoke.mu8Ze3/smoke-home \
  ./scripts/gapply profile init --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-taxonomy-smoke-1 --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-taxonomy-smoke-1 --json
GROUNDED_APPLY_HOME=<same> ./scripts/gapply profile review --json
PASS — replay returned identical workflow/claim/evidence IDs; review returned
5 pending, unusable claims with exact evidence spans

git diff --check
PASS
```

The disposable smoke runtime root was removed after verification.

Complete-diff review and changed-path credential/PII scans found no secrets or
real candidate data. Identity-, credential-, and government-ID-like strings are
conspicuously synthetic tests, deny-pattern definitions, or assertions.

## Known limitations and release blockers

- Taxonomy v1 is a deterministic high-confidence lexical boundary, not semantic
  classification. Novel phrasing, homoglyph and entity encodings, mixed-depth or
  broader fragmentation, arbitrary channel reorderings, and non-English content
  remain incompletely classified.
- Unselected raw source is deliberately not globally classified. Same-line
  context is bounded to 256 code points and label lookbehind to 512 code points;
  overlong same-line prefixes fail closed. Raw source remains caller-managed and
  transiently in memory.
- Manifest version 1, request-identity versions 1–2, and content-policy version 1
  have no automatic migration or reclassification. Earlier-policy rows remain
  pending and unusable, but review cannot taxonomy-revalidate them because it is
  not yet bound to their workflow/result identity.
- Review is display-only. There is no approve/edit/reject decision,
  contradiction resolution, revalidation at decision time, or audit decision
  record. Review also does not yet require a surviving workflow/result-manifest
  row or recompute record IDs from that workflow; replay currently owns those
  cross-record checks.
- The source digest is a correlation/consistency identifier, not proof of origin,
  authentication, confidentiality, or encryption. Protected artifact retention,
  redacted logging, backup, export, and deletion policies remain planned.
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

1. `tests/test_profile_service.py`: add an idempotent review-decision service
   that revalidates workflow/result identity, evidence checksum/locator integrity,
   and contradictions; records actor/time/audit fields; and cannot approve
   sensitive or malformed proposals.
2. `tests/test_config.py`: reject multiply linked profile databases without
   modifying either alias, then preserve that invariant across import/review.
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

- The taxonomy is executable policy, so its version and canonical digest are
  part of import request identity. Changing any category rule requires an
  explicit policy/version decision rather than silent replay under old identity.
- Imported documents and proposal manifests remain untrusted data. Taxonomy
  matches cannot authorize persistence, disclosure, or external action.
- Restricted labels are matched separately from general context so bounded
  answer-label checks do not turn into global scans of unrelated source text.
- Legacy provenance and unknown versions fail closed; they are not promoted or
  repaired automatically.
- Phase 0 remains in progress. Passing this milestone does not authorize real
  candidate data or imply release readiness.
