# Session handoff

This is the single live checkpoint for resuming Grounded Apply development. Keep
it short, factual, and current. Durable phase scope belongs in `ROADMAP.md` and
architecture rationale in ADRs.

## Checkpoint

- **Last updated:** 2026-09-03 17:48 CDT
- **Branch:** `codex/phase-0-truth-layer`
- **HEAD:** `d0726ba` (`Add safe structured profile import proposals`)
- **Milestone:** Phase 0 — strict file/stdin import and read-only pending review
- **Status:** CLI milestone implemented and verified in the working tree; Phase 0
  continues and the product is not ready for real candidate data
- **Expected working tree:** Uncommitted milestone changes in `AGENTS.md`, `README.md`,
  `docs/DEVELOPMENT.md`, `docs/ROADMAP.md`, this file,
  `src/grounded_apply/cli.py`, `src/grounded_apply/config.py`,
  `src/grounded_apply/repositories/sqlite.py`,
  `src/grounded_apply/services/`, `tests/test_cli.py`,
  `tests/test_config.py`, `tests/test_profile_import.py`, and
  `tests/test_repository.py`

The prior checkpoint was stale at session start: it described the structured
import service as uncommitted at `9ae8523`; that work was already committed in a
clean tree as `d0726ba`.

## Implemented and verified

The committed `d0726ba` service milestone remains intact. This working tree adds:

- `gapply profile import --source-file PATH|- --proposals-file PATH|-`
  with `--idempotency-key OPAQUE [--dry-run] [--json]`. Candidate source and proposal
  values have no inline argument, and at most one input may use stdin.
- A strict schema-v1 JSON adapter: exact field sets, duplicate-key and non-finite
  rejection, zero-based/end-exclusive Unicode-codepoint spans, exact span text,
  bounded regular-file/stdin input, valid Unicode, bounded provenance metadata,
  an opaque idempotency key, and a 1,000-proposal ceiling. Trust, approval,
  scope, sensitivity, subject, and other unknown fields are rejected at the CLI
  boundary.
- Persistence-independent `ProfileService.preview_import_proposal`. Dry-run does
  not open SQLite, create runtime paths, consume an idempotency key, or imply
  storage validation; output explicitly reports `storage_checked: false`.
- Persisted CLI import routed through `ProfileService`, retaining the atomic,
  concurrent-safe idempotency and review-only trust state. Import requires an
  already initialized, current schema. It opens the existing database in SQLite
  `mode=rw`, validates without migration on the same connection used to write,
  and cannot create or migrate a missing, empty, or input-time-replaced database.
  Output contains hashes, counts, and opaque IDs, not candidate text.
- `gapply profile review [--json]`, backed by
  `ProfileService.list_review_items`. It shows both canonical and structured
  values plus exact supporting evidence, scope, sensitivity, and provenance;
  every item is marked `content_trust: untrusted` and `usable: false`.
- A SQLite repository read-only/query-only mode. Review validates the current
  schema without migration, chmod, or database-byte/mtime changes; missing and
  empty databases are not created or adopted.
- Non-mutating storage privacy preflight for import and review. The data directory
  and database must retain user-only permissions, database targets must remain
  inside private data and outside Git worktrees, and portable runtime children
  must remain beneath `GROUNDED_APPLY_HOME`. Escapes and permission drift fail
  without repair or content disclosure.
- Terminal-safe human review rendering for control and bidirectional-format
  characters. Imported prompt-like text remains explicitly untrusted data.
- Updated README, development command contract, and roadmap evidence.

## Verification

All verification used synthetic `example.com`/555 data and no network access.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -W error \
  -m unittest tests.test_cli tests.test_profile_import tests.test_repository \
  tests.test_config -v
PASS — 79/79 focused CLI/import/repository/config tests

./scripts/check
PASS — CLI help + isolated doctor smoke + 106/106 unittest cases

./scripts/gapply --help
./scripts/gapply profile import --help
./scripts/gapply profile review --help
PASS

GROUNDED_APPLY_HOME=<fresh-/private/tmp-path>/profile-home \
  ./scripts/gapply doctor --json
PASS — read-only uninitialized result; no runtime data created

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
PASS — Ran 106 tests, OK

GROUNDED_APPLY_HOME=<fresh-/private/tmp-path>/profile-home \
  ./scripts/gapply profile init --json
GROUNDED_APPLY_HOME=<same-path> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-smoke-import-1 --dry-run --json
GROUNDED_APPLY_HOME=<same-path> ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-smoke-import-1 --json
GROUNDED_APPLY_HOME=<same-path> ./scripts/gapply profile review --json
PASS — 5 pending/unusable claims and 5 pending exact evidence spans

git diff --check
PASS
```

The new tests cover file and either-stdin input, CRLF preservation, input size and
regular-file limits, strict JSON/schema/trust-field failures, valid Unicode,
metadata and batch bounds, true no-write dry-run against absent and initialized
homes, same-connection existing-only validation, input-time database replacement,
refusal to initialize storage implicitly, permission drift and database/portable
path escapes, idempotent replay and changed input, minimized errors/output, empty
and populated review queues, complete pending imported evidence, canonical/value
disagreement, terminal control text, and byte/mode/mtime-stable read-only SQLite
access.

Complete-diff review and changed-path credential/PII scans found no secrets or
real candidate data; identity-like strings are explicitly fictional fixture and
assertion values.

## Known limitations and release blockers

- This milestone is a structured proposal ingestion boundary, not a resume
  extractor. There is no PDF/DOCX/LaTeX parser, model adapter, or artifact
  lifecycle service. Do not use it with real candidate data.
- Allowed claim types still accept flexible JSON values. A malicious or mistaken
  extractor could hide sensitive eligibility or secret-shaped data under an
  allowed type. Per-type value schemas and sensitive-field rejection are required
  before real-data import or approval.
- Proposal-supplied `source_ref` and `extraction_method` are syntax-bounded but
  are not bound to a protected artifact or registered extractor identity.
- Review is deliberately display-only. No approve, edit, reject, contradiction
  resolution, evidence revalidation, or audit decision exists. Review lists the
  full pending queue and has no workflow filter or pagination.
- Dry-run validates request semantics only. Because `storage_checked` is false,
  it does not predict idempotent replay/conflict, schema compatibility, or
  artifact foreign-key success.
- Raw source remains caller-managed and in memory; only selected spans and hashes
  persist. Redacted structured logging, backup, export, deletion, and protected
  artifact retention remain planned.
- Native Windows runtime-directory conventions and installed-wheel migration
  discovery remain unverified. Argparse usage failures are still plain text.
- Initialized storage does not yet reject a multiply linked database inode; a
  hard link could alias the same SQLite file outside the validated runtime path.
  Single-link or equivalent file-identity enforcement is required before real
  candidate data.
- Derived claims remain unusable until a registered evaluator recomputes them.

## Next exact tasks

1. Start with `tests/test_profile_import.py`: define a registered value schema for
   every currently allowed import claim type and add adversarial cases for work
   authorization, government identifiers, credentials/tokens, and whole-document
   evidence hidden under an allowed type; fail before any write.
2. Start with `tests/test_cli.py`: replace proposal-authored extractor/source
   identity with an application-owned source digest and registered extractor
   identifier, while retaining private-path-free provenance and replay integrity.
3. Start with `tests/test_profile_service.py`: add an explicit, idempotent review
   decision service that revalidates evidence checksum/locator integrity, detects
   contradictions, records actor/time/audit fields, and cannot approve sensitive
   or malformed proposals.
4. Start with `tests/test_config.py`: reject multiply linked profile databases
   without modifying either alias, then preserve that invariant across import and
   review.
5. Start with a new redacted-logging test module: prove source text, proposed
   values, credentials, sensitive answers, and private paths never enter normal
   logs or public error metadata.
6. Verify an installed wheel in an isolated environment when Hatchling is
   available, specifically bundled migration discovery and the `gapply` entry
   point.

## First command

```bash
./scripts/check
```

## Key decisions

- CLI import accepts separate explicitly authorized source/proposal files or one
  stdin stream; imported content cannot add authority fields or authorize reads.
- Dry-run is a persistence-independent request validation, so its output uses
  planned counts and truthfully reports that storage was not checked.
- Normal import and review require a current schema created by explicit
  `profile init`; import validates an existing writable connection without schema
  changes, and data commands do not silently initialize or migrate it.
- Import and review revalidate private permissions and resolved-path containment;
  they never repair unsafe storage as a side effect.
- Review content is intentionally visible private output but remains marked
  untrusted and unusable. The read path is physically read-only, and there is no
  approval side effect.
- The narrow CLI manifest fixes imported claims to conservative service defaults:
  person subject, global scope, at least personal sensitivity, pending approval,
  and pending evidence confirmation.
