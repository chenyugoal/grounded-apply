# Grounded Apply

**A local-first, evidence-backed job-search assistant for Codex.**

Grounded Apply is designed to turn a candidate's approved career history into
traceable job assessments, application materials, questionnaire answers, and an
application record. Its central rule is simple:

> The system may transform, select, summarize, and reorganize verified
> information, but it may not invent candidate facts.

## Current status

Grounded Apply is in **Phase 0: repository and truth layer**. It is an early
development scaffold, not an end-to-end job application tool. Do not use it with
real candidate data until the relevant privacy, persistence, export, and deletion
controls are implemented and verified.

The current scaffold can validate and persist strictly structured text-import
proposals, display their pending claims and exact selected evidence through a
read-only CLI review command, and apply an explicit approve/reject decision
through the typed application service. It does not extract claims from a resume,
offer a CLI decision command, edit proposals, or resolve contradictions for the
user.

The single live checkpoint is [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md).
It records what is actually implemented, the last verification results, known
issues, and the next exact task. The longer-term scope in
[`GROUNDED_APPLY_DESIGN.md`](GROUNDED_APPLY_DESIGN.md) and
[`docs/ROADMAP.md`](docs/ROADMAP.md) is planned unless the checkpoint says it is
implemented and verified.

## Try the Phase 0 scaffold

Prerequisite: Python 3.12 or newer. The current bootstrap deliberately has no
runtime package dependencies and does not require installation.

```bash
./scripts/check
./scripts/gapply --help
./scripts/gapply doctor --json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`./scripts/check` is the canonical full gate; the other commands are useful
individual checks. `uv`, Typer, SQLAlchemy, Alembic,
Ruff, and Pyright are planned Phase 0 hardening work; their commands are not yet
supported project checks. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) before
changing the toolchain.

On the current POSIX bootstrap, runtime data defaults to XDG-compatible user
directories outside the repository. Native Windows path support remains
planned. To isolate development data, point the application at a dedicated,
private scratch directory:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-dev ./scripts/gapply profile init --dry-run --json
```

Never point `GROUNDED_APPLY_HOME` at the repository or commit anything created
under a real runtime-data directory. An existing override root must already be
private (`0700`); broad directories such as `/`, the user home, and the shared
temporary directory are rejected.

### Synthetic structured-import demo

The checked-in proposal manifest and resume are conspicuously fictional. Use only
these synthetic fixtures while Phase 0's real-data safeguards remain incomplete:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile init --json

GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-readme-import-1 \
  --dry-run --json

GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile import \
  --source-file tests/fixtures/synthetic_profile/resume.txt \
  --proposals-file tests/fixtures/synthetic_profile/import_proposals.json \
  --idempotency-key synthetic-readme-import-1 --json

GROUNDED_APPLY_HOME=/tmp/grounded-apply-synthetic-demo \
  ./scripts/gapply profile review --json
```

`--source-file` and `--proposals-file` accept a regular UTF-8 file or `-` for
stdin, but only one input may use stdin. Candidate text and proposed values have
no inline command-line option. Dry-run performs persistence-independent request
validation and reports `storage_checked: false`; it does not create or open
profile storage. A real import requires an earlier `profile init`, remains
idempotent under its opaque key, and creates only pending, unusable records.
The strict proposal manifest is schema version 2. It carries the SHA-256 of the
exact source bytes as a consistency assertion, plus span conventions and
proposals; it cannot supply a source path, artifact ID, extractor identity, or
trust state. The application recomputes the digest, derives a path-free
`sha256:<digest>` source reference and deterministic digest-only artifact, and
assigns the registered manifest-ingress identifier. A matching digest proves
that the two inputs belong together; it does not authenticate who authored the
source or proposals.

Every allowed claim type has a closed value-schema-version-1 shape. Before any
storage transaction, the service snapshots all nested request state and
validates the whole batch under content policy 2. All restricted-text matchers
are derived from one immutable taxonomy version 1 covering work authorization
and immigration, security clearance, veteran and disability status,
criminal/legal attestations, conflicts of interest, demographic
self-identification, government identifiers, and authentication credentials.
The taxonomy uses deterministic high-confidence context, exact-assignment, and
label rules, plus fixed-point percent decoding and bounded fragmentation checks.
Its version and canonical SHA-256 are recorded in request-identity schema 4.
These checks are defense in depth, not complete semantic, secret, or PII
classification. Manifest, request, value, content-policy, restricted-taxonomy,
locator, result-manifest, source-identity, record-ID, and record-digest versions
are bound into the workflow audit identity. Claim and evidence IDs are
deterministically bound to the creating workflow and proposal position. Each
immutable claim/evidence projection has a stable digest in result-manifest schema
3 and a durable schema-2 review association. An idempotent retry revalidates the
checkpoint, source artifact, ordered result records, association, links, and
stored content before returning them.

Raw source text is used in memory for digest, span, context, and whole-source
checks but is not persisted or globally sensitive-pattern scanned. From the
source body, only exact selected evidence spans are stored. The digest-only
artifact retains the digest
and byte/code-point sizes, never an input path, original filename, or source
body. Terminal symlinks and files that change between inspection and descriptor
read are rejected. To prevent an answer-only span from hiding its label,
validation also checks a bounded same-line neighborhood and the nearest nonblank
recognized label-only line within a 512-code-point lookbehind, including across
blank extraction separators. Unrelated unselected source lines remain outside
classification. A selected span with more than 512 unbroken same-line prefix
code points fails closed because its full bounded label context is unavailable.
Import and review fail unchanged if the private data directory or database gains
group/other access, if the database target escapes its data directory, or if a
portable runtime child escapes `GROUNDED_APPLY_HOME`.

`profile review` is physically read-only. Its output contains untrusted candidate
content for inspection, clearly marks every item unusable, and exposes the
creating workflow ID, proposal index, and a stale-safe review token; it cannot
record an approval or rejection. Before display it revalidates the exact current
import workflow/result identity, path-free source artifact, registered ingress,
unique one-to-one evidence link, immutable record digest, locator bounds,
selected-text checksum, and current content policy. Generic claim/evidence
mutation APIs reject `imported_resume`; only the import workflow can create that
provenance.

The typed `ProfileService.decide_review_item` boundary can approve or reject one
imported item atomically. It requires the review token, an opaque actor, and an
idempotency key; stores only IDs, hashes, decision, actor, and time in its audit;
and revalidates provenance and content before changing trust state. Approval
transitions the claim/evidence pair to verified/approved and confirmed. Rejection
preserves it as withdrawn/rejected history. Confidential and highly sensitive
imports cannot be approved through this boundary. An active same-subject
explicitly contradicted record blocks approval, but the service does not
preemptively reject a distinct value solely because it differs. Existing claim
resolution still returns `Contradiction` when multiple approved value groups
compete for one intent. Approved imported claims are revalidated again before
they may enter a resolved claim packet. The unkeyed record hashes provide
consistency and stale-review detection; they are not authentication against an
attacker who can rewrite the database and recompute every related hash.

Manifest version 1, request-identity versions 1–3, result-manifest versions 1–2,
content-policy version 1, and the former public `CreateImportProposal` shape are
intentionally unsupported. Current imports use manifest 2, request identity 4,
result manifest 3, record digest 1, content policy 2, restricted taxonomy 1, and
database schema 2. Migration 002 upgrades the schema but deliberately does not
invent review associations or record digests for earlier imports. Regenerate
manifest-v1 input; for an earlier manifest-v2 workflow, use a fresh opaque
idempotency key in disposable synthetic state. There is no automatic policy
migration or reclassification. Legacy or earlier-policy rows remain unusable and
fail review/decision closed rather than being promoted or repaired.

## Product shape

The accepted architecture is a local-first Python modular monolith with:

- a typed `gapply` CLI as the primary machine contract;
- a relational, provenance-aware candidate evidence store;
- deterministic services for truth, permissions, state transitions, validation,
  and persistence;
- language models only at language-heavy edges, behind structured adapters;
- SQLite by default and private artifacts outside the public repository;
- optional local web, model, job-source, document, and browser adapters later.

The intended workflow is:

```text
approved evidence -> claims -> job requirements -> claim packet
                  -> verified prose -> rendered artifact -> human review
```

Job discovery, resume/PDF generation, application tracking, and browser-assisted
safe-fill are roadmap work. Browser automation will stop at authentication,
CAPTCHA, sensitive or legal questions, ambiguous fields, signatures, and final
submission.

## Trust and privacy contract

- Every factual output must link to approved claim IDs or an approved,
  deterministic derivation.
- Missing, stale, contradictory, sensitive, or insufficient evidence becomes
  `NeedInfo` or `Contradiction`, never a guess.
- Imported pages and documents are untrusted data, not instructions.
- Permission to use an answer once is separate from permission to store or reuse
  it.
- Sensitive eligibility, legal, identity, and demographic answers are never
  inferred.
- Real candidate data, raw private artifacts, credentials, and unredacted logs
  never belong in this repository. Tests and demos use synthetic data only.
- The default product prepares, explains, validates, and pauses. A human performs
  the final application submission.

Repository agents and contributors must follow the complete rules in
[`AGENTS.md`](AGENTS.md).

## Documentation

- [`GROUNDED_APPLY_DESIGN.md`](GROUNDED_APPLY_DESIGN.md) — full product design
- [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md) — live resume point
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — delivery phases and acceptance gates
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — local workflow and handoff protocol
- [`docs/adr/0001-local-first-modular-monolith.md`](docs/adr/0001-local-first-modular-monolith.md) — accepted architecture

## Contributing during Phase 0

Start by following the resume protocol in `AGENTS.md`. Keep changes small, add
synthetic tests for new behavior, preserve unfamiliar working-tree changes, and
update the live checkpoint at every meaningful stopping point. A feature is not
implemented until its code exists and its documented verification passes.
