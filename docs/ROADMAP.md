# Roadmap

This roadmap defines delivery scope and acceptance gates. It is not the live
session log; use [`SESSION_HANDOFF.md`](SESSION_HANDOFF.md) for the current
working-tree checkpoint and next exact task.

## Status rules

- **Implemented** means code or documentation exists and its documented
  verification has passed.
- **In progress** means work has started but is incomplete, unverified, or not
  integrated.
- **Planned** means design intent only.
- **Blocked** names a concrete dependency or unresolved decision.

As of September 4, 2026, **the bounded local application pilot is implemented**
(ADR 0006); broader platform and semantic features remain in progress. The design basis,
resumability contract, executable scaffold, deterministic truth policies, and
initial SQLite persistence slice are implemented and verified. Safe structured
text-import proposals, registered schema-v1 claim values, a versioned
nine-category restricted-text taxonomy for deterministic high-confidence
screening of persisted content and metadata, whole-source minimization guards, a
stable-descriptor file/stdin CLI boundary, application-owned digest provenance,
a registered manifest ingress, replay-bound workflow records,
provenance-validating read-only review, durable record-digest associations,
stale-safe review tokens, a storage-free CLI decision syntax preview, confirmed
atomic audited approve/reject decisions with minimized output and idempotent
recovery, typed-CLI database/sidecar link and orphan guards, read-only
persistent-WAL refusal, resolution-time imported-record revalidation, and the
first synthetic profile fixture, adapter-owned safe SQLite opens, genuine
hot-journal recovery tests, fixed-schema opt-in diagnostic events, and an isolated
installed-package gate, and optional authenticated profile backup/restore with
an isolated installed-extra round trip are implemented. Exact text extraction,
selected onboarding, explicit name/contact vocabulary, audited retirement and
approved replacement, support export, and whole-portable-home deletion now extend
that foundation. The local pilot covers job capture, evidence retrieval, verified
PDFs/answers, explicit material approval, and manual application history. Broader
semantic contradiction handling, novel-obfuscation classification, registered
derivations, automatic retention, and Phases 2–5 remain planned or in progress.

## Phase 0 — Repository and truth layer

**Status: In progress**

Outcome: a dependency-light, testable foundation that can represent candidate
facts and provenance without placing private data in the repository.

| Deliverable | Status | Exit evidence |
|---|---|---|
| Product design and local-first architecture decision | Implemented | Design document and ADR 0001 exist |
| Durable resume and handoff protocol | Implemented | `AGENTS.md`, development guide, and live checkpoint agree |
| Zero-install CLI bootstrap and diagnostics | Implemented | Root and `profile decide` help, stable JSON usage errors, doctor, dry-run, and idempotent private init tests pass |
| Private runtime path resolution and user-only directories | In progress | Typed CLI and public SQLite adapters cover POSIX permissions, direct single-link database/sidecar files, orphan refusal, read-only persistent-WAL refusal, and creation before SQLite access; the CLI adds XDG/portable containment and exclusive no-follow config creation. Native Windows paths remain planned; sampled same-UID TOCTOU is an explicit residual limit |
| Typed atomic claims, evidence, status, scope, and sensitivity | Implemented | Domain invariants and serialization tests pass |
| `NeedInfo`, `Contradiction`, and claim-packet resolution | Implemented | Missing/conflicting/stale/sensitive cases fail closed |
| SQLite schema and migration mechanism | Implemented | Atomic fresh/concurrent init, future refusal, checksum validation, and existing-only no-migration writes pass |
| Repository/service boundaries for validated mutation | In progress | Profile, retirement, job, material, and application mutations use validated services with audited idempotency; broader editing and lifecycle services remain |
| Resume import, extraction proposal, and human review | Implemented for UTF-8 pilot | Exact line extraction and hash-bound selected onboarding retain source spans; explicit name/contact vocabulary 2 retains vocabulary 1 replay. Pending records require per-item audited approval. Retirement/replacement resolves selected corrections without rewriting history. PDF/DOCX input and broader semantic conflict assistance remain planned |
| Content-free diagnostic logging | Implemented | Opt-in fixed-schema JSONL excludes caller content; failure, interruption, ambiguous-commit recovery, and broken-sink tests pass; no file logs or telemetry |
| Backup, export, deletion, and retention | Implemented for bounded pilot; broader lifecycle planned | Encrypted complete-database backup/restore includes job/material/application state. Fixed-schema support export and confirmed whole-portable-home deletion pass the CLI lifecycle gate. External source files/exports/backups remain caller-owned. Full filesystem backup, per-record deletion and automatic retention remain planned |
| Installed-package verification | Implemented | Source/wheel install, bundled migrations 001–004, both optional extras, and the complete synthetic pilot pass in a fresh offline environment on local Python 3.13.1 |
| CI interpreter/OS matrix | In progress | Python 3.12/3.13 on Ubuntu 24.04 and macOS 15 configured with pinned actions and isolated synthetic gates; hosted runs have not yet verified those targets |
| Synthetic candidate/job fixtures and adversarial corpus | In progress | One synthetic profile plus adversarial schema, all nine taxonomy-category positives, explicit lexical false-positive controls, secret, percent-encoding, fragmentation, whole-source, padding, mutation, and no-write import cases exist; broader candidate/job corpus remains |

Phase 0 exits only when the canonical commands pass from a clean checkout, real
data is unnecessary, and the truth/privacy invariants have automated coverage.

## Phase 1 — Usable application MVP

**Status: Local pilot implemented; broader product in progress**

| Deliverable | Status | Evidence / limits |
|---|---|---|
| Immutable job text and URL | Implemented | User-supplied text, digest, capture time and source spans; no live fetch/verification |
| Requirement extraction | Implemented for recognized text headings | Exact quotes, classification basis and ambiguity labels; no semantic hiring hypotheses |
| Evidence matrix and gaps | Implemented | Approved-packet retrieval, shared terms, structured unknowns; fit and hiring probability stay unresolved |
| Structured resume, LaTeX, PDF, text and manifest | Implemented | Selected exact approved text, source order/bullet preservation, mappings, current evidence recheck, exact PDF extraction and overflow gates, visual inspection |
| Copy-paste career answers | Implemented | Selected approved text plus mappings; sensitive/unknown questions yield NeedInfo, required missing answers block approval |
| Application tracker and submission snapshot | Implemented | Validated transitions, append-only audited/hash-linked events and immutable exact bundle snapshot; retirement preserves history and blocks future use |
| Human approval and submission boundary | Implemented | Exact material digest approval; applied requires explicit confirmation of manual submission; no external action |

`scripts/check_pilot.py` verifies the full CLI workflow, diagnostics separation,
backup/restore of all pilot state, retirement invalidation, and confirmed deletion.
`scripts/check_package.py --pilot-wheelhouse PATH` repeats it from a fresh offline
wheel installation. `scripts/check_materials.py` refuses optional-provider skips.
See the handoff for exact final counts, interpreter, visual QA and limitations.
The command-line quickstart is in `docs/QUICKSTART.md`.

Richer input formats, semantic tailoring, live job fetching, broader platforms,
ATS compatibility validation, and a graphical interface remain unfinished.

Phase 1 exits when every factual artifact unit maps to approved claim IDs, every
missing sensitive fact becomes `NeedInfo`, supported PDFs pass critical-field
extraction, and the complete workflow runs on synthetic fixtures.

## Phase 2 — Job discovery

**Status: Planned**

- official Greenhouse, Lever, and Ashby adapters;
- generic JSON-LD and compliant company-page ingestion;
- watchlists, source cursors, bounded sync, health, and freshness reports;
- conservative deduplication that preserves source aliases and versions;
- explainable ranking with hard constraints, evidence strength, and uncertainty.

Unauthorized LinkedIn or Indeed scraping/auto-apply code is not a roadmap item.

## Phase 3 — Browser assistance

**Status: Planned**

- visible Playwright sessions and semantic field extraction;
- minimum-data answer packets and a deterministic safe-fill policy;
- resumable browser state without duplicated secrets;
- Greenhouse, Lever, and Ashby application adapters;
- structured final review and mandatory human gates.

The public product target is review-ready assistance. Autonomous final submission,
CAPTCHA/MFA bypass, and broad laptop control are not goals.

## Phase 4 — Communication and learning loop

**Status: Planned**

- proposed application events from user-authorized email;
- reminders and optional calendar integration;
- reusable, evidence-linked behavioral stories;
- local conversion analytics with sample size and uncertainty;
- calibration from user overrides and outcomes;
- connector/plugin interfaces that preserve the same policy boundary.

## Phase 5 — Community ecosystem

**Status: Planned**

- contributor and adapter authoring guides;
- more resume templates and international conventions;
- optional local-model provider and encrypted portable vault;
- internationalization and optional desktop packaging.

## Cross-phase release gates

No phase may regress these requirements:

- zero unsupported factual claims in the golden evaluation corpus;
- full claim traceability for factual resume and questionnaire content;
- missing sensitive information always fails to `NeedInfo`;
- private runtime data remains outside the repository;
- tests and demos use synthetic data only;
- external content remains untrusted and cannot authorize tools;
- no default workflow submits an application;
- validation, state changes, and meaningful side effects remain deterministic,
  typed, testable, and auditable.
